"""Linear triangulation: lecture 002 pp. 65–68, 82–83, 98/101, 132–136.

The explicit DLT solver and quality thresholds extend p. 136's approximate
intersection of calibrated optical rays. Page numbers are PPTX slide order.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from itertools import combinations

import numpy as np
from numpy.typing import ArrayLike

from distance_measuring.camera import CameraExtrinsics, CameraIntrinsics


@dataclass(frozen=True)
class TriangulationResult:
    position: tuple[float, float, float] | None
    reprojection_errors_px: tuple[float, ...] = ()
    angle_degrees: float | None = None
    rejection_reason: str | None = None


def triangulate_point(
    pixels: ArrayLike,
    intrinsics: Sequence[CameraIntrinsics],
    poses: Sequence[CameraExtrinsics],
    *,
    max_reprojection_error_px: float = 3.0,
    min_angle_degrees: float = 1.0,
    solver: str = "svd",
) -> TriangulationResult:
    """Fit one point from >=2 ideal pixel observations, using every supplied view."""
    pixels = np.asarray(pixels, dtype=float)
    if pixels.ndim != 2 or pixels.shape[1] != 2 or not np.isfinite(pixels).all():
        raise ValueError("pixels must be a finite Nx2 array")
    if len(pixels) < 2 or len(intrinsics) != len(pixels) or len(poses) != len(pixels):
        raise ValueError("Triangulation requires at least two aligned pixels, intrinsics and poses")
    if not np.isfinite(max_reprojection_error_px) or max_reprojection_error_px <= 0:
        raise ValueError("max_reprojection_error_px must be positive and finite")
    if not np.isfinite(min_angle_degrees) or not 0 < min_angle_degrees < 90:
        raise ValueError("min_angle_degrees must be finite and in (0, 90)")
    if solver not in ("svd", "eigh"):
        raise ValueError("solver must be svd or eigh")
    if any(any(camera.distortion) for camera in intrinsics):
        raise ValueError("Triangulation requires already-undistorted pixels and distortion-free K")
    centers = np.array([pose.C_w for pose in poses])
    # Work near the first camera with a baseline-sized unit. This similarity
    # improves conditioning without changing the lecture's world/camera model.
    origin = centers[0]
    unit = max(float(np.linalg.norm(a - b)) for a, b in combinations(centers, 2))
    if unit == 0:
        return TriangulationResult(None, rejection_reason="coincident_cameras")
    rows = []
    for pixel, camera, pose in zip(pixels, intrinsics, poses, strict=True):
        x, y = camera.normalize_pixels(pixel)
        # Lecture 002 pp. 70–76, 82–83: K^-1 m ~ [R|O] P_h, O=-R C.
        # In conditioned world coordinates, P_world=origin+unit*P_conditioned.
        M_ext = np.c_[pose.R_cw, pose.R_cw @ ((origin - pose.C_w) / unit)]
        # pp. 65–68 and 133–136: eliminate homogeneous depth from each 3x4
        # projection, giving (x*M3-M1)P_h=0 and (y*M3-M2)P_h=0.
        rows.extend((x * M_ext[2] - M_ext[0], y * M_ext[2] - M_ext[1]))
    A = np.array(rows)
    if np.linalg.matrix_rank(A) < 3:
        return TriangulationResult(None, rejection_reason="degenerate_rays")
    if solver == "eigh":
        # Lecture 002 pp. 98/101's minimum eigenvector method, reused for the
        # four unknown coordinates of P_h rather than twelve entries of camera M.
        _, vectors = np.linalg.eigh(A.T @ A)
        homogeneous = vectors[:, 0]
    else:
        # SVD solves the same unit-norm homogeneous least-squares problem without
        # squaring A's condition number. It is the default numerical solver.
        _, _, Vt = np.linalg.svd(A)
        homogeneous = Vt[-1]
    if abs(homogeneous[3]) <= 1e-12:
        return TriangulationResult(None, rejection_reason="point_at_infinity")
    point = origin + unit * homogeneous[:3] / homogeneous[3]
    if not np.isfinite(point).all():
        return TriangulationResult(None, rejection_reason="nonfinite_point")
    # Lecture 002 pp. 82–83, 133–136: physically visible points lie forward of
    # every camera. Depth is camera Z, not the sign of an arbitrary scaled M.
    if any(pose.world_to_camera(point)[2] <= 0 for pose in poses):
        return TriangulationResult(None, rejection_reason="behind_camera")
    errors = tuple(
        float(np.linalg.norm(camera.project(point, pose, distort=False) - pixel))
        for camera, pose, pixel in zip(intrinsics, poses, pixels, strict=True)
    )
    directions = point - centers
    directions /= np.linalg.norm(directions, axis=1, keepdims=True)
    # pp. 133–136's ray intersection is ill-conditioned for nearly parallel OR
    # antiparallel lines. Use the best acute angle among all view pairs.
    angle = max(
        float(np.degrees(np.arccos(np.clip(abs(np.dot(a, b)), 0, 1))))
        for a, b in combinations(directions, 2)
    )
    if max(errors) > max_reprojection_error_px:
        return TriangulationResult(None, errors, angle, "high_reprojection_error")
    if angle < min_angle_degrees:
        return TriangulationResult(None, errors, angle, "poor_triangulation_angle")
    return TriangulationResult(tuple(float(v) for v in point), errors, angle)
