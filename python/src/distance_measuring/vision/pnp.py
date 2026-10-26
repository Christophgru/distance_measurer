"""Shared calibrated PnP registration, extracted from the pairwise scene path."""

from dataclasses import dataclass
from time import perf_counter

import cv2
import numpy as np

from distance_measuring.camera import CameraExtrinsics


@dataclass(frozen=True)
class PnPConfig:
    min_inliers: int = 8
    threshold_px: float = 3.0
    iterations: int = 10000
    confidence: float = 0.999
    seed: int = 0

    def __post_init__(self):
        if type(self.min_inliers) is not int or self.min_inliers < 6:
            raise ValueError("PnP needs at least six inliers")
        if not np.isfinite(self.threshold_px) or self.threshold_px <= 0:
            raise ValueError("PnP pixel threshold must be positive and finite")
        if type(self.iterations) is not int or self.iterations < 1:
            raise ValueError("PnP iterations must be a positive integer")
        if not np.isfinite(self.confidence) or not 0 < self.confidence < 1:
            raise ValueError("PnP confidence must be in (0, 1)")
        if type(self.seed) is not int or not 0 <= self.seed < 2**31:
            raise ValueError("PnP seed must be an integer in [0, 2**31)")


DEFAULT_PNP = PnPConfig()


@dataclass(frozen=True)
class PnPResult:
    pose: CameraExtrinsics | None
    inliers: np.ndarray
    errors_px: np.ndarray
    report: dict


def estimate_pnp(world, pixels, camera, *, config: PnPConfig = DEFAULT_PNP):
    start = perf_counter()
    world, pixels = np.asarray(world, float), np.asarray(pixels, float)
    if (
        world.ndim != 2
        or world.shape[1] != 3
        or pixels.shape != (len(world), 2)
        or not np.isfinite(world).all()
        or not np.isfinite(pixels).all()
        or any(camera.distortion)
        or (pixels < 0).any()
        or (pixels >= camera.image_size).any()
    ):
        raise ValueError("PnP requires aligned finite world points and ideal in-bounds pixels")
    mask, errors = np.zeros(len(world), bool), np.full(len(world), np.inf)
    pose, reason, ransac_count = None, None, 0
    if len(world) < config.min_inliers:
        reason = "insufficient_3d_2d_correspondences"
    elif any(np.linalg.matrix_rank(a - a.mean(axis=0)) < 2 for a in (world, pixels)):
        reason = "degenerate_3d_2d_correspondences"
    else:
        # OpenCV assumes zero skew: normalize with our K first, then validate
        # exact pixel errors using the original (possibly skewed) camera.
        normalized = camera.normalize_pixels(pixels)
        sampling = np.linalg.svd(camera.K[:2, :2], compute_uv=False)[-1]
        cv2.setRNGSeed(config.seed)
        try:
            ok, rvec, tvec, indices = cv2.solvePnPRansac(
                world,
                normalized,
                np.eye(3),
                None,
                iterationsCount=config.iterations,
                reprojectionError=config.threshold_px / sampling,
                confidence=config.confidence,
                flags=cv2.SOLVEPNP_EPNP,
            )
            ransac_count = len(indices) if indices is not None else 0
            if not ok or ransac_count < config.min_inliers:
                reason = "insufficient_pnp_ransac_inliers"
            else:
                keep = indices[:, 0]
                rvec, tvec = cv2.solvePnPRefineLM(
                    world[keep], normalized[keep], np.eye(3), None, rvec, tvec
                )
                rotation = cv2.Rodrigues(rvec)[0]
                trial = CameraExtrinsics(rotation, -rotation.T @ tvec.ravel())
                depth = trial.world_to_camera(world)[:, 2]
                forward = depth > 0
                errors[forward] = np.linalg.norm(
                    camera.project(world[forward], trial, distort=False) - pixels[forward], axis=1
                )
                mask[keep] = forward[keep] & (errors[keep] <= config.threshold_px)
                if mask.sum() < config.min_inliers:
                    reason = "insufficient_pnp_verified_inliers"
                else:
                    pose = trial
        except (cv2.error, ValueError) as error:
            reason = f"pnp_solver_failed: {error}"
    if pose is None:
        mask[:] = False
    return PnPResult(
        pose,
        mask,
        errors,
        {
            "success": pose is not None,
            "reason": reason,
            "input_count": len(world),
            "ransac_inlier_count": ransac_count,
            "inlier_count": int(mask.sum()),
            "reprojection_rmse_px": float(np.sqrt(np.mean(errors[mask] ** 2)))
            if mask.any()
            else None,
            "R_cw": pose.R_cw.tolist() if pose else None,
            "C_w": pose.C_w.tolist() if pose else None,
            "runtime_s": perf_counter() - start,
        },
    )
