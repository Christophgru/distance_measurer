"""Visual-only camera validation; lecture 002 pp. 81–83, 133–138.

Hold-out splitting and robust observation pruning extend the lecture. Neither
known ruler lengths nor inertial measurements are camera-fitting inputs.
"""

import copy
from dataclasses import replace
from itertools import combinations

import numpy as np

from .tracks import MatchEdge, build_tracks
from .triangulation import triangulate_point


def split_camera_matches(audit, every=5):
    """Reserve complete physical-location tracks before any camera pose fit."""
    if type(every) is not int or every < 2:
        raise ValueError("Hold-out stride must be an integer >=2")
    ids = audit["image_ids"]
    edges, pixels = [], {}
    for row in audit["pairwise_matches"]:
        if not row["usable_pairwise"]:
            continue
        keys = [(i, k) for i, k in zip(ids, row["group_ids"], strict=True) if k is not None]
        if len(keys) != 2:
            raise ValueError("Pairwise records require exactly two observations")
        for i, k in keys:
            xy = row["working_pixels"][ids.index(i)]
            if (i, k) in pixels and pixels[i, k] != xy:
                raise ValueError("A feature group has inconsistent pixel coordinates")
            pixels[i, k] = xy
        edges.append(MatchEdge(*keys, 1 - row["correlation"]["score"]))
    tracks = build_tracks(edges)
    reserved = [t for t in tracks.tracks if t.track_id % every == 0]
    reserved_keys = {key for t in reserved for key in t.features}
    training = copy.deepcopy(audit)
    for row in training["pairwise_matches"]:
        if any(
            (i, k) in reserved_keys
            for i, k in zip(ids, row["group_ids"], strict=True)
            if k is not None
        ):
            row["usable_pairwise"] = False
    records = [
        {
            "source_track_id": t.track_id,
            "observations": [
                {"image_id": i, "feature_id": k, "pixel": pixels[i, k]} for i, k in t.features
            ],
        }
        for t in reserved
    ]
    return training, records


def evaluate_camera_holdout(scene, records):
    cameras = {c.image_id: c for c in scene.cameras}
    rows, errors = [], []
    accepted_by_camera = {i: 0 for i in cameras}
    for row in records:
        observations = row["observations"]
        result = triangulate_point(
            [o["pixel"] for o in observations],
            [cameras[o["image_id"]].intrinsics for o in observations],
            [cameras[o["image_id"]].extrinsics for o in observations],
        )
        accepted = result.position is not None
        errors.extend(result.reprojection_errors_px)
        if accepted:
            for o in observations:
                accepted_by_camera[o["image_id"]] += 1
        rows.append(
            {
                **row,
                "accepted": accepted,
                "rejection_reason": result.rejection_reason,
                "reprojection_errors_px": list(result.reprojection_errors_px),
                "triangulation_angle_degrees": result.angle_degrees,
            }
        )
    count = sum(r["accepted"] for r in rows)
    return {
        "track_count": len(rows),
        "accepted_count": count,
        "accepted_fraction": count / len(rows) if rows else 0.0,
        "accepted_observations_by_camera": accepted_by_camera,
        "all_finite_error_p95_px": float(np.percentile(errors, 95)) if errors else None,
        "all_finite_error_max_px": max(errors) if errors else None,
        "tracks": rows,
    }


def prune_visual_outliers(scene, threshold=3.0):
    """Record and remove high-error TRAINING observations; never drop a camera."""
    cameras = {c.image_id: c for c in scene.cameras}
    points = {p.point_id: p for p in scene.points}
    tracks, new_points, removed = [], [], []
    for track in scene.tracks:
        if track.point_id is None:
            tracks.append(track)
            continue
        keep = tuple(o for o in track.observations if o.reprojection_error_px <= threshold)
        removed.extend(
            {
                "track_id": track.track_id,
                "image_id": o.image_id,
                "error_px": o.reprojection_error_px,
                "reason": "reprojection_error_above_threshold",
            }
            for o in track.observations
            if o not in keep
        )
        if keep == track.observations:
            tracks.append(track)
            new_points.append(points[track.point_id])
            continue
        if len(keep) < 2:
            removed.extend(
                {
                    "track_id": track.track_id,
                    "image_id": o.image_id,
                    "error_px": o.reprojection_error_px,
                    "reason": "insufficient_remaining_views",
                }
                for o in keep
            )
            tracks.append(replace(track, point_id=None, rejection_reason="post_ba_outliers"))
            continue
        result = triangulate_point(
            [o.pixel for o in keep],
            [cameras[o.image_id].intrinsics for o in keep],
            [cameras[o.image_id].extrinsics for o in keep],
            max_reprojection_error_px=threshold,
        )
        if result.position is None:
            removed.extend(
                {
                    "track_id": track.track_id,
                    "image_id": o.image_id,
                    "error_px": o.reprojection_error_px,
                    "reason": "retriangulation_" + result.rejection_reason,
                }
                for o in keep
            )
            tracks.append(
                replace(
                    track,
                    observations=keep,
                    point_id=None,
                    rejection_reason=result.rejection_reason,
                )
            )
            continue
        obs = tuple(
            replace(o, reprojection_error_px=e)
            for o, e in zip(keep, result.reprojection_errors_px, strict=True)
        )
        tracks.append(replace(track, observations=obs))
        new_points.append(
            replace(
                points[track.point_id],
                position=result.position,
                triangulation_angle_degrees=result.angle_degrees,
            )
        )
    return replace(scene, tracks=tuple(tracks), points=tuple(new_points)), removed


def camera_fit_quality(scene, holdout, *, converged, removed_fraction):
    errors, by_camera = [], {c.image_id: [] for c in scene.cameras}
    for t in scene.tracks:
        if t.point_id is not None:
            for o in t.observations:
                errors.append(o.reprojection_error_px)
                by_camera[o.image_id].append(o.pixel)
    thresholds = {
        "training_rmse_px": 1.0,
        "training_p95_px": 2.0,
        "training_max_px": 3.0,
        "min_training_observations_per_camera": 20,
        "min_held_out_observations_per_camera": 8,
        "min_held_out_acceptance_fraction": 0.9,
        "max_removed_training_fraction": 0.1,
    }
    rmse = float(np.sqrt(np.mean(np.square(errors)))) if errors else None
    p95 = float(np.percentile(errors, 95)) if errors else None
    passed = bool(
        errors
        and converged
        and rmse <= 1
        and p95 <= 2
        and max(errors) <= 3
        and all(len(p) >= 20 for p in by_camera.values())
        and removed_fraction <= 0.1
        and holdout["accepted_fraction"] >= 0.9
        and all(n >= 8 for n in holdout["accepted_observations_by_camera"].values())
    )
    centers = {c.image_id: c.extrinsics.C_w for c in scene.cameras}
    return {
        "passed": passed,
        "thresholds": thresholds,
        "converged": converged,
        "training_rmse_px": rmse,
        "training_p95_px": p95,
        "training_max_px": max(errors) if errors else None,
        "training_observations_by_camera": {i: len(p) for i, p in by_camera.items()},
        "removed_training_fraction": removed_fraction,
        "camera_baselines_arbitrary_units": [
            {"image_ids": [i, j], "distance": float(np.linalg.norm(centers[i] - centers[j]))}
            for i, j in combinations(centers, 2)
        ],
        "imu_used_for_camera_fit": False,
        "ruler_used_for_camera_fit": False,
        "absolute_camera_position_accuracy_certified": False,
    }
