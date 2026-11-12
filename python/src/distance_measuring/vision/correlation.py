"""Two-image normalized correlation: lecture 002 pp. 141–144 (PPTX order).

No third view, camera pose, intrinsic matrix or metric scale is required.
Local homography normalization for oblique patches is an implementation extension.
"""

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass(frozen=True)
class CorrelationPeak:
    top_left: tuple[int, int] | None
    score: float | None
    peak_margin: float | None
    rejection_reason: str | None = None


def correlate_patch(template, search, *, min_score=0.7, min_margin=0.05, peak_radius=2):
    """Slide a zero-mean normalized-correlation window over a second image region.

    top_left is (x,y) in SEARCH pixels; a rejected peak is diagnostic only.
    """
    template, search = [np.asarray(a, dtype=np.float32) for a in (template, search)]
    if any(a.ndim != 2 or a.size == 0 or not np.isfinite(a).all() for a in (template, search)):
        raise ValueError("Correlation requires finite nonempty grayscale arrays")
    if any(a < b for a, b in zip(search.shape, template.shape, strict=True)):
        raise ValueError("Search region must be at least as large as the template")
    if (
        not np.isfinite([min_score, min_margin]).all()
        or not -1 <= min_score <= 1
        or not 0 <= min_margin <= 2
        or type(peak_radius) is not int
        or peak_radius < 0
    ):
        raise ValueError("Invalid correlation thresholds or peak radius")
    if float(np.std(template.astype(float))) < 1e-6:
        # Lecture 002 p. 144: homogeneous areas cannot supply distinctive matches.
        # OpenCV returns 1 for constant templates; explicitly reject that case.
        return CorrelationPeak(None, None, None, "textureless_template")
    # pp. 141/143: normalized correlation tolerates positive gain and offset.
    scores = cv2.matchTemplate(search, template, cv2.TM_CCOEFF_NORMED).astype(float)
    h, w = template.shape

    def window_sum(array):
        integral = cv2.integral(array.astype(float))
        return integral[h:, w:] - integral[:-h, w:] - integral[h:, :-w] + integral[:-h, :-w]

    sums = window_sum(search)
    variance = np.maximum(
        0, window_sum(search.astype(float) ** 2) / (h * w) - (sums / (h * w)) ** 2
    )
    scores[variance < 1e-12] = -np.inf
    if not np.isfinite(scores).any():
        return CorrelationPeak(None, None, None, "textureless_search")
    y, x = np.unravel_index(np.argmax(scores), scores.shape)
    score = float(np.clip(scores[y, x], -1, 1))
    competitors = scores.copy()
    competitors[
        max(0, y - peak_radius) : y + peak_radius + 1, max(0, x - peak_radius) : x + peak_radius + 1
    ] = -np.inf
    second = float(competitors.max())
    margin = score - second if np.isfinite(second) else None
    reason = (
        "low_correlation"
        if score < min_score
        else ("ambiguous_peak" if margin is not None and margin < min_margin else None)
    )
    return CorrelationPeak((int(x), int(y)), score, margin, reason)


def prepare_correlation_image(image):
    """Own one validated float32 image for reuse throughout a patch-search batch."""
    image = np.array(image, dtype=np.float32, copy=True)
    if image.ndim != 2 or not image.size or not np.isfinite(image).all():
        raise ValueError("Images must be finite nonempty grayscale arrays")
    image.setflags(write=False)
    return image


def correlate_local_point(first, second, first_pixel, second_pixel, **kwargs):
    """Validate images once for a single search; batched callers reuse prepared images."""
    return correlate_prepared_local_point(
        prepare_correlation_image(first),
        prepare_correlation_image(second),
        first_pixel,
        second_pixel,
        **kwargs,
    )


def correlate_prepared_local_point(
    first,
    second,
    first_pixel,
    second_pixel,
    *,
    homography=None,
    patch_size=21,
    search_radius=5,
    min_score=0.7,
    min_margin=0.05,
):
    """Correlate around an approximate pair match; return a proposed second pixel.

    H maps first image to second. None assumes local translation; no geometry is
    fitted by this function. It never overwrites the caller's feature locations.
    """
    if type(patch_size) is not int or patch_size < 3 or patch_size % 2 != 1:
        raise ValueError("patch_size must be an odd integer >=3")
    if type(search_radius) is not int or search_radius < 1:
        raise ValueError("search_radius must be a positive integer")
    # Only accept the read-only images produced by prepare_correlation_image.
    # There is deliberately no whole-image scan/copy inside this per-point loop.
    if any(a.ndim != 2 or a.dtype != np.float32 or a.flags.writeable for a in (first, second)):
        raise ValueError("Patch batches require prepared read-only float32 images")
    a, b = np.asarray(first_pixel, float), np.asarray(second_pixel, float)
    if a.shape != (2,) or b.shape != (2,) or not np.isfinite([a, b]).all():
        raise ValueError("Points must be finite 2D pixels")
    H = (
        np.array([[1, 0, b[0] - a[0]], [0, 1, b[1] - a[1]], [0, 0, 1]], float)
        if homography is None
        else np.asarray(homography, float)
    )
    if H.shape != (3, 3) or not np.isfinite(H).all() or np.linalg.matrix_rank(H) < 3:
        raise ValueError("Homography must be finite nonsingular 3x3")
    half = patch_size // 2
    if np.any(a - half < 0) or np.any(a + half >= np.array(first.shape[::-1])):
        return {"accepted": False, "reason": "template_outside_image"}
    origin = np.linalg.solve(H, np.r_[b, 1])
    if abs(origin[2]) < 1e-12:
        return {"accepted": False, "reason": "search_at_infinity"}
    origin = origin[:2] / origin[2]
    size = patch_size + 2 * search_radius
    x, y = np.meshgrid(
        np.arange(size) - size // 2 + origin[0], np.arange(size) - size // 2 + origin[1]
    )
    mapped = np.c_[x.ravel(), y.ravel(), np.ones(x.size)] @ H.T
    if np.any(np.abs(mapped[:, 2]) < 1e-12):
        return {"accepted": False, "reason": "search_crosses_horizon"}
    pixels = mapped[:, :2] / mapped[:, 2:]
    if np.any(pixels < 0) or np.any(pixels >= np.array(second.shape[::-1]) - 1):
        return {"accepted": False, "reason": "search_outside_image"}
    search = cv2.remap(
        second,
        pixels[:, 0].reshape(size, size).astype(np.float32),
        pixels[:, 1].reshape(size, size).astype(np.float32),
        cv2.INTER_LINEAR,
    )
    template = cv2.getRectSubPix(first, (patch_size, patch_size), tuple(a))
    peak = correlate_patch(template, search, min_score=min_score, min_margin=min_margin)
    result = {
        "accepted": peak.rejection_reason is None,
        "reason": peak.rejection_reason,
        "score": peak.score,
        "peak_margin": peak.peak_margin,
        "candidates_examined": (2 * search_radius + 1) ** 2,
    }
    if peak.top_left is not None:
        source = origin + np.array(peak.top_left) - search_radius
        target = H @ np.r_[source, 1]
        target = target[:2] / target[2]
        result.update(
            second_pixel_refined=target.tolist(),
            refinement_shift_px=float(np.linalg.norm(target - b)),
        )
    return result


def correlate_pair_matches(first, second, first_points, second_points, *, support_mask=None):
    """Independent pairwise checks, with a held-out local plane for each point.

    Neighbor support comes from this pair only. The query correspondence is
    excluded from its own homography fit. A missing local model means unverified,
    not a proof that the pair match is false. Nonplanar neighborhoods can fail.
    """
    a, b = np.asarray(first_points, float), np.asarray(second_points, float)
    if a.ndim != 2 or a.shape[1] != 2 or a.shape != b.shape or not np.isfinite([a, b]).all():
        raise ValueError("Pair coordinates must be finite aligned Nx2 arrays")
    support = np.ones(len(a), bool) if support_mask is None else np.asarray(support_mask, bool)
    if support.shape != (len(a),):
        raise ValueError("Support mask must align with points")
    results = []
    for point, other in zip(a, b, strict=True):
        distance = np.linalg.norm(a - point, axis=1)
        neighbors = np.flatnonzero(support & (distance > 4) & (distance < 160))
        neighbors = neighbors[np.argsort(distance[neighbors])[:30]]
        if len(neighbors) < 6:
            results.append({"accepted": False, "reason": "insufficient_local_support"})
            continue
        cv2.setRNGSeed(0)
        H, inliers = cv2.findHomography(
            a[neighbors], b[neighbors], cv2.RANSAC, 2.5, maxIters=1000, confidence=0.99
        )
        count = 0 if inliers is None else int(inliers.sum())
        if H is None or not np.isfinite(H).all() or np.linalg.matrix_rank(H) < 3 or count < 6:
            results.append({"accepted": False, "reason": "local_model_unavailable"})
            continue
        result = correlate_local_point(first, second, point, other, homography=H)
        result["local_model_support_count"] = count
        result["query_excluded_from_model"] = True
        if result["accepted"] and result.get("refinement_shift_px", float("inf")) > 3:
            result.update(accepted=False, reason="large_refinement_shift")
        results.append(result)
    return results
