"""Consistency checks and inspection for the optional P9 tracking recording."""

from bisect import bisect_left, bisect_right
from collections import Counter
from itertools import pairwise

import cv2
import numpy as np

from .schema import CLOCK_DOMAIN, DatasetFormatError, capture_path


def validate_tracking(session, report, max_sensor_gap_s):
    tracking = session.tracking
    if tracking is None:
        return
    frames, records = tracking.frames, tracking.records
    if tracking.session_id != session.metadata.session_id:
        report.error("tracking_session_id", "tracking.json belongs to a different session")
    if tracking.state in ("recording", "ending"):
        report.error("tracking_unfinished", f"tracking.json is still {tracking.state}")
    elif tracking.state in ("interrupted", "unavailable"):
        report.warn(
            "tracking_" + tracking.state, f"Tracking {tracking.state}: {tracking.failure_reason}"
        )
    if tracking.state == "unavailable" and frames:
        report.error("tracking_state", "Unavailable tracking cannot contain saved frames")
    if tracking.state == "complete" and tracking.failure_reason is not None:
        report.error("tracking_state", "Complete tracking cannot have a failure reason")
    counts = Counter(r.type for r in records)
    dropped = sum(r.raw["count"] for r in records if r.type == "drop")
    actual = {
        "frames": len(frames),
        "dropped_frames": dropped,
        "resets": counts["reset"],
        "capture_events": counts["capture"],
    }
    if actual != tracking.counts:
        report.error(
            "tracking_counts",
            f"tracking.json counts {tracking.counts} disagree with records {actual}",
        )
    if [r.sequence for r in records] != list(range(len(records))):
        report.error(
            "tracking_sequence", "Tracking append sequences must be contiguous starting at zero"
        )
    if any(b.recorded_timestamp_ns < a.recorded_timestamp_ns for a, b in pairwise(records)):
        report.error("tracking_record_order", "Tracking append timestamps must be nondecreasing")
    if session.start_ns is not None and any(
        r.recorded_timestamp_ns < session.start_ns for r in records
    ):
        report.error("tracking_record_time", "Tracking record append time precedes session start")
    if any(b.frame_id <= a.frame_id for a, b in pairwise(frames)):
        report.error("tracking_frame_order", "Saved frame IDs must increase in append order")
    if any(
        b.clock_domain == a.clock_domain and b.timestamp_ns <= a.timestamp_ns
        for a, b in pairwise(frames)
    ):
        report.error(
            "tracking_camera_order", "Camera timestamps must increase within a clock segment"
        )
    if any(b.received_timestamp_ns < a.received_timestamp_ns for a, b in pairwise(frames)):
        report.error(
            "tracking_receive_order", "Saved frame reception timestamps must be nondecreasing"
        )
    intervals = [(f.frame_id, f.frame_id + 1) for f in frames]
    intervals += [
        (r.raw["first_frame_id"], r.raw["first_frame_id"] + r.raw["count"])
        for r in records
        if r.type == "drop"
    ]
    expected = 0
    for first, last in sorted(intervals):
        if first != expected:
            report.error(
                "tracking_frame_ids",
                "Offered frame IDs must be covered exactly once by saved frames "
                "or explicit drops, starting at zero",
            )
            break
        expected = last
    offered = len(frames) + dropped
    if any(r.raw["next_frame_id"] > offered for r in records if r.type == "reset"):
        report.error("tracking_reset_frame", "Reset next_frame_id exceeds offered frame count")
    if dropped:
        report.warn(
            "tracking_dropped_frames",
            f"Writer reported {dropped} explicitly dropped frames; "
            "upstream acquisition losses are not counted",
        )
    if not frames and tracking.state != "unavailable":
        report.warn("tracking_no_frames", "Tracking sidecar contains no saved frames")
    _validate_frames(session, report, max_sensor_gap_s)
    _validate_captures(session, report)


def _inside(session, timestamp):
    return (session.start_ns is None or timestamp > session.start_ns) and (
        session.end_ns is None or timestamp < session.end_ns
    )


def _validate_frames(session, report, max_gap_s):
    frames = session.tracking.frames
    filenames = Counter(f.filename for f in frames)
    if any(v > 1 for v in filenames.values()):
        report.error("tracking_duplicate_filename", "Multiple tracking frames name the same file")
    paths, unknown_clocks, unavailable_k, uncovered, unavailable_mount = set(), 0, 0, 0, 0
    gyro = sorted(s.timestamp_ns for s in session.sensor_streams.get("gyroscope", ()))
    for frame in frames:
        label = f"tracking frame {frame.frame_id} ({frame.filename})"
        if not _inside(session, frame.received_timestamp_ns):
            report.error(
                "tracking_outside_timeline", f"{label}: received outside the active timeline"
            )
        if frame.received_timestamp_ns > frame.recorded_timestamp_ns:
            report.error("tracking_receive_time", f"{label}: received after append time")
        if frame.metadata.sensor_timestamp_ns not in (None, frame.timestamp_ns):
            report.error(
                "tracking_sensor_timestamp",
                f"{label}: metadata sensor time disagrees with frame time",
            )
        if frame.metadata.sensor_timestamp_clock_domain not in (None, frame.clock_domain):
            report.error(
                "tracking_sensor_clock",
                f"{label}: metadata sensor clock disagrees with frame clock",
            )
        if frame.clock_domain == CLOCK_DOMAIN:
            if session.metadata.clock_domain != CLOCK_DOMAIN:
                report.error(
                    "tracking_clock_domain",
                    f"{label}: REALTIME frame requires the declared session clock",
                )
            if not _inside(session, frame.timestamp_ns):
                report.error(
                    "tracking_outside_timeline",
                    f"{label}: camera frame outside the active timeline",
                )
            if frame.timestamp_ns > frame.received_timestamp_ns:
                report.error("tracking_camera_time", f"{label}: camera exposure follows reception")
            before, after = (
                bisect_left(gyro, frame.timestamp_ns) - 1,
                bisect_right(gyro, frame.timestamp_ns),
            )
            if (
                not session.metadata.sensors.get("gyroscope", False)
                or before < 0
                or after >= len(gyro)
                or (gyro[after] - gyro[before]) / 1e9 > max_gap_s
            ):
                uncovered += 1
        else:
            unknown_clocks += 1
        unavailable_k += frame.intrinsics is None
        unavailable_mount += (
            frame.metadata.lens_facing != 1
            or frame.metadata.target_rotation != 0
            or frame.metadata.is_flipped is not False
            or frame.metadata.sensor_orientation_degrees is None
        )
        try:
            path = capture_path(session.path, frame.filename)
            if path in paths:
                report.error(
                    "tracking_duplicate_path", f"{label}: resolves to another frame's file"
                )
            paths.add(path)
            with path.open("rb") as source:
                signature = source.read(8)
            pixels = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
            if (
                signature != b"\x89PNG\r\n\x1a\n"
                or pixels is None
                or pixels.ndim != 2
                or pixels.dtype != np.uint8
            ):
                report.error(
                    "tracking_frame_decode", f"{label}: expected a decodable 8-bit grayscale PNG"
                )
            elif pixels.shape != (frame.metadata.height, frame.metadata.width):
                report.error(
                    "tracking_frame_dimensions",
                    f"{label}: decoded dimensions disagree with metadata",
                )
        except (OSError, DatasetFormatError, cv2.error) as error:
            report.error("tracking_frame_file", f"{label}: {error}")
    if unknown_clocks:
        report.warn(
            "tracking_unknown_clock",
            f"{unknown_clocks} frames have unknown camera clocks; "
            "gyro/still time association is unavailable",
        )
    if unavailable_k:
        report.warn(
            "tracking_intrinsics_unavailable",
            f"{unavailable_k} frames have explicitly unavailable intrinsics",
        )
    if uncovered:
        report.warn(
            "tracking_gyro_coverage",
            f"{uncovered} REALTIME frames lack strict gyro bracketing within {max_gap_s:g} s; "
            "gyro compensation is unavailable there",
        )
    if unavailable_mount:
        report.warn(
            "tracking_mount_unavailable",
            f"{unavailable_mount} frames lack the unmirrored back-camera/native-rotation "
            "metadata required by the current nominal gyro mounting model",
        )
    resets = [r.raw["next_frame_id"] for r in session.tracking.records if r.type == "reset"]
    for first, second in pairwise(frames):
        if _geometry_key(first) != _geometry_key(second) and not any(
            first.frame_id < i <= second.frame_id for i in resets
        ):
            report.warn(
                "tracking_geometry_changed",
                f"Frame {second.frame_id}: camera/layout/clock/intrinsics changed without "
                "an explicit reset; do not continue its previous track segment",
            )


def _geometry_key(frame):
    return (
        frame.buffer_size,
        frame.buffer_crop_region,
        frame.sample_step,
        frame.metadata.camera_id,
        frame.metadata.physical_camera_id,
        frame.metadata.rotation_degrees,
        frame.metadata.target_rotation,
        frame.metadata.is_flipped,
        frame.metadata.lens_facing,
        frame.metadata.sensor_orientation_degrees,
        frame.metadata.crop_region,
        frame.metadata.zoom_ratio,
        frame.metadata.focal_length_mm,
        frame.clock_domain,
        frame.intrinsics,
    )


def _validate_captures(session, report):
    requests = {e.raw["index"]: e.timestamp_ns for e in session.events if e.type == "image_request"}
    request_counts = Counter(e.raw["index"] for e in session.events if e.type == "image_request")
    seen, previous_tick = set(), None
    for row in session.tracking.records:
        if row.type != "capture":
            continue
        event = row.raw
        scheduled = event["scheduled_timestamp_ns"]
        if previous_tick is not None and scheduled <= previous_tick:
            report.error("tracking_capture_order", "Automatic capture ticks must strictly increase")
        previous_tick = scheduled
        if not _inside(session, scheduled) or scheduled > row.recorded_timestamp_ns:
            report.error(
                "tracking_capture_time",
                "Capture tick is outside the timeline or follows its append time",
            )
        if "missed_tick_count" in event:
            last = event["last_skipped_timestamp_ns"]
            if (
                last < scheduled
                or not _inside(session, last)
                or last > row.recorded_timestamp_ns
                or (event["missed_tick_count"] == 1 and last != scheduled)
            ):
                report.error(
                    "tracking_capture_range",
                    "Aggregated skipped ticks need an ordered range "
                    "inside the timeline before append time",
                )
            previous_tick = last
        if event["status"] == "requested":
            index, requested = event["image_index"], event["request_timestamp_ns"]
            if index in seen or request_counts[index] != 1 or requests.get(index) != requested:
                report.error(
                    "tracking_capture_request",
                    "Automatic capture must reference exactly one matching "
                    "image_request index/time",
                )
            seen.add(index)
            if (
                scheduled > requested
                or requested > row.recorded_timestamp_ns
                or not _inside(session, requested)
            ):
                report.error(
                    "tracking_capture_time",
                    "Automatic request must follow its tick and precede "
                    "its append time inside the timeline",
                )


def summarize_tracking(tracking):
    if tracking is None:
        return None
    frames = tracking.frames
    gaps = [
        (b.timestamp_ns - a.timestamp_ns) / 1e9
        for a, b in pairwise(frames)
        if a.clock_domain == b.clock_domain and b.timestamp_ns > a.timestamp_ns
    ]
    clocks = sorted({f.clock_domain for f in frames})
    span = (
        frames[-1].timestamp_ns - frames[0].timestamp_ns
        if len(frames) > 1 and len(clocks) == 1
        else 0
    )
    counts = Counter(r.type for r in tracking.records)
    return {
        "schema_version": tracking.schema_version,
        "state": tracking.state,
        "failure_reason": tracking.failure_reason,
        "settings": tracking.settings,
        "frame_count": len(frames),
        "dropped_frame_count": sum(r.raw["count"] for r in tracking.records if r.type == "drop"),
        "reset_count": counts["reset"],
        "capture_event_count": counts["capture"],
        "capture_status_counts": dict(
            Counter(r.raw["status"] for r in tracking.records if r.type == "capture")
        ),
        "automatic_skipped_tick_count": sum(
            r.raw.get("missed_tick_count", 1)
            for r in tracking.records
            if r.type == "capture" and r.raw["status"] == "skipped"
        ),
        "scheduler_missed_tick_count": sum(
            r.raw.get("missed_tick_count", 0) for r in tracking.records if r.type == "capture"
        ),
        "clock_domains": clocks,
        "unknown_clock_frame_count": sum(f.clock_domain != CLOCK_DOMAIN for f in frames),
        "intrinsics_available_frame_count": sum(f.intrinsics is not None for f in frames),
        "observed_camera_rate_hz": (len(frames) - 1) * 1e9 / span if span > 0 else None,
        "median_frame_gap_s": float(np.median(gaps)) if gaps else None,
        "max_frame_gap_s": max(gaps) if gaps else None,
        "first_frame_id": frames[0].frame_id if frames else None,
        "last_frame_id": frames[-1].frame_id if frames else None,
        "acquisition_loss_count": None,
        "note": "Rates cover saved frames only; keep-only-latest and throttling losses "
        "are not counted by explicit writer drops. Intrinsics/clock availability "
        "does not certify cross-stream pose alignment.",
    }
