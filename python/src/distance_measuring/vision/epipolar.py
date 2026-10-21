"""Lecture 002 pp. 138–139: epipolar lines and horizontal rectification.

F estimation, Sampson distance and RANSAC are implementation extensions, not
algorithms specified by those slides. See docs/math/feature_matching.md.
"""

from dataclasses import dataclass

import cv2
import numpy as np
from numpy.typing import ArrayLike, NDArray

from distance_measuring.camera.geometry import from_homogeneous, points_array, to_homogeneous


@dataclass(frozen=True)
class GeometricMatches:
    F: NDArray[np.float64] | None
    inliers: NDArray[np.bool_]
    sampson_error_px: NDArray[np.float64]
    failure: str | None = None


def _pair(first: ArrayLike, second: ArrayLike) -> tuple[np.ndarray, np.ndarray]:
    first, second = points_array(first, 2), points_array(second, 2)
    if first.ndim != 2 or second.ndim != 2 or first.shape != second.shape:
        raise ValueError("Correspondence arrays must have equal Nx2 shapes")
    return first, second


def sampson_errors(F: ArrayLike, first: ArrayLike, second: ArrayLike) -> np.ndarray:
    first, second = _pair(first, second)
    F = np.asarray(F, dtype=np.float64)
    if F.shape != (3, 3) or not np.isfinite(F).all() or np.linalg.norm(F) == 0:
        raise ValueError("F must be a finite nonzero 3x3 matrix")
    F = F / np.linalg.norm(F)
    # Lecture 002 p. 138: l2 = F x1, l1 = F.T x2, x2.T F x1 = 0.
    # Homogeneous pixel coordinates follow pp. 65–68; this algebra extends p. 138.
    x1, x2 = to_homogeneous(first), to_homogeneous(second)
    lines2, lines1 = x1 @ F.T, x2 @ F
    numerator = np.abs(np.sum(x2 * lines2, axis=1))
    denominator = np.sqrt(np.sum(lines2[:, :2] ** 2 + lines1[:, :2] ** 2, axis=1))
    return np.divide(
        numerator, denominator, out=np.full(len(first), np.inf), where=denominator > 1e-15
    )


def estimate_fundamental(
    first: ArrayLike,
    second: ArrayLike,
    *,
    threshold_px: float = 1.5,
    confidence: float = 0.999,
    max_iterations: int = 10000,
    seed: int = 0,
) -> GeometricMatches:
    """F uses ideal, undistorted pixels. Mask indices align with input match rows."""
    first, second = _pair(first, second)
    if not np.isfinite(threshold_px) or threshold_px <= 0:
        raise ValueError("threshold_px must be positive and finite")
    if not np.isfinite(confidence) or not 0 < confidence < 1:
        raise ValueError("confidence must be strictly between zero and one")
    if type(max_iterations) is not int or max_iterations < 1:
        raise ValueError("max_iterations must be a positive integer")
    if type(seed) is not int or not 0 <= seed <= 2**31 - 1:
        raise ValueError("seed must be an integer in [0, 2**31-1]")

    def failed(reason: str) -> GeometricMatches:
        return GeometricMatches(
            None, np.zeros(len(first), bool), np.full(len(first), np.inf), reason
        )

    if len(first) < 8:
        return failed("At least eight descriptor matches are required")
    if any(np.linalg.matrix_rank(points - points.mean(axis=0)) < 2 for points in (first, second)):
        return failed("Coincident or collinear correspondences cannot constrain F")
    # RANSAC handles descriptor outliers before enforcing the epipolar constraint
    # of lecture 002 p. 138. RNG is reset per call for repeatable serial runs.
    cv2.setRNGSeed(seed)
    F, _ = cv2.findFundamentalMat(
        first, second, cv2.FM_RANSAC, threshold_px, confidence, max_iterations
    )
    if F is None or F.shape != (3, 3) or not np.isfinite(F).all():
        return failed("RANSAC could not estimate a finite fundamental matrix")
    U, singular, Vt = np.linalg.svd(F)
    if singular[1] <= singular[0] * 1e-12:
        return failed("Estimated fundamental matrix has rank below two")
    # A valid F has rank two: project numerical noise out of its third singular value.
    singular[2] = 0
    F = (U * singular) @ Vt
    F /= np.linalg.norm(F)
    error = sampson_errors(F, first, second)
    mask = error <= threshold_px
    if mask.sum() < 8:
        return failed("F has fewer than eight Sampson-consistent correspondences")
    return GeometricMatches(F, mask, error)


@dataclass(frozen=True)
class Rectification:
    H_first: NDArray[np.float64]
    H_second: NDArray[np.float64]
    first_points: NDArray[np.float64]
    second_points: NDArray[np.float64]


def rectify_matches(
    first: ArrayLike, second: ArrayLike, geometry: GeometricMatches, image_size: tuple[int, int]
) -> Rectification:
    first, second = _pair(first, second)
    if geometry.F is None or geometry.inliers.shape != (len(first),) or geometry.inliers.sum() < 8:
        raise ValueError("Rectification requires a verified F and at least eight inliers")
    if len(image_size) != 2 or any(type(value) is not int or value <= 0 for value in image_size):
        raise ValueError("Rectification image_size must contain positive integer width and height")
    # Lecture 002 pp. 139–140: map both views to parallel horizontal epipolar
    # scanlines. These are projective 3x3 image homographies, not camera R or M.
    success, H1, H2 = cv2.stereoRectifyUncalibrated(
        first[geometry.inliers], second[geometry.inliers], geometry.F, image_size, threshold=0
    )
    if not success or any(
        H is None or not np.isfinite(H).all() or np.linalg.matrix_rank(H) != 3 for H in (H1, H2)
    ):
        raise ValueError("Uncalibrated rectification could not produce nonsingular homographies")
    try:
        p1 = from_homogeneous(to_homogeneous(first) @ H1.T)
        p2 = from_homogeneous(to_homogeneous(second) @ H2.T)
    except ValueError as error:
        raise ValueError("Rectification sends a matched point to infinity") from error
    # p. 139's common scanlines must remain visible on the output canvas. The
    # uncalibrated H matrices can otherwise place most matches outside the frame.
    # A common y scale/offset and independent x offsets preserve horizontal lines.
    # Use robust x support to avoid one extreme disparity shrinking the whole view.
    support = [points[geometry.inliers] for points in (p1, p2)]
    x_bounds = [np.percentile(points[:, 0], [2, 98]) for points in support]
    y_min = min(points[:, 1].min() for points in support)
    y_max = max(points[:, 1].max() for points in support)
    width, height = image_size
    scale = min(
        1.0,
        0.9 * height / max(y_max - y_min, 1),
        *(0.9 * width / max(bounds[1] - bounds[0], 1) for bounds in x_bounds),
    )
    y_offset = height / 2 - scale * (y_min + y_max) / 2
    adjusted = []
    for H, bounds in zip((H1, H2), x_bounds, strict=True):
        viewport = np.array(
            [[scale, 0, width / 2 - scale * bounds.mean()], [0, scale, y_offset], [0, 0, 1]]
        )
        adjusted.append(viewport @ H)
    H1, H2 = adjusted
    p1 = from_homogeneous(to_homogeneous(first) @ H1.T)
    p2 = from_homogeneous(to_homogeneous(second) @ H2.T)
    return Rectification(H1, H2, p1, p2)
