"""Recover a search pose from tracked two-view motion and existing visual scale."""

from dataclasses import asdict
from time import perf_counter

import numpy as np

from distance_measuring.camera import CameraExtrinsics, to_homogeneous

from .bootstrap import DEFAULT_BOOTSTRAP, estimate_bootstrap_pose


def tracked_motion_prior(
    scene,
    pair,
    *,
    trusted_point_ids=None,
    min_landmarks=3,
    max_error_px=32,
    bootstrap_config=DEFAULT_BOOTSTRAP,
):
    """Fit one relative translation scale; this never registers a camera.

    Reuse calibrated RANSAC/cheirality for R and unit t. For an existing point X
    and target ray y, y cross (R X_source + s t) = 0 is linear in visual scale s.
    At least three trusted anchors must predict within the bounded search radius.
    Registration still requires the ordinary independent PnP acceptance gates.
    """
    start = perf_counter()
    if type(min_landmarks) is not int or min_landmarks < 3:
        raise ValueError("A tracked motion prior needs at least three scale anchors")
    if not np.isfinite(max_error_px) or max_error_px <= 0:
        raise ValueError("The motion-prior search radius must be positive and finite")
    source = next((c for c in scene.cameras if c.image_id == pair.image_ids[0]), None)
    if source is None:
        raise ValueError("The motion-prior source camera must be registered")
    report = {
        "available": False,
        "reason": None,
        "anchor_count": 0,
        "relative_pose": None,
        "settings": {
            "min_landmarks": min_landmarks,
            "max_error_px": max_error_px,
            "two_view": asdict(bootstrap_config),
        },
    }

    def finish(pose, reason):
        report.update(available=pose is not None, reason=reason, runtime_s=perf_counter() - start)
        return pose, report

    if len(pair.track_ids) < bootstrap_config.min_pose_inliers:
        return finish(None, "insufficient_two_view_tracks")
    try:
        estimate = estimate_bootstrap_pose(pair, bootstrap_config)
    except ValueError as error:
        return finish(None, f"two_view_pose_unavailable: {error}")
    report["relative_pose"] = estimate.report
    if not estimate.report["success"]:
        return finish(None, "two_view_pose_rejected")
    points = {
        p.track_id: p
        for p in scene.points
        if trusted_point_ids is None or p.point_id in trusted_point_ids
    }
    rows = [k for k, t in enumerate(pair.track_ids) if t in points and estimate.relative.inliers[k]]
    report["anchor_count"] = len(rows)
    if len(rows) < min_landmarks:
        return finish(None, "insufficient_trusted_scale_anchors")
    world = np.array([points[int(pair.track_ids[k])].position for k in rows])
    rays = to_homogeneous(pair.second_camera.normalize_pixels(pair.second_pixels[rows]))
    source_points = source.extrinsics.world_to_camera(world)
    relative = estimate.relative
    A = np.cross(rays, relative.t_direction)
    b = -np.cross(rays, source_points @ relative.R.T)
    denominator = np.sum(A * A, axis=1)
    valid = (source_points[:, 2] > 0) & (denominator > 1e-12)
    if valid.sum() < min_landmarks:
        return finish(None, "unobservable_translation_scale")
    scale = float(np.sum(A[valid] * b[valid]) / np.sum(A[valid] ** 2))
    if not np.isfinite(scale) or scale <= 0:
        return finish(None, "nonpositive_translation_scale")
    rotation = relative.R @ source.extrinsics.R_cw
    pose = CameraExtrinsics(
        rotation, source.extrinsics.C_w - rotation.T @ relative.t_direction * scale
    )
    depths = pose.world_to_camera(world)[:, 2]
    errors = np.full(len(world), np.inf)
    forward = depths > 0
    errors[forward] = np.linalg.norm(
        pair.second_camera.project(world[forward], pose, distort=False)
        - pair.second_pixels[np.array(rows)[forward]],
        axis=1,
    )
    support = valid & (errors <= max_error_px)
    report.update(
        scale_in_reconstruction_units=scale,
        anchor_track_ids=[int(pair.track_ids[k]) for k in rows],
        anchor_errors_px=[float(e) if np.isfinite(e) else None for e in errors],
        supported_anchor_count=int(support.sum()),
        max_anchor_error_px=max_error_px,
    )
    if support.sum() < min_landmarks:
        return finish(None, "inconsistent_translation_scale_anchors")
    report.update(R_cw=pose.R_cw.tolist(), C_w=pose.C_w.tolist())
    return finish(pose, None)
