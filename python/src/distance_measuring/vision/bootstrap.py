"""Select the earliest useful tracked pair and reuse calibrated bootstrap geometry."""

import warnings
from collections import Counter
from dataclasses import asdict, dataclass
from time import perf_counter

import numpy as np
from scipy.spatial.transform import Rotation

from distance_measuring.camera import CameraExtrinsics, CameraIntrinsics, to_homogeneous
from distance_measuring.scene import Observation, Scene, SceneCamera, ScenePoint, SceneTrack

from .parallax import rotation_compensated_parallax
from .pose import (
    EssentialEstimate,
    RelativePose,
    chain_camera,
    estimate_essential,
    recover_relative_pose,
)
from .tracking import TrackingConfig, grid_cell_ids
from .triangulation import triangulate_point


@dataclass(frozen=True)
class BootstrapConfig:
    min_tracks: int = 24
    grid_rows: int = 6
    grid_cols: int = 8
    min_occupied_cells: int = 6
    min_residual_parallax_px: float = 3.0
    min_visual_parallax_px: float = 3.0
    ransac_threshold_px: float = 1.5
    min_pose_inliers: int = 12
    min_essential_inlier_ratio: float = 0.5
    min_pose_parallax_degrees: float = 0.25
    max_reprojection_error_px: float = 3.0
    min_triangulation_angle_degrees: float = 1.0
    min_landmarks: int = 8
    max_landmarks: int = 100
    seed: int = 0

    def __post_init__(self):
        TrackingConfig(grid_rows=self.grid_rows, grid_cols=self.grid_cols)
        for name in ("min_tracks", "min_pose_inliers", "min_landmarks", "max_landmarks"):
            value = getattr(self, name)
            if type(value) is not int or value < 8:
                raise ValueError(f"{name} must be an integer of at least 8")
        if self.max_landmarks < self.min_landmarks:
            raise ValueError("max_landmarks must be at least min_landmarks")
        if (
            type(self.min_occupied_cells) is not int
            or not 1 <= self.min_occupied_cells <= self.grid_rows * self.grid_cols
        ):
            raise ValueError("min_occupied_cells must fit the configured grid")
        for name in (
            "min_residual_parallax_px",
            "min_visual_parallax_px",
            "ransac_threshold_px",
            "max_reprojection_error_px",
            "min_pose_parallax_degrees",
            "min_triangulation_angle_degrees",
        ):
            if not np.isfinite(getattr(self, name)) or getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive and finite")
        if self.min_pose_parallax_degrees >= 90 or self.min_triangulation_angle_degrees >= 90:
            raise ValueError("Parallax/triangulation angles must be below 90 degrees")
        if (
            not np.isfinite(self.min_essential_inlier_ratio)
            or not 0 < self.min_essential_inlier_ratio <= 1
        ):
            raise ValueError("min_essential_inlier_ratio must be finite and in (0, 1]")
        if type(self.seed) is not int or not 0 <= self.seed <= 2**31 - 1:
            raise ValueError("seed must be an integer in [0, 2**31-1]")


DEFAULT_BOOTSTRAP = BootstrapConfig()


@dataclass(frozen=True)
class BootstrapPair:
    image_ids: tuple[int, int]
    track_ids: np.ndarray
    first_pixels: np.ndarray
    second_pixels: np.ndarray
    first_camera: CameraIntrinsics
    second_camera: CameraIntrinsics
    residual_parallax_px: np.ndarray

    def __post_init__(self):
        ids = np.asarray(self.track_ids)
        if ids.ndim != 1:
            raise ValueError("Bootstrap track IDs must be a vector")
        n = len(ids)
        if (
            len(self.image_ids) != 2
            or any(type(i) is not int or i < 0 for i in self.image_ids)
            or self.image_ids[0] >= self.image_ids[1]
        ):
            raise ValueError("Bootstrap pair IDs must follow capture order")
        if ids.shape != (n,) or ids.dtype.kind not in "iu" or np.any(ids < 0) or len(set(ids)) != n:
            raise ValueError("Bootstrap track IDs must be unique nonnegative integers")
        object.__setattr__(self, "track_ids", ids.copy())
        for name, camera in (
            ("first_pixels", self.first_camera),
            ("second_pixels", self.second_camera),
        ):
            points = np.asarray(getattr(self, name), dtype=float)
            if (
                points.shape != (n, 2)
                or not np.isfinite(points).all()
                or (points < 0).any()
                or (points >= camera.image_size).any()
            ):
                raise ValueError(
                    "Bootstrap observations must be aligned finite in-bounds Nx2 pixels"
                )
            if any(camera.distortion):
                raise ValueError("Bootstrap requires already-undistorted pixels and intrinsics")
            object.__setattr__(self, name, points.copy())
        residuals = np.asarray(self.residual_parallax_px, dtype=float)
        if residuals.shape != (n,) or not np.isfinite(residuals).all() or (residuals < 0).any():
            raise ValueError("Bootstrap residual parallax must align with selected P3 tracks")
        object.__setattr__(self, "residual_parallax_px", residuals.copy())


def evaluate_bootstrap_pair(pair: BootstrapPair, config: BootstrapConfig = DEFAULT_BOOTSTRAP):
    """Cheap pre-pose gates; SO(3) ray alignment rejects obvious pure rotation.

    The constrained rotation fit is SciPy's existing Wahba solver. Its median
    pixel residual is a conservative diagnostic, not a translation estimate or
    a substitute for calibrated RANSAC and cheirality.
    """
    start = perf_counter()
    grid = TrackingConfig(grid_rows=config.grid_rows, grid_cols=config.grid_cols)
    cells = [
        grid_cell_ids(points, (camera.image_height, camera.image_width), grid)
        for points, camera in (
            (pair.first_pixels, pair.first_camera),
            (pair.second_pixels, pair.second_camera),
        )
    ]
    coverage = [len(set(ids)) for ids in cells]
    n = len(pair.track_ids)
    median = float(np.median(pair.residual_parallax_px)) if n else None
    reasons = []
    if n < config.min_tracks:
        reasons.append(f"insufficient_tracks: {n} < {config.min_tracks}")
    if min(coverage) < config.min_occupied_cells:
        reasons.append(
            f"insufficient_coverage: source/target={coverage}, "
            f"require {config.min_occupied_cells} each"
        )
    if median is None or median < config.min_residual_parallax_px:
        reasons.append(
            f"insufficient_residual_parallax: median={median}, "
            f"require {config.min_residual_parallax_px} px"
        )
    visual_median, rotation_matrix = None, None
    if not reasons:
        rays = [
            to_homogeneous(camera.normalize_pixels(points))
            for camera, points in (
                (pair.first_camera, pair.first_pixels),
                (pair.second_camera, pair.second_pixels),
            )
        ]
        rays = [a / np.linalg.norm(a, axis=1, keepdims=True) for a in rays]
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", UserWarning)
                rotation, _ = Rotation.align_vectors(rays[1], rays[0])
            rotation_matrix = rotation.as_matrix()
            residual = rotation_compensated_parallax(
                pair.first_pixels,
                pair.second_pixels,
                pair.first_camera,
                pair.second_camera,
                rotation_matrix,
            )
            if not residual.valid.all():
                reasons.append("rotation_only_fit_has_invalid_rays")
            else:
                visual_median = float(np.median(residual.parallax_px))
                if visual_median < config.min_visual_parallax_px:
                    reasons.append(
                        f"rotation_only_motion: median={visual_median:.6f} "
                        f"< {config.min_visual_parallax_px} px"
                    )
        except (ValueError, UserWarning) as error:
            reasons.append(f"rotation_only_fit_degenerate: {error}")
    score = float(min(coverage)) * min(median or 0, 32) * min(n / config.min_tracks, 1)
    return {
        "image_indices": list(pair.image_ids),
        "input_track_count": n,
        "occupied_cells": coverage,
        "median_residual_parallax_px": median,
        "rotation_only_fit_median_px": visual_median,
        "rotation_only_fit_R": rotation_matrix.tolist() if rotation_matrix is not None else None,
        "score": score,
        "preselection_passed": not reasons,
        "reasons": reasons,
        "selection_runtime_s": perf_counter() - start,
    }


def select_bootstrap_pair(pairs, config: BootstrapConfig = DEFAULT_BOOTSTRAP):
    """Return the earliest passing pair and diagnostics, never maximize over time."""
    ordered = sorted(pairs, key=lambda p: (p.image_ids[1], p.image_ids[0]))
    diagnostics = []
    for pair in ordered:
        result = evaluate_bootstrap_pair(pair, config)
        diagnostics.append(result)
        if result["preselection_passed"]:
            return pair, diagnostics
    return None, diagnostics


@dataclass(frozen=True)
class BootstrapPose:
    essential: EssentialEstimate
    relative: RelativePose
    report: dict


def estimate_bootstrap_pose(pair: BootstrapPair, config: BootstrapConfig = DEFAULT_BOOTSTRAP):
    """Reuse calibrated E/RANSAC and SVD/cheirality; no rectification/re-undistortion."""
    start = perf_counter()
    first = pair.first_camera.normalize_pixels(pair.first_pixels)
    second = pair.second_camera.normalize_pixels(pair.second_pixels)
    # Same conservative pixel -> normalized threshold as the existing M5 pipeline.
    sampling = min(
        np.linalg.svd(k.K[:2, :2], compute_uv=False)[-1]
        for k in (pair.first_camera, pair.second_camera)
    )
    threshold = float(config.ransac_threshold_px / sampling)
    estimate_start = perf_counter()
    essential = estimate_essential(first, second, threshold_normalized=threshold, seed=config.seed)
    essential_s = perf_counter() - estimate_start
    recovery_start = perf_counter()
    relative = recover_relative_pose(
        first,
        second,
        essential,
        min_parallax_degrees=config.min_pose_parallax_degrees,
        max_reprojection_error_normalized=float(config.max_reprojection_error_px / sampling),
    )
    recovery_s = perf_counter() - recovery_start
    n, inliers, pose_inliers = len(first), int(essential.inliers.sum()), int(relative.inliers.sum())
    ratio = inliers / n if n else 0.0
    reasons = [relative.failure] if relative.failure else []
    if ratio < config.min_essential_inlier_ratio:
        reasons.append(
            f"insufficient_essential_ratio: {ratio:.6f} < {config.min_essential_inlier_ratio}"
        )
    if pose_inliers < config.min_pose_inliers:
        reasons.append(f"insufficient_pose_inliers: {pose_inliers} < {config.min_pose_inliers}")
    report = {
        "success": relative.R is not None and not reasons,
        "reasons": reasons,
        "input_track_count": n,
        "ransac_inlier_count": inliers,
        "pose_inlier_count": pose_inliers,
        "essential_inlier_ratio": ratio,
        "threshold_normalized": threshold,
        "E": essential.E.tolist() if essential.E is not None else None,
        "R_second_first": relative.R.tolist() if relative.R is not None else None,
        "t_second_first_direction": relative.t_direction.tolist()
        if relative.t_direction is not None
        else None,
        "cheirality_counts": list(relative.cheirality_counts),
        "median_parallax_degrees": relative.median_parallax_degrees,
        "essential_runtime_s": essential_s,
        "recovery_runtime_s": recovery_s,
        "runtime_s": perf_counter() - start,
    }
    return BootstrapPose(essential, relative, report)


@dataclass(frozen=True)
class BootstrapLandmarks:
    scene: Scene
    accepted: np.ndarray
    report: dict


def triangulate_bootstrap(
    pair: BootstrapPair,
    pose: BootstrapPose,
    config: BootstrapConfig = DEFAULT_BOOTSTRAP,
):
    """Triangulate pose inliers only, preserving original temporal IDs and quality."""
    if not pose.report["success"]:
        raise ValueError("Cannot triangulate an unsuccessful bootstrap pose")
    start = perf_counter()
    origin = CameraExtrinsics(np.eye(3), np.zeros(3))
    second = chain_camera(origin, pose.relative, baseline=1.0)
    cameras = (
        SceneCamera(pair.image_ids[0], pair.first_camera, origin),
        SceneCamera(pair.image_ids[1], pair.second_camera, second),
    )
    points, tracks, details = [], [], []
    accepted = np.zeros(len(pair.track_ids), bool)
    attempted = 0
    for row, track_id in enumerate(pair.track_ids):
        track_id = int(track_id)
        result, reason = None, None
        if not pose.essential.inliers[row]:
            reason = "essential_outlier"
        elif not pose.relative.inliers[row]:
            reason = "pose_rejected"
        elif len(points) >= config.max_landmarks:
            reason = "landmark_budget"
        else:
            attempted += 1
            result = triangulate_point(
                [pair.first_pixels[row], pair.second_pixels[row]],
                [pair.first_camera, pair.second_camera],
                [origin, second],
                max_reprojection_error_px=config.max_reprojection_error_px,
                min_angle_degrees=config.min_triangulation_angle_degrees,
            )
            reason = result.rejection_reason
        errors = result.reprojection_errors_px if result is not None else ()
        observations = tuple(
            Observation(image_id, track_id, tuple(pixel), errors[k] if errors else None)
            for k, (image_id, pixel) in enumerate(
                zip(pair.image_ids, (pair.first_pixels[row], pair.second_pixels[row]), strict=True)
            )
        )
        valid = result is not None and result.position is not None
        accepted[row] = valid
        tracks.append(SceneTrack(track_id, observations, track_id if valid else None, reason))
        if valid:
            points.append(ScenePoint(track_id, track_id, result.position, result.angle_degrees))
        details.append(
            {
                "track_id": track_id,
                "accepted": valid,
                "rejection_reason": reason,
                "position": list(result.position) if valid else None,
                "reprojection_errors_px": list(errors) if errors else None,
                "triangulation_angle_degrees": result.angle_degrees if result is not None else None,
                "depths_in_baseline_units": [
                    float(c.extrinsics.world_to_camera(result.position)[2]) for c in cameras
                ]
                if valid
                else None,
            }
        )
    success = len(points) >= config.min_landmarks
    errors = [
        error for row in details if row["accepted"] for error in row["reprojection_errors_px"]
    ]
    report = {
        "success": success,
        "point_count": len(points),
        "triangulation_attempt_count": attempted,
        "rejection_counts": dict(
            Counter(d["rejection_reason"] for d in details if d["rejection_reason"])
        ),
        "reprojection_rmse_px": float(np.sqrt(np.mean(np.square(errors)))) if errors else None,
        "positive_depth_required": True,
        "landmarks": details,
        "reasons": []
        if success
        else [f"insufficient_landmarks: {len(points)} < {config.min_landmarks}"],
        "runtime_s": perf_counter() - start,
    }
    scene = Scene(
        cameras,
        tuple(tracks),
        tuple(points),
        {
            "bootstrap_pair": list(pair.image_ids),
            "feature_id_source": "P2_temporal_track_id",
            "scale_status": "arbitrary_visual_scale_unverified",
            "quality_status": "two_view_bootstrap_unverified"
            if success
            else "bootstrap_insufficient_landmarks",
            "gauge": {"fixed_camera_id": pair.image_ids[0], "first_baseline_length": 1.0},
            "bootstrap_settings": asdict(config),
        },
    )
    return BootstrapLandmarks(scene, accepted, report)
