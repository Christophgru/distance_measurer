"""Spatial Shi–Tomasi seeds and temporal tracks in ideal working pixels."""

from dataclasses import dataclass, replace
from time import perf_counter

import cv2
import numpy as np

from .features import SiftBackend
from .matching import match_descriptors


@dataclass(frozen=True)
class TrackingConfig:
    max_points: int = 800
    grid_rows: int = 6
    grid_cols: int = 8
    min_distance_px: float = 12.0
    quality_level: float = 0.01
    lk_window_px: int = 31
    lk_max_level: int = 4
    fb_threshold_px: float = 1.5
    replenish_ratio: float = 0.75
    initialize_motion: bool = False
    motion_max_features: int = 600
    motion_max_image_size: int = 640
    motion_retry_ratio: float = 0.25

    def __post_init__(self):
        for name in ("max_points", "grid_rows", "grid_cols"):
            if type(getattr(self, name)) is not int or getattr(self, name) < 1:
                raise ValueError(f"{name} must be a positive integer")
        if not np.isfinite(self.min_distance_px) or self.min_distance_px < 1:
            raise ValueError("min_distance_px must be finite and at least 1")
        if not np.isfinite(self.quality_level) or not 0 < self.quality_level <= 1:
            raise ValueError("quality_level must be finite and in (0, 1]")
        if (
            type(self.lk_window_px) is not int
            or self.lk_window_px < 3
            or self.lk_window_px % 2 != 1
        ):
            raise ValueError("lk_window_px must be an odd integer of at least 3")
        if type(self.lk_max_level) is not int or self.lk_max_level < 0:
            raise ValueError("lk_max_level must be a nonnegative integer")
        if not np.isfinite(self.fb_threshold_px) or self.fb_threshold_px <= 0:
            raise ValueError("fb_threshold_px must be positive and finite")
        if not np.isfinite(self.replenish_ratio) or not 0 < self.replenish_ratio <= 1:
            raise ValueError("replenish_ratio must be finite and in (0, 1]")
        if type(self.initialize_motion) is not bool:
            raise ValueError("initialize_motion must be boolean")
        if type(self.motion_max_features) is not int or self.motion_max_features < 8:
            raise ValueError("motion_max_features must be an integer of at least 8")
        if type(self.motion_max_image_size) is not int or self.motion_max_image_size < 32:
            raise ValueError("motion_max_image_size must be an integer of at least 32")
        if not np.isfinite(self.motion_retry_ratio) or not 0 < self.motion_retry_ratio <= 1:
            raise ValueError("motion_retry_ratio must be finite and in (0, 1]")


DEFAULT_CONFIG = TrackingConfig()


def _gray(image):
    if not isinstance(image, np.ndarray) or image.dtype != np.uint8 or image.size == 0:
        raise ValueError("Tracking requires nonempty uint8 grayscale or BGR images")
    if image.ndim == 2:
        return image
    if image.ndim == 3 and image.shape[2] == 3:
        return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    raise ValueError("Tracking requires nonempty uint8 grayscale or BGR images")


def _points(values):
    points = np.asarray(values, dtype=float)
    if points.ndim != 2 or points.shape[1] != 2 or not np.isfinite(points).all():
        raise ValueError("Points must be a finite Nx2 array")
    return points


def _in_bounds(points, shape):
    return (
        np.isfinite(points).all(axis=1)
        & (points >= 0).all(axis=1)
        & (points < np.array(shape[:2][::-1])).all(axis=1)
    )


def grid_cell_ids(points, image_shape, config: TrackingConfig):
    """Pixel-aligned tile IDs shared by selection, coverage and track ranking."""
    points = _points(points)
    height, width = image_shape[:2]
    if config.grid_rows > height or config.grid_cols > width:
        raise ValueError("Grid cannot have more rows/columns than image pixels")
    if not _in_bounds(points, image_shape).all():
        raise ValueError("Active points must be inside the image")
    xs = np.linspace(0, width, config.grid_cols + 1, dtype=int)
    ys = np.linspace(0, height, config.grid_rows + 1, dtype=int)
    cells = np.searchsorted(ys[1:], points[:, 1], side="right") * config.grid_cols
    cells += np.searchsorted(xs[1:], points[:, 0], side="right")
    return cells


def grid_occupancy(points, image_shape, config: TrackingConfig):
    cells = grid_cell_ids(points, image_shape, config)
    return np.bincount(cells, minlength=config.grid_rows * config.grid_cols)


@dataclass(frozen=True)
class TrackingSeeds:
    points: np.ndarray
    response: np.ndarray
    visited_cells: tuple[int, ...]
    runtime_s: float


def select_track_points(image, config: TrackingConfig = DEFAULT_CONFIG, *, active_points=None):
    """Fill tile quotas, respecting existing points and spacing across tile edges.

    Returns NEW seeds only. Quality is OpenCV's Shi–Tomasi minimum eigenvalue.
    Detection runs on under-populated tile ROIs, never on a masked full frame.
    """
    start = perf_counter()
    gray = _gray(image)
    active = np.empty((0, 2)) if active_points is None else _points(active_points)
    counts = grid_occupancy(active, gray.shape, config)
    cells = config.grid_rows * config.grid_cols
    quotas = np.full(cells, config.max_points // cells, dtype=int)
    remainder = config.max_points % cells
    if remainder:
        # Spread small budgets and remainder slots throughout the image.
        quotas[np.linspace(0, cells - 1, remainder, dtype=int)] += 1
    height, width = gray.shape
    xs = np.linspace(0, width, config.grid_cols + 1, dtype=int)
    ys = np.linspace(0, height, config.grid_rows + 1, dtype=int)
    mask = np.full(gray.shape, 255, np.uint8)
    radius = int(np.ceil(config.min_distance_px))
    for pixel in active:
        cv2.circle(mask, tuple(np.rint(pixel).astype(int)), radius, 0, -1)
    selected, responses, visited = [], [], []
    occupied = np.empty((max(config.max_points, len(active)), 2), dtype=float)
    occupied[: len(active)] = active
    for cell in np.flatnonzero(counts < quotas):
        if len(active) + len(selected) >= config.max_points:
            break
        row, col = divmod(int(cell), config.grid_cols)
        x, y, right, bottom = xs[col], ys[row], xs[col + 1], ys[row + 1]
        roi_mask = mask[y:bottom, x:right]
        if min(bottom - y, right - x) < 3 or not roi_mask.any():
            continue
        visited.append(int(cell))
        corners, quality = cv2.goodFeaturesToTrackWithQuality(
            gray[y:bottom, x:right],
            maxCorners=int(quotas[cell] - counts[cell]),
            qualityLevel=config.quality_level,
            minDistance=config.min_distance_px,
            mask=roi_mask,
            blockSize=3,
            gradientSize=3,
        )
        if corners is None:
            continue
        candidates = corners.reshape(-1, 2).astype(float) + [x, y]
        quality = quality.ravel()
        order = np.lexsort((candidates[:, 0], candidates[:, 1], -quality))
        for index in order:
            pixel = candidates[index]
            existing = occupied[: len(active) + len(selected)]
            if len(existing) and np.any(
                np.sum((existing - pixel) ** 2, axis=1) < config.min_distance_px**2
            ):
                continue
            if len(active) + len(selected) >= config.max_points:
                break
            occupied[len(active) + len(selected)] = pixel
            selected.append(pixel)
            responses.append(float(quality[index]))
            cv2.circle(mask, tuple(np.rint(pixel).astype(int)), radius, 0, -1)
    return TrackingSeeds(
        np.asarray(selected, dtype=float).reshape(-1, 2),
        np.asarray(responses, dtype=float),
        tuple(visited),
        perf_counter() - start,
    )


@dataclass(frozen=True)
class FlowResult:
    points: np.ndarray
    forward_status: np.ndarray
    lk_error: np.ndarray
    backward_status: np.ndarray
    forward_backward_error_px: np.ndarray
    accepted: np.ndarray
    rejection_reason: tuple[str | None, ...]
    runtime_s: float
    forward_runtime_s: float
    backward_runtime_s: float
    motion_initialization: dict | None = None


def _map_homography(matrix, points):
    homogeneous = np.c_[points, np.ones(len(points))] @ matrix.T
    denominator = homogeneous[:, 2:]
    mapped = np.divide(
        homogeneous[:, :2],
        denominator,
        out=np.full_like(points, np.nan, dtype=float),
        where=np.abs(denominator) > 1e-10,
    )
    return mapped


def _motion_initialization(first, second, config):
    """Optional coarse image warp for widely spaced stills; reuse bounded SIFT.

    This is an explicit descriptor cost, not part of the default temporal path.
    The estimated 2D warp only initializes LK; it is not a camera pose or a track.
    """
    start = perf_counter()
    height, width = first.shape
    scale = min(1.0, config.motion_max_image_size / max(height, width))
    size = (max(1, round(width * scale)), max(1, round(height * scale)))
    sx, sy = size[0] / width, size[1] / height
    # Same OpenCV pixel-center convention used by working_camera.
    affine = np.array([[sx, 0, (sx - 1) / 2], [0, sy, (sy - 1) / 2], [0, 0, 1]])
    backend = SiftBackend(max_features=config.motion_max_features)
    a, b = [
        backend.extract(cv2.resize(im, size, interpolation=cv2.INTER_AREA))
        for im in (first, second)
    ]
    matches = match_descriptors(a.descriptors, b.descriptors, ratio=0.75, mutual=True)
    matrix, mask = None, None
    if len(matches) >= 8:
        cv2.setRNGSeed(0)
        matrix, mask = cv2.findHomography(
            a.points[matches.indices[:, 0]],
            b.points[matches.indices[:, 1]],
            cv2.RANSAC,
            3.0,
            maxIters=2000,
            confidence=0.995,
        )
    inliers = int(mask.sum()) if mask is not None else 0
    if matrix is not None and (
        inliers < 8 or not np.isfinite(matrix).all() or abs(np.linalg.det(matrix)) < 1e-10
    ):
        matrix = None
    if matrix is not None:
        matrix = np.linalg.solve(affine, matrix @ affine)
    return matrix, {
        "success": matrix is not None,
        "runtime_s": perf_counter() - start,
        "feature_counts": [len(a), len(b)],
        "retained_match_count": len(matches),
        "homography_inlier_count": inliers,
        "descriptor_distance_upper_bound": 2 * len(a) * len(b),
        "working_to_coarse_affine": affine.tolist(),
        "H_second_first": matrix.tolist() if matrix is not None else None,
    }


def _track_points(first, second, points, config, initial_warp=None):
    """Forward/backward LK; masks/errors align with every input row.

    LK error is mean L1 patch difference; round-trip error is Euclidean pixels.
    Reverse flow uses only valid, in-bounds forward destinations.
    """
    start = perf_counter()
    first, second = _gray(first), _gray(second)
    points = _points(points)
    if first.shape != second.shape:
        raise ValueError("Tracking images must have equal dimensions")
    if not _in_bounds(points, first.shape).all():
        raise ValueError("Source points must be inside the image")
    source_points = points
    source_ok = np.ones(len(points), bool)
    if initial_warp is not None:
        source_points = _map_homography(initial_warp, points)
        source_ok = _in_bounds(source_points, first.shape)
        first = cv2.warpPerspective(first, initial_warp, first.shape[::-1])
    count = len(points)
    target, status, errors = (
        np.full((count, 2), np.nan),
        np.zeros(count, bool),
        np.full(count, np.nan),
    )
    options = dict(
        winSize=(config.lk_window_px, config.lk_window_px),
        # A coarse warp already removes large motion; avoid letting black warp
        # borders dominate the coarsest LK levels of the residual search.
        maxLevel=min(2, config.lk_max_level) if initial_warp is not None else config.lk_max_level,
        criteria=(cv2.TERM_CRITERIA_COUNT | cv2.TERM_CRITERIA_EPS, 30, 0.01),
    )
    forward_time, backward_time = 0.0, 0.0
    forward_rows = np.flatnonzero(source_ok)
    if len(forward_rows):
        forward_start = perf_counter()
        pixels, raw_status, raw_errors = cv2.calcOpticalFlowPyrLK(
            first,
            second,
            np.asarray(source_points[forward_rows], np.float32).reshape(-1, 1, 2),
            None,
            **options,
        )
        forward_time = perf_counter() - forward_start
        if pixels is not None and raw_status is not None:
            target[forward_rows] = pixels.reshape(-1, 2)
            status[forward_rows] = raw_status.ravel().astype(bool)
            if raw_errors is not None:
                # OpenCV does not define errors for failed rows.
                good_rows = forward_rows[status[forward_rows]]
                errors[good_rows] = raw_errors.ravel()[status[forward_rows]]
    target[~status] = np.nan  # Failed LK coordinates are undefined, too.
    inside = _in_bounds(target, second.shape)
    forward_ok = status & inside & np.isfinite(errors)
    reasons = [
        None
        if forward_ok[i]
        else "initialization_outside_image"
        if not source_ok[i]
        else "forward_failed"
        if not status[i] or not np.isfinite(errors[i]) or not np.isfinite(target[i]).all()
        else "outside_image"
        for i in range(count)
    ]
    backward_status, roundtrip = np.zeros(count, bool), np.full(count, np.nan)
    backward_points = np.full((count, 2), np.nan)
    rows = np.flatnonzero(forward_ok)
    if len(rows):
        backward_start = perf_counter()
        returned, raw_status, _ = cv2.calcOpticalFlowPyrLK(
            second,
            first,
            np.asarray(target[rows], np.float32).reshape(-1, 1, 2),
            None,
            **options,
        )
        backward_time = perf_counter() - backward_start
        if returned is not None and raw_status is not None:
            backward_points[rows] = returned.reshape(-1, 2)
            backward_status[rows] = raw_status.ravel().astype(bool)
    backward_inside = _in_bounds(backward_points, first.shape)
    if initial_warp is not None:
        backward_points = _map_homography(np.linalg.inv(initial_warp), backward_points)
    valid_reverse = backward_status & np.isfinite(backward_points).all(axis=1)
    roundtrip[valid_reverse] = np.linalg.norm(
        backward_points[valid_reverse] - points[valid_reverse], axis=1
    )
    backward_inside &= _in_bounds(backward_points, first.shape)
    accepted = forward_ok & valid_reverse & backward_inside & (roundtrip <= config.fb_threshold_px)
    for i in rows:
        reasons[i] = (
            "backward_failed"
            if not valid_reverse[i]
            else "backward_outside_image"
            if not backward_inside[i]
            else "forward_backward_inconsistent"
            if not accepted[i]
            else None
        )
    return FlowResult(
        target,
        status,
        errors,
        backward_status,
        roundtrip,
        accepted,
        tuple(reasons),
        perf_counter() - start,
        forward_time,
        backward_time,
    )


def track_points(first, second, points, config: TrackingConfig = DEFAULT_CONFIG):
    """Pure temporal LK, with opt-in bounded descriptor initialization on failure.

    Guided retries warp the source image/points, then perform two LK passes.
    Returned pixels map back before measuring round-trip source error.
    """
    start = perf_counter()
    result = _track_points(first, second, points, config)
    required = min(
        len(result.points), max(8, int(np.ceil(len(result.points) * config.motion_retry_ratio)))
    )
    if not config.initialize_motion or result.accepted.sum() >= required:
        return result
    warp, diagnostics = _motion_initialization(_gray(first), _gray(second), config)
    diagnostics["initial_lk_accepted_count"] = int(result.accepted.sum())
    diagnostics["guided_retry_selected"] = False
    forward_time, backward_time = result.forward_runtime_s, result.backward_runtime_s
    if warp is not None:
        retry = _track_points(first, second, points, config, warp)
        forward_time += retry.forward_runtime_s
        backward_time += retry.backward_runtime_s
        if retry.accepted.sum() > result.accepted.sum():
            result = retry
            diagnostics["guided_retry_selected"] = True
    return replace(
        result,
        runtime_s=perf_counter() - start,
        forward_runtime_s=forward_time,
        backward_runtime_s=backward_time,
        motion_initialization=diagnostics,
    )


@dataclass(frozen=True)
class TrackObservation:
    image_id: int
    track_id: int
    pixel: tuple[float, float] | None
    age: int
    response: float
    status: str
    lk_error: float | None = None
    forward_backward_error_px: float | None = None


@dataclass(frozen=True)
class TrackingFrame:
    image_id: int
    observations: tuple[TrackObservation, ...]
    flow: FlowResult | None
    selection: TrackingSeeds | None


class TemporalTracker:
    """Keep only the preceding image/active observations; track IDs never restart."""

    def __init__(self, config: TrackingConfig = DEFAULT_CONFIG):
        self.config = config
        self.active: tuple[TrackObservation, ...] = ()
        self.next_track_id = 0
        self.previous_image = None
        self.previous_id = None

    def process(self, image, image_id: int) -> TrackingFrame:
        if (
            type(image_id) is not int
            or image_id < 0
            or (self.previous_id is not None and image_id <= self.previous_id)
        ):
            raise ValueError("Image IDs must be nonnegative and strictly increasing")
        gray = _gray(image)
        if self.previous_image is not None and gray.shape != self.previous_image.shape:
            raise ValueError("Tracking images must have equal dimensions")
        observations, active, flow, selection = [], [], None, None
        if self.previous_image is not None:
            points = np.asarray([o.pixel for o in self.active], dtype=float).reshape(-1, 2)
            flow = track_points(self.previous_image, gray, points, self.config)
            for i, old in enumerate(self.active):
                pixel = tuple(flow.points[i]) if np.isfinite(flow.points[i]).all() else None
                observation = TrackObservation(
                    image_id,
                    old.track_id,
                    pixel,
                    old.age + 1,
                    old.response,
                    "tracked" if flow.accepted[i] else flow.rejection_reason[i],
                    float(flow.lk_error[i]) if np.isfinite(flow.lk_error[i]) else None,
                    float(flow.forward_backward_error_px[i])
                    if np.isfinite(flow.forward_backward_error_px[i])
                    else None,
                )
                observations.append(observation)
                if flow.accepted[i]:
                    active.append(observation)
        if (
            self.previous_image is None
            or len(active) < self.config.max_points * self.config.replenish_ratio
        ):
            selection = select_track_points(
                gray,
                self.config,
                active_points=np.asarray([o.pixel for o in active], dtype=float).reshape(-1, 2),
            )
            for pixel, response in zip(selection.points, selection.response, strict=True):
                observation = TrackObservation(
                    image_id, self.next_track_id, tuple(pixel), 1, float(response), "seeded"
                )
                self.next_track_id += 1
                observations.append(observation)
                active.append(observation)
        self.previous_image, self.previous_id = gray.copy(), image_id
        self.active = tuple(active)
        return TrackingFrame(image_id, tuple(observations), flow, selection)
