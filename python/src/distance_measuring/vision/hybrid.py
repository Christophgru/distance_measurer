"""P9.3: verify actual JPEG pixels against both bracketing preview track sets."""

from dataclasses import dataclass
from time import perf_counter

import cv2
import numpy as np

from distance_measuring.camera import from_homogeneous, to_homogeneous
from distance_measuring.dataset.schema import CLOCK_DOMAIN
from distance_measuring.inertial.rotation import camera_ray_rotation, integrate_gyro_interval

from .pipeline import working_camera
from .tracking import DEFAULT_CONFIG, track_points


@dataclass(frozen=True)
class HybridConfig:
    max_preview_gap_s: float = 0.25
    cross_stream_agreement_px: float = 1.5
    min_still_correspondences: int = 8

    def __post_init__(self):
        for name in ("max_preview_gap_s", "cross_stream_agreement_px"):
            if not np.isfinite(getattr(self, name)) or getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive and finite")
        if type(self.min_still_correspondences) is not int or self.min_still_correspondences < 8:
            raise ValueError("Hybrid still support needs at least eight correspondences")


DEFAULT_HYBRID = HybridConfig()


@dataclass
class PreviewSnapshot:
    frame: object
    camera: object
    gray: np.ndarray
    observations: dict
    segment_id: int
    signature: tuple = ()


def rotation_between(session, source, target, *, max_gyro_gap_s=0.25):
    """Actual camera times only; reverse integration when the preview follows the JPEG."""
    start = perf_counter()
    if source.clock_domain != CLOCK_DOMAIN or target.clock_domain != CLOCK_DOMAIN:
        raise ValueError("Cross-stream camera timestamps must share the sensor clock")
    a, b = source.timestamp_ns, target.timestamp_ns
    if a == b:
        rotation = np.eye(3)  # Measured zero interval, not a missing-gyro substitute.
    else:
        interval = integrate_gyro_interval(session, min(a, b), max(a, b), max_gap_s=max_gyro_gap_s)
        rotation = interval.R_device_second_first if a < b else interval.R_device_second_first.T
    return camera_ray_rotation(rotation, source.metadata, target.metadata), perf_counter() - start


def associate_still(
    session,
    event,
    original_camera,
    working_affine,
    raw_gray,
    before,
    after,
    *,
    config=DEFAULT_HYBRID,
    tracking_config=DEFAULT_CONFIG,
    max_gyro_gap_s=0.25,
):
    """Return measured JPEG-working pixels, never preview-scaled stand-ins or poses."""
    start = perf_counter()
    report = {
        "image_id": event.index,
        "timestamp_ns": event.timestamp_ns,
        "status": "unsupported",
        "reason": None,
        "accepted_count": 0,
        "segment_id": None,
        "branches": [],
        "candidate_track_count": 0,
    }

    def reject(reason):
        report.update(reason=reason, runtime_s=perf_counter() - start)
        return {}, report

    if (
        event.timestamp_source != "camera_frame"
        or event.metadata.sensor_timestamp_ns != event.timestamp_ns
    ):
        return reject("still_exposure_timestamp_unavailable")
    if (
        event.clock_domain != CLOCK_DOMAIN
        or event.metadata.sensor_timestamp_clock_domain != CLOCK_DOMAIN
    ):
        return reject("still_camera_clock_unknown")
    if before is None or after is None:
        return reject("missing_bracketing_preview")
    report.update(
        bracket_frame_ids=[before.frame.frame_id, after.frame.frame_id],
        before_offset_s=(event.timestamp_ns - before.frame.timestamp_ns) / 1e9,
        after_offset_s=(after.frame.timestamp_ns - event.timestamp_ns) / 1e9,
    )
    if before.segment_id != after.segment_id:
        return reject("preview_segment_boundary")
    if not before.frame.timestamp_ns <= event.timestamp_ns <= after.frame.timestamp_ns:
        return reject("still_outside_preview_bracket")
    if (after.frame.timestamp_ns - before.frame.timestamp_ns) / 1e9 > config.max_preview_gap_s:
        return reject("preview_gap_exceeds_limit")
    report["segment_id"] = before.segment_id
    shared = sorted(before.observations.keys() & after.observations.keys())
    report["candidate_track_count"] = len(shared)
    if not shared:
        return reject("no_common_preview_tracks")
    target_size = before.gray.shape[::-1]
    if after.gray.shape != before.gray.shape:
        return reject("preview_layout_changed")
    jpeg_camera, analysis_affine = working_camera(original_camera, target_size)
    gray = cv2.resize(raw_gray, target_size, interpolation=cv2.INTER_AREA)
    if any(jpeg_camera.distortion):
        gray = jpeg_camera.undistort_image(gray)
    analysis_to_working = working_affine @ np.linalg.inv(analysis_affine)
    working_size = np.rint(
        np.asarray(original_camera.image_size) * np.diag(working_affine)[:2]
    ).astype(int)
    points, good, quality, lk_errors = [], [], [], []
    for snapshot in (before, after):
        source_pixels = np.asarray([snapshot.observations[t].pixel for t in shared], float)
        try:
            rotation, gyro_s = rotation_between(
                session, snapshot.frame, event, max_gyro_gap_s=max_gyro_gap_s
            )
            initial = jpeg_camera.K @ rotation @ np.linalg.inv(snapshot.camera.K)
            prior_reason = None
        except ValueError as error:
            initial, gyro_s, prior_reason = None, 0.0, str(error)
        flow = track_points(
            snapshot.gray, gray, source_pixels, tracking_config, initial_warp=initial
        )
        # Convert actual source round-trip vectors to a common intrinsic pixel
        # basis, then to the JPEG's working resolution for the existing FB gate.
        units = analysis_to_working @ jpeg_camera.K @ np.linalg.inv(snapshot.camera.K)
        source_working = from_homogeneous(to_homogeneous(source_pixels) @ units.T)
        backward_working = np.full_like(source_working, np.nan)
        valid_backward = np.isfinite(flow.backward_points).all(axis=1)
        backward_working[valid_backward] = from_homogeneous(
            to_homogeneous(flow.backward_points[valid_backward]) @ units.T
        )
        error = np.linalg.norm(backward_working - source_working, axis=1)
        good.append(flow.accepted & (error <= tracking_config.fb_threshold_px))
        quality.append(error)
        target = np.full_like(source_working, np.nan)
        finite = np.isfinite(flow.points).all(axis=1)
        target[finite] = from_homogeneous(
            to_homogeneous(flow.points[finite]) @ analysis_to_working.T
        )
        # A subpixel destination inside the analysis array can lie outside the
        # working array after the pixel-center affine expands the last pixel.
        inside_working = (
            np.isfinite(target).all(axis=1)
            & (target >= 0).all(axis=1)
            & (target < working_size).all(axis=1)
        )
        good[-1] &= inside_working
        points.append(target)
        lk_errors.append(flow.lk_error)
        report["branches"].append(
            {
                "frame_id": snapshot.frame.frame_id,
                "accepted_count": int(good[-1].sum()),
                "outside_working_image_count": int((finite & ~inside_working).sum()),
                "input_count": len(shared),
                "gyro_prior_available": initial is not None,
                "gyro_prior_reason": prior_reason,
                "gyro_runtime_s": gyro_s,
                "geometry_initialization": flow.geometry_initialization,
                "flow_runtime_s": flow.runtime_s,
                "forward_lk_runtime_s": flow.forward_runtime_s,
                "backward_lk_runtime_s": flow.backward_runtime_s,
                "motion_initialization": flow.motion_initialization,
            }
        )
    agreement = np.linalg.norm(points[0] - points[1], axis=1)
    accepted = good[0] & good[1] & (agreement <= config.cross_stream_agreement_px)
    result = {}
    for row in np.flatnonzero(accepted):
        t = shared[row]
        result[t] = {
            "pixel": (points[0][row] + points[1][row]) / 2,
            "forward_backward_error_px": float(max(quality[0][row], quality[1][row])),
            "lk_error": float(max(lk_errors[0][row], lk_errors[1][row])),
            "response": before.observations[t].response,
        }
    report.update(
        accepted_count=len(result),
        status="verified_2d" if len(result) >= config.min_still_correspondences else "unsupported",
        reason=None
        if len(result) >= config.min_still_correspondences
        else "insufficient_cross_stream_support",
        analysis_size=list(target_size),
        analysis_to_working_affine=analysis_to_working.tolist(),
        agreement_threshold_working_px=config.cross_stream_agreement_px,
        median_agreement_working_px=float(np.median(agreement[accepted]))
        if accepted.any()
        else None,
        runtime_s=perf_counter() - start,
    )
    return result, report
