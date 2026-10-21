"""Homogeneous camera geometry; see docs/math/camera_model.md.

Local lecture references use PPTX slide order: 39–45, 65–68, 72, 81–83, 90–102.
M is the 3x4 projection matrix; K is internal, T_scene is external.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import ArrayLike, NDArray

P0 = np.column_stack((np.eye(3), np.zeros(3)))  # Lecture slide 82: canonical [I | 0].
P0.setflags(write=False)


def points_array(points: ArrayLike, dimension: int, name: str = "points") -> NDArray[np.float64]:
    result = np.asarray(points, dtype=np.float64)
    if result.ndim < 1 or result.shape[-1] != dimension or not np.isfinite(result).all():
        raise ValueError(f"{name} must be finite coordinates with last dimension {dimension}")
    return result


def to_homogeneous(points: ArrayLike) -> NDArray[np.float64]:
    """Append a scale coordinate of one, preserving preceding batch dimensions."""
    points = np.asarray(points, dtype=np.float64)
    if points.ndim < 1 or points.shape[-1] < 1 or not np.isfinite(points).all():
        raise ValueError("Expected finite Cartesian coordinates")
    return np.concatenate((points, np.ones((*points.shape[:-1], 1))), axis=-1)


def from_homogeneous(points: ArrayLike) -> NDArray[np.float64]:
    """Divide by the final coordinate; points at infinity have no finite result."""
    points = np.asarray(points, dtype=np.float64)
    if points.ndim < 1 or points.shape[-1] < 2 or not np.isfinite(points).all():
        raise ValueError("Expected finite homogeneous coordinates")
    if np.any(points[..., -1] == 0):
        raise ValueError("Cannot dehomogenize a point at infinity (zero scale coordinate)")
    with np.errstate(over="raise", invalid="raise", divide="raise"):
        try:
            return points[..., :-1] / points[..., -1:]
        except FloatingPointError as error:
            raise ValueError("Homogeneous division produced non-finite coordinates") from error


def project_matrix(M: ArrayLike, P_w: ArrayLike) -> NDArray[np.float64]:
    """Lecture slides 67/82: rho*m = M*P_w_h, followed by homogeneous division.

    M describes ideal pinhole pixels, without lens distortion. Its overall sign
    and scale are arbitrary, so this function does not infer physical depth.
    """
    M = np.asarray(M, dtype=np.float64)
    if M.shape != (3, 4) or not np.isfinite(M).all() or np.linalg.matrix_rank(M) != 3:
        raise ValueError("M must be a finite rank-3 matrix with shape (3, 4)")
    return from_homogeneous(to_homogeneous(points_array(P_w, 3, "P_w")) @ M.T)


@dataclass(frozen=True, slots=True)
class CameraExtrinsics:
    """Lecture R, C: P_c = R_cw*(P_w-C_w) = R_cw*P_w + O_cw.

    C_w (also called T in earlier slides) is the camera center in world space.
    O_cw = -R_cw*C_w is the translation column, and is NOT the camera center.
    """

    R_cw: NDArray[np.float64]
    C_w: NDArray[np.float64]

    def __post_init__(self) -> None:
        rotation = np.array(self.R_cw, dtype=np.float64, copy=True)
        center = np.array(self.C_w, dtype=np.float64, copy=True)
        if rotation.shape != (3, 3) or not np.isfinite(rotation).all():
            raise ValueError("R_cw must be a finite 3x3 rotation")
        if not np.allclose(rotation @ rotation.T, np.eye(3), atol=1e-7, rtol=0) or not np.isclose(
            np.linalg.det(rotation), 1, atol=1e-7, rtol=0
        ):
            raise ValueError("R_cw must be orthonormal with determinant +1")
        if center.shape != (3,) or not np.isfinite(center).all():
            raise ValueError("C_w must be a finite world-space camera center with shape (3,)")
        rotation.setflags(write=False)
        center.setflags(write=False)
        object.__setattr__(self, "R_cw", rotation)
        object.__setattr__(self, "C_w", center)

    @property
    def O_cw(self) -> NDArray[np.float64]:
        return -self.R_cw @ self.C_w

    @property
    def M_ext(self) -> NDArray[np.float64]:
        """The 3x4 extrinsic block [R | O] = [R | -R*C]."""
        return np.column_stack((self.R_cw, self.O_cw))

    @property
    def T_scene(self) -> NDArray[np.float64]:
        """Lecture slide 82: 4x4 homogeneous world-to-camera transformation."""
        return np.vstack((self.M_ext, [0, 0, 0, 1]))

    def world_to_camera(self, P_w: ArrayLike) -> NDArray[np.float64]:
        return (points_array(P_w, 3, "P_w") - self.C_w) @ self.R_cw.T

    def camera_to_world(self, P_c: ArrayLike) -> NDArray[np.float64]:
        return points_array(P_c, 3, "P_c") @ self.R_cw + self.C_w

    def projection_matrix(self, K: ArrayLike) -> NDArray[np.float64]:
        K = np.asarray(K, dtype=np.float64)
        if K.shape != (3, 3) or not np.isfinite(K).all() or np.linalg.matrix_rank(K) != 3:
            raise ValueError("K must be a finite invertible 3x3 matrix")
        # Lecture 82: M = K P0 T_scene = K [R_cw | -R_cw C_w].
        M = K @ P0 @ self.T_scene
        return M
