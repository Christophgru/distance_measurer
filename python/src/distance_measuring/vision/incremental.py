"""Temporal landmark registration and conflict-aware guided landmark growth."""

from dataclasses import replace
from time import perf_counter

import numpy as np
from scipy.spatial import cKDTree

from distance_measuring.scene import Observation, Scene, SceneCamera, ScenePoint, SceneTrack

from .bootstrap import DEFAULT_BOOTSTRAP
from .guided import (
    DEFAULT_GUIDED,
    epipolar_search,
    project_landmarks,
    search_landmark_patches,
)
from .pnp import DEFAULT_PNP, estimate_pnp
from .pose_prior import tracked_motion_prior
from .tracks import MatchEdge, build_tracks
from .triangulation import triangulate_point


def register_camera(
    scene,
    image_id,
    camera,
    image,
    images,
    temporal_pixels,
    coarse_pose,
    *,
    config=DEFAULT_GUIDED,
    pnp_config=DEFAULT_PNP,
    confidence_config=None,
    motion_pair=None,
    motion_config=DEFAULT_BOOTSTRAP,
):
    """Try temporal PnP, then bounded image evidence and a verified PnP fit.

    temporal_pixels maps original track IDs to accepted P2 observations. A failed
    registration returns the original scene and never creates a placeholder pose.
    """
    start = perf_counter()
    if image_id in {c.image_id for c in scene.cameras}:
        raise ValueError("Camera is already registered")
    points = {p.track_id: p for p in scene.points}
    confidence = None
    if confidence_config is not None:
        from .landmark_confidence import score_landmarks

        confidence = score_landmarks(scene, config=confidence_config)
        points = {t: p for t, p in points.items() if p.point_id in confidence.trusted_point_ids}
    candidates = {
        t: np.asarray(pixel, float) for t, pixel in temporal_pixels.items() if t in points
    }
    pnp_attempts = []

    def solve():
        keys = sorted(candidates)
        result = estimate_pnp(
            np.array([points[t].position for t in keys]).reshape(-1, 3),
            np.array([candidates[t] for t in keys]).reshape(-1, 2),
            camera,
            config=pnp_config,
        )
        pnp_attempts.append(result.report)
        return keys, result

    initial_keys, initial = solve()
    temporal_inliers = set()
    if initial.pose is not None:
        temporal_inliers = {
            t for t, keep in zip(initial_keys, initial.inliers, strict=True) if keep
        }
    projection_pose = initial.pose or coarse_pose
    projections = project_landmarks(scene, camera, projection_pose, config=config)
    if confidence is not None:
        projections = [r for r in projections if r["point_id"] in confidence.trusted_point_ids]
    searches, search_report = search_landmark_patches(
        scene,
        images,
        image,
        [r for r in projections if r["track_id"] not in temporal_inliers],
        config=config,
    )
    search_report["skipped_temporal_inlier_count"] = len(temporal_inliers)
    for row in searches:
        if row["accepted"] and (row["track_id"] not in candidates or initial.pose is None):
            candidates[row["track_id"]] = np.asarray(row["second_pixel_refined"])
    keys, result = solve()
    prior_report = None
    if result.pose is None and motion_pair is not None and config.use_two_view_prior:
        prior, prior_report = tracked_motion_prior(
            scene,
            motion_pair,
            trusted_point_ids={p.point_id for p in points.values()},
            min_landmarks=config.min_prior_landmarks,
            max_error_px=config.search_radius_px,
            bootstrap_config=motion_config,
        )
        if prior is not None:
            warped = [
                r
                for r in project_landmarks(
                    scene, camera, prior, normalize_patches=True, config=config
                )
                if r["track_id"] in points
            ]
            matches, matching = search_landmark_patches(scene, images, image, warped, config=config)
            for row in matches:
                if row["accepted"]:
                    candidates[row["track_id"]] = np.asarray(row["second_pixel_refined"])
            keys, result = solve()
            prior_report.update(
                projections=warped, patch_matches=matches, local_search=matching, pnp=result.report
            )
    report = {
        "image_id": image_id,
        "temporal_pnp": initial.report,
        "pnp": result.report,
        "pnp_attempts": pnp_attempts,
        "pnp_runtime_s": sum(r["runtime_s"] for r in pnp_attempts),
        "projection_pose_source": "temporal_pnp" if initial.pose else "coarse_prediction",
        "projections": projections,
        "local_search": search_report,
        "patch_matches": searches,
        "correspondences": [
            {
                "track_id": t,
                "point_id": points[t].point_id,
                "pixel": candidates[t].tolist(),
                "inlier": bool(result.inliers[k]),
                "reprojection_error_px": float(result.errors_px[k])
                if np.isfinite(result.errors_px[k])
                else None,
            }
            for k, t in enumerate(keys)
        ],
        "success": result.pose is not None,
        "two_view_prior": prior_report,
        "runtime_s": perf_counter() - start,
    }
    if confidence is not None:
        report["confidence"] = confidence.report
    if result.pose is None:
        return scene, report
    observations = {
        t: Observation(image_id, t, tuple(candidates[t]), float(result.errors_px[k]))
        for k, t in enumerate(keys)
        if result.inliers[k]
    }
    tracks = tuple(
        replace(t, observations=t.observations + (observations[t.track_id],))
        if t.track_id in observations
        else t
        for t in scene.tracks
    )
    return Scene(
        (*scene.cameras, SceneCamera(image_id, camera, result.pose)),
        tracks,
        scene.points,
        {**scene.metadata, "quality_status": "incremental_visual_geometry_unverified"},
    ), report


def _feature_keys(features, image_id, scene, offset, radius):
    observations = [o for t in scene.tracks for o in t.observations if o.image_id == image_id]
    old_tree = cKDTree([o.pixel for o in observations]) if observations else None
    tree = cKDTree(features.points) if len(features) else None
    keys, pixels = [], {}
    for pixel in features.points:
        distance, index = old_tree.query(pixel) if old_tree else (np.inf, None)
        if distance <= radius:
            observation = observations[index]
            key, pixel = (image_id, observation.feature_id), observation.pixel
        else:
            representative = min(tree.query_ball_point(pixel, radius))
            key, pixel = (image_id, offset + representative), features.points[representative]
        keys.append(key)
        pixels[key] = tuple(pixel)
    return keys, pixels


def grow_landmarks(
    scene,
    source_id,
    target_id,
    first,
    second,
    *,
    reserved_track_id=-1,
    temporal_pixels=None,
    config=DEFAULT_GUIDED,
):
    """Verify shared temporal tracks and corridor matches with triangulation.

    Temporal IDs use P2's reserved namespace. Existing landmark observations are
    committed only by registration; an old rejected track needs a new view.
    """
    start = perf_counter()
    cameras = {c.image_id: c for c in scene.cameras}
    if source_id == target_id or source_id not in cameras or target_id not in cameras:
        raise ValueError("Guided growth requires two distinct registered cameras")
    offset = (
        max(
            [reserved_track_id, *(o.feature_id for t in scene.tracks for o in t.observations)],
            default=-1,
        )
        + 1
    )
    akeys, apixels = _feature_keys(first, source_id, scene, offset, config.merge_radius_px)
    bkeys, bpixels = _feature_keys(
        second, target_id, scene, offset + len(first), config.merge_radius_px
    )
    old_by_key = {(o.image_id, o.feature_id): t for t in scene.tracks for o in t.observations}
    old_pixels = {(o.image_id, o.feature_id): o.pixel for t in scene.tracks for o in t.observations}
    old_tracks = {t.track_id: t for t in scene.tracks}
    occupied = {key for key, track in old_by_key.items() if track.point_id is not None}
    queries, seen = [], set()
    for i, key in enumerate(akeys):
        if key not in occupied and key not in seen:
            queries.append(i)
            seen.add(key)
    # Already-associated target landmarks cannot become new landmark edges.
    matches, search = epipolar_search(
        first, second, cameras[source_id], cameras[target_id], query_indices=queries, config=config
    )
    edges = [
        MatchEdge(
            (t.observations[0].image_id, t.observations[0].feature_id), (o.image_id, o.feature_id)
        )
        for t in scene.tracks
        for o in t.observations[1:]
    ]
    guided_edges = [
        MatchEdge(akeys[i], bkeys[j], 1 + ratio)
        for i, j, ratio in matches
        if bkeys[j] not in occupied
    ]
    temporal_edges, temporal_coordinates, temporal_track_ids = [], {}, []
    if temporal_pixels is not None:
        shared = sorted(
            t for i, t in temporal_pixels if i == source_id and (target_id, t) in temporal_pixels
        )
        for track_id in shared:
            if type(track_id) is not int or not 0 <= track_id <= reserved_track_id:
                raise ValueError("Temporal track IDs must lie in the reserved P2 namespace")
            keys = ((source_id, track_id), (target_id, track_id))
            previous_track = old_tracks.get(track_id)
            if previous_track is not None and previous_track.point_id is not None:
                continue
            for key in keys:
                pixel = np.asarray(temporal_pixels[key], float)
                if (
                    pixel.shape != (2,)
                    or not np.isfinite(pixel).all()
                    or (pixel < 0).any()
                    or (pixel >= cameras[key[0]].intrinsics.image_size).any()
                ):
                    raise ValueError("Temporal growth requires finite in-bounds pixels")
                if key in old_pixels and not np.array_equal(pixel, old_pixels[key]):
                    raise ValueError("Temporal growth pixels disagree with existing observations")
            if any(k in occupied for k in keys) or all(k in old_by_key for k in keys):
                continue
            temporal_edges.append(MatchEdge(*keys))
            temporal_track_ids.append(track_id)
            if previous_track is not None and keys[0] not in old_by_key:
                anchor = previous_track.observations[0]
                temporal_edges.append(MatchEdge((anchor.image_id, anchor.feature_id), keys[0]))
            temporal_coordinates.update({k: tuple(temporal_pixels[k]) for k in keys})
    confirmed_edges = [*guided_edges, *temporal_edges]
    edges.extend(confirmed_edges)
    merge_start = perf_counter()
    linked = build_tracks(edges)
    merging_s = perf_counter() - merge_start
    pixels = {
        **apixels,
        **bpixels,
        **temporal_coordinates,
        **old_pixels,
    }
    old_points = {p.point_id: p for p in scene.points}
    next_id = max([reserved_track_id, *old_tracks], default=-1) + 1
    tracks, points, details, aliases = [], [], [], {}
    triangulation_s = 0.0
    for group in linked.tracks:
        previous = sorted({old_by_key[k].track_id for k in group.features if k in old_by_key})
        established = [old_tracks[t] for t in previous if old_tracks[t].point_id is not None]
        if established:
            # Guided queries exclude landmarks; target collisions are filtered.
            if len(established) != 1 or len(previous) != 1:
                raise ValueError("Guided merging attempted to combine established landmarks")
            tracks.append(established[0])
            points.append(old_points[established[0].point_id])
            continue
        keys = set(group.features)
        confirmed = any(e.first in keys and e.second in keys for e in confirmed_edges)
        if len(previous) == 1 and not confirmed:
            tracks.append(old_tracks[previous[0]])
            continue
        temporal_ids = [t for i, t in group.features if (i, t) in temporal_coordinates]
        track_id = previous[0] if previous else min(temporal_ids) if temporal_ids else next_id
        if not previous and not temporal_ids:
            next_id += 1
        aliases.update({str(t): track_id for t in previous[1:]})
        triangulation_start = perf_counter()
        result = triangulate_point(
            [pixels[k] for k in group.features],
            [cameras[i].intrinsics for i, _ in group.features],
            [cameras[i].extrinsics for i, _ in group.features],
            max_reprojection_error_px=config.max_reprojection_error_px,
            min_angle_degrees=config.min_triangulation_angle_degrees,
        )
        triangulation_s += perf_counter() - triangulation_start
        valid = result.position is not None
        observations = tuple(
            Observation(
                i,
                k,
                pixels[i, k],
                result.reprojection_errors_px[n] if result.reprojection_errors_px else None,
            )
            for n, (i, k) in enumerate(group.features)
        )
        tracks.append(
            SceneTrack(track_id, observations, track_id if valid else None, result.rejection_reason)
        )
        if valid:
            points.append(ScenePoint(track_id, track_id, result.position, result.angle_degrees))
        details.append(
            {
                "track_id": track_id,
                "accepted": valid,
                "reason": result.rejection_reason,
                "observation_count": len(observations),
                "reprojection_errors_px": list(result.reprojection_errors_px),
                "best_triangulation_angle_degrees": result.angle_degrees,
            }
        )
    grown = Scene(
        scene.cameras,
        tuple(sorted(tracks, key=lambda t: t.track_id)),
        tuple(sorted(points, key=lambda p: p.point_id)),
        {
            **scene.metadata,
            "feature_id_source": "temporal_and_guided_feature_ids",
            "guided_track_aliases": {**scene.metadata.get("guided_track_aliases", {}), **aliases},
        },
    )
    grown = with_landmark_quality(grown)
    return grown, {
        "source_image_id": source_id,
        "target_image_id": target_id,
        "epipolar_search": search,
        "track_conflict_count": len(linked.conflicts),
        "track_conflicts": [
            {"first": list(c.edge.first), "second": list(c.edge.second), "reason": c.reason}
            for c in linked.conflicts
        ],
        "new_landmark_count": len(grown.points) - len(scene.points),
        "temporal_candidate_count": len(temporal_track_ids),
        "triangulations": details,
        "track_merging_runtime_s": merging_s,
        "triangulation_runtime_s": triangulation_s,
        "runtime_s": perf_counter() - start,
    }


def landmark_quality(point, observations, cameras, *, errors=None):
    """Measure current ray geometry, sharing the calculation with confidence scoring."""
    rays = np.array(
        [np.asarray(point.position) - cameras[o.image_id].extrinsics.C_w for o in observations]
    )
    rays /= np.linalg.norm(rays, axis=1, keepdims=True)
    first, second = np.triu_indices(len(rays), 1)
    dots = np.sum(rays[first] * rays[second], axis=1)
    angles = np.degrees(np.arccos(np.clip(np.abs(dots), 0, 1)))
    errors = [o.reprojection_error_px for o in observations] if errors is None else errors
    return {
        "observation_count": len(observations),
        "best_triangulation_angle_degrees": float(np.max(angles)),
        "median_triangulation_angle_degrees": float(np.median(angles)),
        "reprojection_errors_px": errors,
        "reprojection_rmse_px": float(np.sqrt(np.mean(np.square(errors)))),
    }


def with_landmark_quality(scene):
    cameras = {c.image_id: c for c in scene.cameras}
    tracks = {t.track_id: t for t in scene.tracks}
    quality, points = {}, []
    for point in scene.points:
        measured = landmark_quality(point, tracks[point.track_id].observations, cameras)
        quality[str(point.point_id)] = measured
        points.append(
            replace(point, triangulation_angle_degrees=measured["best_triangulation_angle_degrees"])
        )
    return replace(
        scene, points=tuple(points), metadata={**scene.metadata, "landmark_quality": quality}
    )
