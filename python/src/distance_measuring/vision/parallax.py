"""Rotation-only ray prediction and residual image parallax; no metric translation."""

from dataclasses import dataclass

import numpy as np

from distance_measuring.camera import CameraExtrinsics, CameraIntrinsics, to_homogeneous
from distance_measuring.camera.geometry import points_array


@dataclass(frozen=True)
class RotationParallax:
    predicted_pixels: np.ndarray
    residual_vectors_px: np.ndarray
    parallax_px: np.ndarray
    valid: np.ndarray
    predicted_in_bounds: np.ndarray


def rotation_compensated_parallax(
    first_pixels,
    second_pixels,
    first_camera: CameraIntrinsics,
    second_camera: CameraIntrinsics,
    R_second_first,
) -> RotationParallax:
    """Predict K2 R_c2_c1 K1^-1 x1, then observed x2 minus prediction.

    Inputs are ideal pixels, as persisted by P2. Invalid/behind-camera predicted
    rays retain aligned NaN outputs and a false valid mask. Offscreen forward
    rays remain measurable and have a separate in-bounds flag. R is a passive
    scene-ray transform, not the active phone rotation measured by the gyro.
    """
    first = points_array(first_pixels, 2, "first_pixels")
    second = points_array(second_pixels, 2, "second_pixels")
    if first.ndim != 2 or first.shape != second.shape:
        raise ValueError("Corresponding pixels must be aligned finite Nx2 arrays")
    if any(first_camera.distortion) or any(second_camera.distortion):
        raise ValueError("Parallax requires ideal pixels and distortion-free intrinsics")
    pose = CameraExtrinsics(R_second_first, np.zeros(3))
    rays = to_homogeneous(first_camera.normalize_pixels(first))
    rotated = pose.world_to_camera(rays)
    valid = np.isfinite(rotated).all(axis=1) & (rotated[:, 2] > 1e-9)
    predicted = np.full(first.shape, np.nan)
    if valid.any():
        predicted[valid] = second_camera.project(rays[valid], pose, distort=False)
    valid &= np.isfinite(predicted).all(axis=1)
    residual = second - predicted
    parallax = np.linalg.norm(residual, axis=1)
    in_bounds = (
        valid & (predicted >= 0).all(axis=1) & (predicted < second_camera.image_size).all(axis=1)
    )
    return RotationParallax(predicted, residual, parallax, valid, in_bounds)
