"""Temporal landmark registration and conflict-aware guided landmark growth."""

from dataclasses import replace
from itertools import combinations
from time import perf_counter

import numpy as np
from scipy.spatial import cKDTree

from distance_measuring.scene import Observation, Scene, SceneCamera, ScenePoint, SceneTrack

from .guided import DEFAULT_GUIDED, epipolar_search, project_landmarks, search_landmark_patches
from .pnp import DEFAULT_PNP, estimate_pnp
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

    def solve():
        keys = sorted(candidates)
        result = estimate_pnp(
            np.array([points[t].position for t in keys]).reshape(-1, 3),
            np.array([candidates[t] for t in keys]).reshape(-1, 2),
            camera,
            config=pnp_config,
        )
        return keys, result

    _, initial = solve()
    projection_pose = initial.pose or coarse_pose
    projections = project_landmarks(scene, camera, projection_pose, config=config)
    if confidence is not None:
        projections = [r for r in projections if r["point_id"] in confidence.trusted_point_ids]
    searches, search_report = search_landmark_patches(
        scene, images, image, projections, config=config
    )
    for row in searches:
        if row["accepted"] and (row["track_id"] not in candidates or initial.pose is None):
            candidates[row["track_id"]] = np.asarray(row["second_pixel_refined"])
    keys, result = solve()
    report = {
        "image_id": image_id,
        "temporal_pnp": initial.report,
        "pnp": result.report,
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
    scene, source_id, target_id, first, second, *, reserved_track_id=-1, config=DEFAULT_GUIDED
):
    """Merge corridor matches with existing tracks, then reuse triangulation."""
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
    edges.extend(guided_edges)
    linked = build_tracks(edges)
    pixels = {
        **apixels,
        **bpixels,
        **{(o.image_id, o.feature_id): o.pixel for t in scene.tracks for o in t.observations},
    }
    old_tracks = {t.track_id: t for t in scene.tracks}
    old_points = {p.point_id: p for p in scene.points}
    next_id = max([reserved_track_id, *old_tracks], default=-1) + 1
    tracks, points, details, aliases = [], [], [], {}
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
        confirmed = any(e.first in keys and e.second in keys for e in guided_edges)
        if len(previous) == 1 and not confirmed:
            tracks.append(old_tracks[previous[0]])
            continue
        track_id = previous[0] if previous else next_id
        if not previous:
            next_id += 1
        aliases.update({str(t): track_id for t in previous[1:]})
        result = triangulate_point(
            [pixels[k] for k in group.features],
            [cameras[i].intrinsics for i, _ in group.features],
            [cameras[i].extrinsics for i, _ in group.features],
            max_reprojection_error_px=config.max_reprojection_error_px,
            min_angle_degrees=config.min_triangulation_angle_degrees,
        )
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
        "triangulations": details,
        "runtime_s": perf_counter() - start,
    }


def with_landmark_quality(scene):
    cameras = {c.image_id: c for c in scene.cameras}
    tracks = {t.track_id: t for t in scene.tracks}
    quality, points = {}, []
    for point in scene.points:
        observations = tracks[point.track_id].observations
        rays = np.array(
            [np.asarray(point.position) - cameras[o.image_id].extrinsics.C_w for o in observations]
        )
        rays /= np.linalg.norm(rays, axis=1, keepdims=True)
        angles = [
            float(np.degrees(np.arccos(np.clip(abs(a @ b), 0, 1))))
            for a, b in combinations(rays, 2)
        ]
        errors = [o.reprojection_error_px for o in observations]
        quality[str(point.point_id)] = {
            "observation_count": len(observations),
            "best_triangulation_angle_degrees": max(angles),
            "median_triangulation_angle_degrees": float(np.median(angles)),
            "reprojection_errors_px": errors,
            "reprojection_rmse_px": float(np.sqrt(np.mean(np.square(errors)))),
        }
        points.append(replace(point, triangulation_angle_degrees=max(angles)))
    return replace(
        scene, points=tuple(points), metadata={**scene.metadata, "landmark_quality": quality}
    )
