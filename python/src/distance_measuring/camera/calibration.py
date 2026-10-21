"""Lecture DLT/eigenvector/RQ calibration and its multi-view planar extension.

See docs/math/camera_calibration.md. Lecture slides 95–101 derive the homogeneous
least-squares system, including the coplanar degeneracy; slides 90–91 use RQ.
Zhang's planar constraints extend the same eigenvector method to checkerboards.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from numpy.typing import ArrayLike, NDArray
from scipy.linalg import rq
from scipy.optimize import least_squares
from scipy.spatial.transform import Rotation

from .geometry import (
    CameraExtrinsics,
    from_homogeneous,
    points_array,
    to_homogeneous,
)
from .intrinsics import CameraIntrinsics, save_intrinsics


def _correspondences(values: ArrayLike, dimension: int, name: str) -> NDArray[np.float64]:
    values = points_array(values, dimension, name)
    if values.ndim != 2:
        raise ValueError(f"{name} must be an Nx{dimension} array")
    return values


def _normalize(points: NDArray[np.float64]) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    center = points.mean(axis=0)
    distances = np.linalg.norm(points - center, axis=1)
    if distances.mean() <= np.finfo(float).eps:
        raise ValueError("Calibration points have no spatial extent")
    scale = np.sqrt(points.shape[1]) / distances.mean()
    T = np.eye(points.shape[1] + 1)
    T[:-1, :-1] *= scale
    T[:-1, -1] = -scale * center
    return from_homogeneous(to_homogeneous(points) @ T.T), T


def _nullspace(A: NDArray[np.float64], solver: str = "eigh") -> tuple[NDArray, dict[str, Any]]:
    """Lecture 97–101: minimize ||A*m||² subject to ||m||=1.

    eigh(A.T@A) is the actual default solution, not just a diagnostic. Coordinate
    normalization is essential because the normal matrix squares the condition
    number. SVD is an explicit alternative and checks rank for either solver.
    """
    if solver not in ("eigh", "svd"):
        raise ValueError("Nullspace solver must be 'eigh' or 'svd'")
    norm = np.linalg.norm(A)
    if not np.isfinite(norm) or norm == 0:
        raise ValueError("Invalid homogeneous calibration system")
    A = A / norm
    singular = np.linalg.svd(A, compute_uv=False)
    required_rank = A.shape[1] - 1
    if len(singular) < required_rank or singular[required_rank - 1] < singular[0] * 1e-10:
        raise ValueError(
            "Degenerate calibration system; provide more spatially diverse points/views"
        )
    eigenvalues, eigenvectors = np.linalg.eigh(A.T @ A)
    if solver == "eigh":
        m = eigenvectors[:, 0]
    else:
        _, _, Vt = np.linalg.svd(A, full_matrices=A.shape[0] < A.shape[1])
        m = Vt[-1]
    return m, {
        "solver": solver,
        "eigenvalues": eigenvalues.tolist(),
        "algebraic_residual": float(np.linalg.norm(A @ m)),
        "singular_values": singular.tolist(),
    }


def estimate_projection_matrix(
    P_w: ArrayLike, pixels: ArrayLike, *, solver: str = "eigh"
) -> tuple[NDArray[np.float64], dict[str, Any]]:
    """Estimate lecture 3x4 M from >=6 noncoplanar known 3D/ideal-pixel pairs.

    These must be undistorted correspondences. One flat checkerboard view cannot
    determine a general M; use calibrate_planar_views for multiple flat views.
    """
    P_w = _correspondences(P_w, 3, "P_w")
    pixels = _correspondences(pixels, 2, "pixels")
    if len(P_w) != len(pixels) or len(P_w) < 6:
        raise ValueError("DLT requires at least six paired 3D and 2D observations")
    if np.linalg.matrix_rank(P_w - P_w.mean(axis=0)) < 3:
        raise ValueError(
            "3D DLT is degenerate for coplanar points; use multi-view planar calibration"
        )
    if np.linalg.matrix_rank(pixels - pixels.mean(axis=0)) < 2:
        raise ValueError("Image correspondences are collinear")
    P_normalized, T_world = _normalize(P_w)
    uv_normalized, T_image = _normalize(pixels)
    P_h = to_homogeneous(P_normalized)
    A = np.zeros((2 * len(P_w), 12))
    # Lecture slide 97's P calligraphic matrix, with rows (P^T,0,-uP^T)
    # and (0,P^T,-vP^T); row-major m stacks M's three row vectors.
    A[0::2, :4], A[1::2, 4:8] = P_h, P_h
    A[0::2, 8:] = -uv_normalized[:, :1] * P_h
    A[1::2, 8:] = -uv_normalized[:, 1:] * P_h
    m, diagnostics = _nullspace(A, solver)
    M = np.linalg.solve(T_image, m.reshape(3, 4) @ T_world)
    M /= np.linalg.norm(M[:, :3])
    if np.linalg.det(M[:, :3]) < 0:
        M = -M
    return M, diagnostics


def decompose_projection_matrix(
    M: ArrayLike, image_size: tuple[int, int]
) -> tuple[CameraIntrinsics, CameraExtrinsics]:
    """Lecture 90–91: M' = K*R_cw via RQ; C_w = -inverse(M')*M[:,3]."""
    M = np.array(M, dtype=np.float64, copy=True)
    if M.shape != (3, 4) or not np.isfinite(M).all() or np.linalg.matrix_rank(M[:, :3]) < 3:
        raise ValueError("A finite camera requires an invertible left 3x3 block of M")
    # Remove arbitrary projective magnitude before determinant/sign decisions.
    M /= np.max(np.abs(M[:, :3]))
    if np.linalg.det(M[:, :3]) < 0:
        M *= -1
    K, R_cw = rq(M[:, :3])
    signs = np.diag(np.where(np.diag(K) < 0, -1.0, 1.0))
    K, R_cw = K @ signs, signs @ R_cw
    K /= K[2, 2]
    C_w = -np.linalg.solve(M[:, :3], M[:, 3])
    return (
        CameraIntrinsics.from_K(
            K, image_size, assumptions=("DLT correspondences must already be undistorted",)
        ),
        CameraExtrinsics(R_cw, C_w),
    )


def estimate_homography(
    plane_points: ArrayLike, pixels: ArrayLike, *, solver: str = "eigh"
) -> tuple[NDArray[np.float64], dict[str, Any]]:
    plane_points = _correspondences(plane_points, 2, "plane_points")
    pixels = _correspondences(pixels, 2, "pixels")
    if len(plane_points) != len(pixels) or len(plane_points) < 4:
        raise ValueError("A homography needs at least four paired planar/image points")
    if (
        np.linalg.matrix_rank(plane_points - plane_points.mean(axis=0)) < 2
        or np.linalg.matrix_rank(pixels - pixels.mean(axis=0)) < 2
    ):
        raise ValueError("Homography points must not be collinear")
    XY, T_plane = _normalize(plane_points)
    uv, T_image = _normalize(pixels)
    P_h = to_homogeneous(XY)
    A = np.zeros((len(XY) * 2, 9))
    A[0::2, :3], A[1::2, 3:6] = P_h, P_h
    A[0::2, 6:] = -uv[:, :1] * P_h
    A[1::2, 6:] = -uv[:, 1:] * P_h
    h, diagnostics = _nullspace(A, solver)
    H = np.linalg.solve(T_image, h.reshape(3, 3) @ T_plane)
    H /= np.linalg.norm(H)
    return H, diagnostics


def _constraint(h_i: NDArray, h_j: NDArray) -> NDArray:
    return np.array(
        [
            h_i[0] * h_j[0],
            h_i[0] * h_j[1] + h_i[1] * h_j[0],
            h_i[1] * h_j[1],
            h_i[2] * h_j[0] + h_i[0] * h_j[2],
            h_i[2] * h_j[1] + h_i[1] * h_j[2],
            h_i[2] * h_j[2],
        ]
    )


def _pose_from_homography(H: NDArray, K: NDArray) -> CameraExtrinsics:
    columns = np.linalg.solve(K, H)
    scale = 2 / (np.linalg.norm(columns[:, 0]) + np.linalg.norm(columns[:, 1]))
    if columns[2, 2] < 0:
        scale = -scale
    r1, r2, O_cw = (scale * columns[:, index] for index in range(3))
    approximate_R = np.column_stack((r1, r2, np.cross(r1, r2)))
    U, _, Vt = np.linalg.svd(approximate_R)
    R_cw = U @ np.diag([1.0, 1.0, np.linalg.det(U @ Vt)]) @ Vt
    C_w = -R_cw.T @ O_cw
    return CameraExtrinsics(R_cw, C_w)


@dataclass(frozen=True, slots=True)
class CalibrationResult:
    intrinsics: CameraIntrinsics
    extrinsics: tuple[CameraExtrinsics, ...]
    initial_rmse_px: float
    rmse_px: float
    per_view_rmse_px: tuple[float, ...]
    diagnostics: dict[str, Any]
    method: str
    view_names: tuple[str, ...]
    coordinate_units: str = "world_units"

    def save(self, path: str | Path) -> Path:
        views = []
        for name, pose, error in zip(
            self.view_names, self.extrinsics, self.per_view_rmse_px, strict=True
        ):
            views.append(
                {
                    "image": name,
                    "R_cw": pose.R_cw.tolist(),
                    "C_w": pose.C_w.tolist(),
                    "O_cw": pose.O_cw.tolist(),
                    "T_scene": pose.T_scene.tolist(),
                    "M": pose.projection_matrix(self.intrinsics.K).tolist(),
                    "rmse_px": error,
                }
            )
        return save_intrinsics(
            path,
            self.intrinsics,
            calibration={
                "method": self.method,
                "coordinate_units": self.coordinate_units,
                "initial_rmse_px": self.initial_rmse_px,
                "rmse_px": self.rmse_px,
                "diagnostics": self.diagnostics,
                "views": views,
            },
        )


def calibrate_3d(
    P_w: ArrayLike,
    pixels: ArrayLike,
    image_size: tuple[int, int],
    *,
    solver: str = "eigh",
    camera_id: str | None = None,
    coordinate_units: str = "world_units",
) -> CalibrationResult:
    M, diagnostics = estimate_projection_matrix(P_w, pixels, solver=solver)
    intrinsics, pose = decompose_projection_matrix(M, image_size)
    intrinsics = replace(intrinsics, camera_id=camera_id)
    errors = intrinsics.project(P_w, pose, distort=False) - np.asarray(pixels)
    rmse = float(np.sqrt(np.mean(np.sum(errors * errors, axis=1))))
    return CalibrationResult(
        intrinsics,
        (pose,),
        rmse,
        rmse,
        (rmse,),
        diagnostics,
        "normalized_dlt_3d",
        ("known_3d_correspondences",),
        coordinate_units,
    )


def checkerboard_points(columns: int, rows: int, square_size_m: float) -> NDArray[np.float64]:
    if type(columns) is not int or type(rows) is not int or columns < 2 or rows < 2:
        raise ValueError("Checkerboard columns/rows must be integers >=2 (inner corners)")
    if not np.isfinite(square_size_m) or square_size_m <= 0:
        raise ValueError("square_size_m must be positive and finite")
    xy = np.mgrid[:columns, :rows].T.reshape(-1, 2) * square_size_m
    return np.column_stack((xy, np.zeros(len(xy))))


def calibrate_planar_views(
    object_points: ArrayLike,
    image_points: list[ArrayLike],
    image_size: tuple[int, int],
    *,
    solver: str = "eigh",
    refine: bool = True,
    fit_skew: bool = False,
    camera_id: str | None = None,
    view_names: tuple[str, ...] | None = None,
    coordinate_units: str = "m",
) -> CalibrationResult:
    """Eigenvector initialization + geometric least squares, including distortion.

    The target must lie in world plane Z=0 and appear at >=3 diverse orientations.
    OpenCV is not used to estimate K or poses; it is used only for corner detection
    in calibrate_checkerboard_folder. R_cw/C_w describe each target-view world frame.
    """
    P_w = _correspondences(object_points, 3, "object_points")
    if len(P_w) < 4 or not np.allclose(P_w[:, 2], 0, atol=1e-12, rtol=0):
        raise ValueError("Planar calibration requires at least four points on Z=0")
    if len(image_points) < 3:
        raise ValueError("At least three differently tilted checkerboard views are required")
    observations = [_correspondences(points, 2, "image_points") for points in image_points]
    if any(len(points) != len(P_w) for points in observations):
        raise ValueError("Each view must have one image observation per target point")
    names = view_names or tuple(f"view_{index:03d}" for index in range(len(observations)))
    if len(names) != len(observations):
        raise ValueError("view_names must match the number of views")
    # One shared pixel normalization improves conditioning of V.T@V while leaving
    # a shared upper-triangular K in the normalized image coordinate system.
    _, T_image = _normalize(np.concatenate(observations))
    homographies = []
    constraints = []
    for pixels in observations:
        uv = from_homogeneous(to_homogeneous(pixels) @ T_image.T)
        H, _ = estimate_homography(P_w[:, :2], uv, solver=solver)
        h1, h2 = H[:, 0], H[:, 1]
        constraints.extend((_constraint(h1, h2), _constraint(h1, h1) - _constraint(h2, h2)))
        homographies.append(np.linalg.solve(T_image, H))
    b, diagnostics = _nullspace(np.array(constraints), solver)
    B = np.array([[b[0], b[1], b[3]], [b[1], b[2], b[4]], [b[3], b[4], b[5]]])
    if np.trace(B) < 0:
        B = -B
    try:
        # B=K^-T K^-1: Cholesky yields the upper-triangular inverse of K.
        K_normalized = np.linalg.inv(np.linalg.cholesky(B).T)
    except np.linalg.LinAlgError as error:
        raise ValueError(
            "Cannot recover a positive-definite intrinsic conic; use more diverse "
            "views with less blur/distortion"
        ) from error
    K = np.linalg.solve(T_image, K_normalized)
    K /= K[2, 2]
    if not fit_skew:
        K[0, 1] = 0  # Lecture's perpendicular-pixel-axis hypothesis, explicitly constrained.
    intrinsics = CameraIntrinsics.from_K(
        K,
        image_size,
        camera_id=camera_id,
        assumptions=(
            "Shared lens/focus/zoom and stored-pixel dimensions across views",
            "Brown-Conrady k1,k2,p1,p2,k3 lens distortion",
            "Skew estimated" if fit_skew else "Perpendicular pixel axes: skew fixed at zero",
        ),
    )
    poses = tuple(_pose_from_homography(H, intrinsics.K) for H in homographies)
    initial_errors = [
        intrinsics.project(P_w, pose) - pixels
        for pose, pixels in zip(poses, observations, strict=True)
    ]
    initial_rmse = float(
        np.sqrt(np.mean(np.concatenate([np.sum(e * e, axis=1) for e in initial_errors])))
    )
    if refine:
        intrinsics, poses, optimization = _refine(P_w, observations, intrinsics, poses, fit_skew)
        diagnostics["optimization"] = optimization
    per_view = tuple(
        float(np.sqrt(np.mean(np.sum((intrinsics.project(P_w, pose) - pixels) ** 2, axis=1))))
        for pose, pixels in zip(poses, observations, strict=True)
    )
    rmse = float(np.sqrt(np.mean(np.square(per_view))))
    if rmse > initial_rmse + 1e-7:
        raise ValueError("Calibration refinement increased reprojection error")
    return CalibrationResult(
        intrinsics,
        poses,
        initial_rmse,
        rmse,
        per_view,
        diagnostics,
        "planar_homography_eigen_initialization",
        names,
        coordinate_units,
    )


def _refine(
    P_w: NDArray,
    observations: list[NDArray],
    initial: CameraIntrinsics,
    poses: tuple[CameraExtrinsics, ...],
    fit_skew: bool,
) -> tuple[CameraIntrinsics, tuple[CameraExtrinsics, ...], dict[str, Any]]:
    shared = [np.log(initial.fx), np.log(initial.fy), initial.cx, initial.cy]
    if fit_skew:
        shared.append(initial.skew)
    shared.extend(initial.distortion)
    values = list(shared)
    for pose in poses:
        values.extend(Rotation.from_matrix(pose.R_cw).as_rotvec())
        values.extend(pose.O_cw)
    shared_count = len(shared)
    if 2 * len(P_w) * len(poses) < len(values):
        raise ValueError(
            "Not enough corner observations to refine intrinsics, distortion, and poses"
        )

    def unpack(parameters):
        offset = 5 if fit_skew else 4
        intrinsics = replace(
            initial,
            fx=float(np.exp(parameters[0])),
            fy=float(np.exp(parameters[1])),
            cx=float(parameters[2]),
            cy=float(parameters[3]),
            skew=float(parameters[4]) if fit_skew else 0.0,
            distortion=tuple(parameters[offset : offset + 5]),
        )
        result = []
        for index in range(len(poses)):
            start = shared_count + 6 * index
            R_cw = Rotation.from_rotvec(parameters[start : start + 3]).as_matrix()
            O_cw = parameters[start + 3 : start + 6]
            result.append(CameraExtrinsics(R_cw, -R_cw.T @ O_cw))
        return intrinsics, tuple(result)

    def residuals(parameters):
        intrinsics, current_poses = unpack(parameters)
        result = []
        for pose, pixels in zip(current_poses, observations, strict=True):
            P_c = pose.world_to_camera(P_w)
            # Trial steps can cross Z=0. Clamp only for evaluating trial residuals;
            # final results must pass the physical positive-depth projection check.
            rays = P_c[:, :2] / np.maximum(P_c[:, 2:], 1e-6)
            predicted = intrinsics.pixels_from_normalized(intrinsics.distort_normalized(rays))
            result.extend((predicted - pixels).ravel())
        return np.asarray(result)

    lower = np.full(len(values), -np.inf)
    upper = np.full(len(values), np.inf)
    lower[:2], upper[:2] = np.log(1e-3), np.log(1e7)
    # For small calibration targets, a dense trust-region solve converges much
    # faster than loosely solved sparse normal equations with correlated K/poses.
    solution = least_squares(
        residuals,
        values,
        bounds=(lower, upper),
        x_scale="jac",
        max_nfev=300,
        ftol=1e-10,
        xtol=1e-10,
        gtol=1e-10,
    )
    if not solution.success:
        raise ValueError(f"Calibration refinement failed to converge: {solution.message}")
    intrinsics, result = unpack(solution.x)
    return (
        intrinsics,
        result,
        {
            "success": bool(solution.success),
            "evaluations": solution.nfev,
            "message": solution.message,
        },
    )


def calibrate_checkerboard_folder(
    folder: str | Path,
    columns: int,
    rows: int,
    square_size_m: float,
    *,
    solver: str = "eigh",
    camera_id: str | None = None,
    fit_skew: bool = False,
) -> CalibrationResult:
    P_w = checkerboard_points(columns, rows, square_size_m)
    if columns < 3 or rows < 3:
        raise ValueError("The corner detector requires at least 3 inner corners per dimension")
    folder = Path(folder)
    paths = sorted(
        path
        for path in folder.iterdir()
        if path.suffix.lower() in (".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff")
    )
    if not paths:
        raise ValueError(f"{folder}: no calibration images found")
    observations, names, skipped = [], [], []
    size = None
    for path in paths:
        gray = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE | cv2.IMREAD_IGNORE_ORIENTATION)
        if gray is None:
            raise ValueError(f"{path}: image cannot be decoded")
        current_size = (gray.shape[1], gray.shape[0])
        if size is not None and size != current_size:
            raise ValueError("All calibration images must have the same stored-pixel dimensions")
        size = current_size
        found, corners = cv2.findChessboardCornersSB(
            gray,
            (columns, rows),
            cv2.CALIB_CB_NORMALIZE_IMAGE | cv2.CALIB_CB_EXHAUSTIVE | cv2.CALIB_CB_ACCURACY,
        )
        if not found:
            skipped.append(path.name)
            continue
        observations.append(corners.reshape(-1, 2).astype(float))
        names.append(path.name)
    result = calibrate_planar_views(
        P_w,
        observations,
        size,
        solver=solver,
        fit_skew=fit_skew,
        camera_id=camera_id,
        view_names=tuple(names),
    )
    result.diagnostics["skipped_images"] = skipped
    result.diagnostics["checkerboard"] = {
        "columns": columns,
        "rows": rows,
        "square_size_m": square_size_m,
    }
    return result
