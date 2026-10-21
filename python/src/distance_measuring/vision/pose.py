"""Relative pose: lecture 002 pp. 65–76, 81–83, 132–138, with E/SVD extensions.

Page numbers are 1-based PPTX slide order. Essential estimation/decomposition and
cheirality extend the deck's camera/ray geometry; see docs/math/relative_pose.md.
"""

from dataclasses import dataclass

import cv2
import numpy as np
from numpy.typing import ArrayLike

from distance_measuring.camera import CameraExtrinsics, to_homogeneous

from .epipolar import _pair, sampson_errors


def essential_manifold(matrix: ArrayLike) -> np.ndarray:
    """Project onto E's equal-first-two-singular-values, rank-two constraint."""
    matrix = np.asarray(matrix, dtype=float)
    if matrix.shape != (3, 3) or not np.isfinite(matrix).all():
        raise ValueError("Essential matrix must be a finite nonzero 3x3 array")
    magnitude = np.max(np.abs(matrix))
    if magnitude == 0:
        raise ValueError("Essential matrix must be nonzero")
    U, singular, Vt = np.linalg.svd(matrix / magnitude)
    if singular[1] < singular[0] * 1e-12:
        raise ValueError("Essential matrix must have rank at least two")
    # Extension of lecture 002 p. 138: E=[t]_x R has singular values (s,s,0).
    # Fixing Frobenius norm removes its arbitrary homogeneous scale (pp. 65–68).
    return U @ np.diag([1 / np.sqrt(2), 1 / np.sqrt(2), 0]) @ Vt


def estimate_essential_linear(first: ArrayLike, second: ArrayLike) -> np.ndarray:
    """Normalized eight-point eigenvector fit, for clean calibrated correspondences."""
    first, second = _pair(first, second)
    if len(first) < 8:
        raise ValueError("Linear E estimation requires at least eight correspondences")
    normalized, transforms = [], []
    for points in (first, second):
        center = points.mean(axis=0)
        rms = np.sqrt(np.mean(np.sum((points - center) ** 2, axis=1)))
        if rms <= np.finfo(float).eps:
            raise ValueError("Coincident correspondences cannot constrain E")
        scale = np.sqrt(2) / rms
        T = np.array([[scale, 0, -scale * center[0]], [0, scale, -scale * center[1]], [0, 0, 1]])
        normalized.append(to_homogeneous(points) @ T.T)
        transforms.append(T)
    x1, x2 = normalized
    A = np.einsum("ni,nj->nij", x2, x1).reshape(-1, 9)
    if np.linalg.matrix_rank(A) < 8:
        raise ValueError("Degenerate correspondences do not constrain the eight-point system")
    # Lecture 002 pp. 98, 101 minimize ||A m|| with a unit-norm constraint for M.
    # Here the SAME minimum-eigenvector method fits e=vec(E), extending p. 138:
    # each row is kron(x2,x1), from x2.T E x1=0. This is not 3D calibration of M.
    _, vectors = np.linalg.eigh(A.T @ A)
    fitted = vectors[:, 0].reshape(3, 3)
    E = transforms[1].T @ fitted @ transforms[0]
    return essential_manifold(E)


@dataclass(frozen=True)
class EssentialEstimate:
    E: np.ndarray | None
    inliers: np.ndarray
    sampson_error_normalized: np.ndarray
    failure: str | None = None


def estimate_essential(
    first: ArrayLike,
    second: ArrayLike,
    *,
    threshold_normalized: float = 0.001,
    seed: int = 0,
) -> EssentialEstimate:
    """Five-point RANSAC, optional eigenvector consensus refit; inputs are rays x/z,y/z."""
    first, second = _pair(first, second)
    if not np.isfinite(threshold_normalized) or threshold_normalized <= 0:
        raise ValueError("threshold_normalized must be positive and finite")
    if type(seed) is not int or not 0 <= seed <= 2**31 - 1:
        raise ValueError("seed must be an integer in [0, 2**31-1]")

    def failed(reason):
        return EssentialEstimate(
            None, np.zeros(len(first), bool), np.full(len(first), np.inf), reason
        )

    if len(first) < 8:
        return failed("At least eight calibrated correspondences are required")
    if any(np.linalg.matrix_rank(points - points.mean(axis=0)) < 2 for points in (first, second)):
        return failed("Coincident or collinear correspondences cannot constrain E")
    cv2.setRNGSeed(seed)
    matrices, _ = cv2.findEssentialMat(
        first,
        second,
        np.eye(3),
        method=cv2.RANSAC,
        prob=0.999,
        threshold=threshold_normalized,
        maxIters=10000,
    )
    if matrices is None or matrices.ndim != 2 or matrices.shape[1] != 3 or matrices.shape[0] % 3:
        return failed("RANSAC could not estimate an essential matrix")
    candidates = []
    for matrix in matrices.reshape(-1, 3, 3):
        try:
            candidates.append(essential_manifold(matrix))
        except ValueError:
            continue
    if not candidates:
        return failed("RANSAC did not return a valid rank-two essential matrix")

    def score(E):
        error = sampson_errors(E, first, second)
        mask = error <= threshold_normalized
        return int(mask.sum()), -float(np.median(error[mask])) if mask.any() else -np.inf

    E = max(candidates, key=score)
    mask = sampson_errors(E, first, second) <= threshold_normalized
    if mask.sum() < 8:
        return failed("E has fewer than eight Sampson-consistent correspondences")
    # Refit only if it improves consensus or, at equal count, median residual.
    # Planar configurations can make the linear system ambiguous; keep RANSAC E.
    try:
        refined = estimate_essential_linear(first[mask], second[mask])
        if score(refined) > score(E):
            E = refined
    except ValueError:
        pass
    error = sampson_errors(E, first, second)
    return EssentialEstimate(E, error <= threshold_normalized, error)


def decompose_essential(E: ArrayLike) -> tuple[tuple[np.ndarray, np.ndarray], ...]:
    """Return the four (R_second_first,t_second_first) hypotheses, t of unit norm."""
    U, _, Vt = np.linalg.svd(essential_manifold(E))
    U[:, -1] *= np.linalg.det(U)
    Vt[-1] *= np.linalg.det(Vt)
    W = np.array([[0, -1, 0], [1, 0, 0], [0, 0, 1]])
    # Lecture 002 pp. 81–83: x_second = R*x_first + O. Our t is this O,
    # NOT the camera center C=-R.T*t. The four-way E/SVD decomposition is an
    # extension of p. 138; cheirality below resolves rotation/sign ambiguity.
    t = U[:, 2]
    return tuple((R, sign * t) for R in (U @ W @ Vt, U @ W.T @ Vt) for sign in (1, -1))


def _cheirality(first: np.ndarray, second: np.ndarray, R: np.ndarray, t: np.ndarray):
    """Temporary two-view DLT points solely for choosing a pose; no scene/track export."""
    # Lecture 002 pp. 82, 133–136: normalized 3x4 cameras [I|0] and [R|t]
    # intersect corresponding rays. Cross-multiply homogeneous projection to
    # build four linear equations, then take the smallest right singular vector.
    P1, P2 = np.c_[np.eye(3), np.zeros(3)], np.c_[R, t]
    A = np.stack(
        (
            first[:, 0, None] * P1[2] - P1[0],
            first[:, 1, None] * P1[2] - P1[1],
            second[:, 0, None] * P2[2] - P2[0],
            second[:, 1, None] * P2[2] - P2[1],
        ),
        axis=1,
    )
    _, _, Vt = np.linalg.svd(A)
    homogeneous = Vt[:, -1]
    valid = np.abs(homogeneous[:, 3]) > 1e-12
    points = np.divide(
        homogeneous[:, :3],
        homogeneous[:, 3, None],
        out=np.zeros((len(first), 3)),
        where=valid[:, None],
    )
    other = points @ R.T + t
    positive = valid & (points[:, 2] > 1e-9) & (other[:, 2] > 1e-9)
    p1 = np.divide(
        points[:, :2],
        points[:, 2, None],
        out=np.full_like(first, np.inf),
        where=np.abs(points[:, 2, None]) > 1e-12,
    )
    p2 = np.divide(
        other[:, :2],
        other[:, 2, None],
        out=np.full_like(second, np.inf),
        where=np.abs(other[:, 2, None]) > 1e-12,
    )
    reprojection = np.maximum(
        np.linalg.norm(p1 - first, axis=1), np.linalg.norm(p2 - second, axis=1)
    )
    # pp. 133–136's two optical rays, both expressed in the first camera frame.
    ray1, ray2 = to_homogeneous(first), to_homogeneous(second) @ R
    ray1 /= np.linalg.norm(ray1, axis=1, keepdims=True)
    ray2 /= np.linalg.norm(ray2, axis=1, keepdims=True)
    angles = np.degrees(np.arccos(np.clip(np.sum(ray1 * ray2, axis=1), -1, 1)))
    return positive, reprojection, angles


@dataclass(frozen=True)
class RelativePose:
    R: np.ndarray | None
    t_direction: np.ndarray | None
    inliers: np.ndarray
    cheirality_counts: tuple[int, ...]
    median_parallax_degrees: float | None
    failure: str | None = None


def recover_relative_pose(
    first: ArrayLike,
    second: ArrayLike,
    estimate: EssentialEstimate,
    *,
    min_parallax_degrees: float = 0.25,
    max_reprojection_error_normalized: float = 0.003,
    min_positive_fraction: float = 0.6,
) -> RelativePose:
    first, second = _pair(first, second)
    if not np.isfinite(min_parallax_degrees) or not 0 < min_parallax_degrees < 90:
        raise ValueError("min_parallax_degrees must be finite and between 0 and 90")
    if not np.isfinite(max_reprojection_error_normalized) or max_reprojection_error_normalized <= 0:
        raise ValueError("max_reprojection_error_normalized must be positive and finite")
    if not np.isfinite(min_positive_fraction) or not 0.5 < min_positive_fraction <= 1:
        raise ValueError("min_positive_fraction must be in (0.5, 1]")
    if estimate.inliers.dtype != bool or estimate.inliers.shape != (len(first),):
        raise ValueError("Essential inlier mask must be boolean and align with correspondences")

    def failed(reason, counts=(), parallax=None):
        return RelativePose(None, None, np.zeros(len(first), bool), counts, parallax, reason)

    if estimate.E is None or estimate.inliers.sum() < 8:
        return failed(estimate.failure or "Pose recovery requires E with at least eight inliers")
    hypotheses = decompose_essential(estimate.E)
    checks = [_cheirality(first, second, R, t) for R, t in hypotheses]
    counts = tuple(int((positive & estimate.inliers).sum()) for positive, _, _ in checks)
    best = int(np.argmax(counts))
    if counts[best] < max(8, min_positive_fraction * estimate.inliers.sum()):
        return failed("Too few essential inliers lie in front of both cameras", counts)
    positive, error, parallax = checks[best]
    eligible = estimate.inliers & positive & (error <= max_reprojection_error_normalized)
    median = float(np.median(parallax[eligible])) if eligible.any() else None
    mask = eligible & (parallax >= min_parallax_degrees)
    if median is None or median < min_parallax_degrees or mask.sum() < 8:
        return failed(
            "Insufficient parallax or reprojection support for translation", counts, median
        )
    R, t = hypotheses[best]
    return RelativePose(R, t, mask, counts, median)


def chain_camera(
    previous: CameraExtrinsics, relative: RelativePose, *, baseline: float = 1.0
) -> CameraExtrinsics:
    """Compose poses with an EXPLICIT chosen baseline; default unit size is arbitrary."""
    if relative.R is None or relative.t_direction is None or relative.failure:
        raise ValueError("Cannot chain an unsuccessful relative pose")
    if not np.isfinite(baseline) or baseline <= 0:
        raise ValueError("baseline must be positive and finite")
    # Lecture 002 pp. 39–45, 81–83: T_b_world = T_b_a @ T_a_world.
    # t is the world-origin column O; C_world = -R_world.T @ O_world.
    R = relative.R @ previous.R_cw
    O_cw = relative.R @ previous.O_cw + baseline * relative.t_direction
    return CameraExtrinsics(R, -R.T @ O_cw)
