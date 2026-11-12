"""Measured capture timing; overlapping a JPEG request does not establish causality."""

from distance_measuring.dataset.schema import CLOCK_DOMAIN

from .prototype_diagnostics import statistics as distribution


def capture_timing_diagnostics(session, *, max_preview_gap_s):
    frames = [f for f in session.tracking.frames if f.clock_domain == CLOCK_DOMAIN]
    gaps = [
        (a.timestamp_ns, b.timestamp_ns, (b.timestamp_ns - a.timestamp_ns) / 1e9)
        for a, b in zip(frames, frames[1:], strict=False)
    ]
    windows, photos = [], []
    for image in session.images:
        request, callback = image.request_timestamp_ns, image.callback_timestamp_ns
        exposure = (
            image.timestamp_ns
            if image.clock_domain == CLOCK_DOMAIN
            and image.timestamp_source == "camera_frame"
            and image.metadata.sensor_timestamp_ns == image.timestamp_ns
            and image.metadata.sensor_timestamp_clock_domain == CLOCK_DOMAIN
            else None
        )
        overlapping = []
        if request is not None and callback is not None:
            windows.append((request, callback))
            overlapping = [v for a, b, v in gaps if a <= callback and b >= request]
        photos.append(
            {
                "image_id": image.index,
                "request_timestamp_ns": request,
                "exposure_timestamp_ns": exposure,
                "callback_timestamp_ns": callback,
                "request_to_exposure_s": (exposure - request) / 1e9
                if exposure is not None and request is not None
                else None,
                "request_to_callback_s": (callback - request) / 1e9
                if callback is not None and request is not None
                else None,
                "overlapping_preview_gaps_s": distribution(overlapping),
                "overlapping_large_gap_count": sum(v > max_preview_gap_s for v in overlapping),
            }
        )
    requests = [p["request_timestamp_ns"] for p in photos if p["request_timestamp_ns"] is not None]
    exposures = [
        p["exposure_timestamp_ns"] for p in photos if p["exposure_timestamp_ns"] is not None
    ]
    records = [r.raw for r in session.tracking.records if r.type == "capture"]
    live = [f.raw.get("diagnostics", {}) for f in session.tracking.frames]
    return {
        "preview_gaps_s": distribution([v for _, _, v in gaps]),
        "preview_gaps_during_jpeg_requests_s": distribution(
            [v for a, b, v in gaps if any(a <= end and b >= start for start, end in windows)]
        ),
        "preview_gaps_outside_jpeg_requests_s": distribution(
            [v for a, b, v in gaps if not any(a <= end and b >= start for start, end in windows)]
        ),
        "large_gap_threshold_s": max_preview_gap_s,
        "large_gap_count": sum(v > max_preview_gap_s for _, _, v in gaps),
        "request_intervals_s": distribution(
            [(b - a) / 1e9 for a, b in zip(requests, requests[1:], strict=False)]
        ),
        "exposure_intervals_s": distribution(
            [(b - a) / 1e9 for a, b in zip(exposures, exposures[1:], strict=False)]
        ),
        "request_to_exposure_s": distribution([p["request_to_exposure_s"] for p in photos]),
        "request_to_callback_s": distribution([p["request_to_callback_s"] for p in photos]),
        "capture_skip_reasons": {
            reason: sum(r.get("missed_tick_count", 1) for r in records if r.get("reason") == reason)
            for reason in {r["reason"] for r in records if r["status"] == "skipped"}
        },
        "stored_live_diagnostics": {
            key: distribution([d.get(key) for d in live])
            for key in (
                "active_tracks",
                "surviving_tracks",
                "median_fb_error_px",
                "median_residual_parallax_px",
                "flow_runtime_ms",
                "png_export_runtime_ms",
                "writer_queue_wait_ms",
            )
        },
        "per_still": photos,
        "interpretation": (
            "Gaps are measured on the common camera clock. Request-window overlap is "
            "a timing association, not proof of a JPEG-induced stall. Unknown camera "
            "clocks are excluded; live diagnostics are recorded device estimates."
        ),
    }
