"""Timeline and image consistency checks for the Android capture contract."""

from __future__ import annotations

import math
from bisect import bisect_left, bisect_right
from collections import Counter
from dataclasses import dataclass, field
from itertools import pairwise

import cv2

from .schema import CLOCK_DOMAIN, DatasetFormatError, Session


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    severity: str
    code: str
    message: str


class SessionValidationError(ValueError):
    """A readable recording violates the capture contract."""

    def __init__(self, report: ValidationReport):
        self.report = report
        details = "\n".join(f"  [{issue.code}] {issue.message}" for issue in report.errors)
        super().__init__(f"Session validation failed ({len(report.errors)} errors):\n{details}")


@dataclass(slots=True)
class ValidationReport:
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def errors(self) -> tuple[ValidationIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity == "error")

    @property
    def warnings(self) -> tuple[ValidationIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity == "warning")

    @property
    def ok(self) -> bool:
        return not self.errors

    def error(self, code: str, message: str) -> None:
        self.issues.append(ValidationIssue("error", code, message))

    def warn(self, code: str, message: str) -> None:
        self.issues.append(ValidationIssue("warning", code, message))

    def raise_for_errors(self) -> None:
        if not self.ok:
            raise SessionValidationError(self)


def validate_session(session: Session, *, max_sensor_gap_s: float = 0.25) -> ValidationReport:
    """Check boundaries, sensor ordering/coverage, counts, paths, and decoded dimensions.

    Coverage requires a sample strictly before and after each image in every enabled
    stream, with at most ``max_sensor_gap_s`` between the bracketing samples.
    """
    if not math.isfinite(max_sensor_gap_s) or max_sensor_gap_s <= 0:
        raise ValueError("max_sensor_gap_s must be a positive finite number")
    report = ValidationReport()
    _validate_timeline(session, report)
    _validate_sensors(session, report, max_sensor_gap_s)
    _validate_images(session, report)
    return report


def _validate_timeline(session: Session, report: ValidationReport) -> None:
    events = session.events
    metadata = session.metadata
    for kind, bound in (("timeline_start", session.start_ns), ("timeline_end", session.end_ns)):
        count = sum(event.type == kind for event in events)
        if count != 1:
            report.error("timeline_boundaries", f"events.jsonl: expected one {kind}, found {count}")
        declared = getattr(metadata, f"{kind}_ns")
        if declared is not None and declared != bound:
            report.error(
                "timeline_metadata", f"session.json: {kind}_ns disagrees with events.jsonl"
            )
    if events and (events[0].type != "timeline_start" or events[-1].type != "timeline_end"):
        report.error(
            "event_boundaries",
            "events.jsonl must begin with timeline_start and end with timeline_end",
        )
    start, end = session.start_ns, session.end_ns
    if start is not None and end is not None:
        if start >= end:
            report.error("timeline_order", "timeline_start must be strictly before timeline_end")
        for event in events:
            if (
                event.type not in ("timeline_start", "timeline_end")
                and not start < event.timestamp_ns < end
            ):
                report.error(
                    "event_outside_timeline",
                    f"{event.type} at {event.timestamp_ns} is outside the open timeline interval",
                )
    # Image records carry request time but are appended on callback, so JSONL append
    # order is not a general guarantee of event timestamp order.
    times = [image.timestamp_ns for image in session.images]
    if any(right <= left for left, right in pairwise(times)):
        report.error("image_order", "Image timestamps must be strictly increasing in event order")
    indices = [image.index for image in session.images]
    if any(right <= left for left, right in pairwise(indices)):
        report.error("image_indices", "Image indices must be unique and increasing")
    if metadata.state in ("recording", "ending"):
        report.error("unfinished_session", f"session.json: state is {metadata.state!r}")
    elif metadata.state == "interrupted":
        report.warn("interrupted_session", "Session was finalized after a lifecycle interruption")
    elif metadata.state is None:
        report.warn("missing_state", "session.json has no finalization state")
    if metadata.clock_domain is None:
        report.warn("missing_clock_domain", "session.json does not document its timestamp clock")
    elif metadata.clock_domain != CLOCK_DOMAIN:
        report.error("clock_domain", f"Unsupported session clock domain {metadata.clock_domain!r}")
    if metadata.image_count is not None and metadata.image_count != len(session.images):
        report.error(
            "image_count",
            f"session.json reports {metadata.image_count} images; "
            f"events.jsonl contains {len(session.images)}",
        )
    if metadata.dropped_sensor_samples:
        report.warn(
            "dropped_samples",
            f"Phone reported {metadata.dropped_sensor_samples} dropped sensor samples",
        )
    if not session.images:
        report.warn("no_images", "Session contains no successful images")
    if any(event.type == "image_error" for event in events):
        report.warn("capture_errors", "Session contains failed image capture attempts")


def _validate_sensors(session: Session, report: ValidationReport, max_gap_s: float) -> None:
    samples = session.sensor_samples
    if any(right.timestamp_ns < left.timestamp_ns for left, right in pairwise(samples)):
        report.error("sensor_order", "sensors.csv rows must be globally nondecreasing in timestamp")
    start, end = session.start_ns, session.end_ns
    if start is not None and end is not None:
        if any(not start < sample.timestamp_ns < end for sample in samples):
            report.error(
                "sensor_outside_timeline",
                "sensors.csv contains samples at or outside the timeline boundaries",
            )
    for name, stream in session.sensor_streams.items():
        available = session.metadata.sensors[name]
        timestamps = [sample.timestamp_ns for sample in stream]
        if any(right <= left for left, right in pairwise(timestamps)):
            report.error("sensor_stream_order", f"{name}: timestamps must be strictly increasing")
        declared = (session.metadata.sensor_counts or {}).get(name)
        if declared is not None and declared != len(stream):
            report.error(
                "sensor_count",
                f"{name}: session.json reports {declared} samples, CSV contains {len(stream)}",
            )
        if not available:
            if stream:
                report.error(
                    "sensor_availability",
                    f"{name}: samples exist but sensor is declared unavailable",
                )
            else:
                report.warn("sensor_unavailable", f"{name}: sensor was unavailable during capture")
            continue
        if not timestamps:
            report.error("empty_sensor_stream", f"{name}: enabled sensor has no samples")
            continue
        ordered = sorted(timestamps)
        gaps = [(right - left) / 1e9 for left, right in pairwise(ordered)]
        if gaps and max(gaps) > max_gap_s:
            report.warn("sensor_gap", f"{name}: maximum sample gap is {max(gaps):.6f} s")
        for image in session.images:
            before = bisect_left(ordered, image.timestamp_ns) - 1
            after = bisect_right(ordered, image.timestamp_ns)
            if before < 0 or after >= len(ordered):
                report.error(
                    "sensor_coverage",
                    f"{name}: no samples strictly before and after "
                    f"image {image.index} at {image.timestamp_ns}",
                )
            elif (ordered[after] - ordered[before]) / 1e9 > max_gap_s:
                report.error(
                    "sensor_coverage_gap",
                    f"{name}: samples around image {image.index} "
                    f"are more than {max_gap_s:g} s apart",
                )
        if name == "rotation_vector":
            norms = [
                math.sqrt(sum(value * value for value in (s.x, s.y, s.z, s.w))) for s in stream
            ]
            if any(abs(norm - 1) > 0.05 for norm in norms):
                report.warn(
                    "rotation_norm",
                    "rotation_vector: quaternion norms differ from 1 by more than 0.05",
                )


def _validate_images(session: Session, report: ValidationReport) -> None:
    filenames = Counter(image.filename for image in session.images)
    if any(count > 1 for count in filenames.values()):
        report.error("duplicate_filename", "Multiple image events refer to the same file")
    approximate_times = False
    missing_exposure_times = False
    for image in session.images:
        label = f"image {image.index} ({image.filename})"
        if (
            image.clock_domain is not None
            and session.metadata.clock_domain is not None
            and image.clock_domain != session.metadata.clock_domain
        ):
            report.error("image_clock_domain", f"{label}: clock differs from session clock")
        if image.timestamp_source == "button_request":
            approximate_times = True
            if image.request_timestamp_ns != image.timestamp_ns:
                report.error(
                    "image_request_time", f"{label}: request and primary timestamps differ"
                )
        if (
            image.request_timestamp_ns is not None
            and image.request_timestamp_ns > image.timestamp_ns
        ):
            report.error("image_request_time", f"{label}: request is later than primary timestamp")
        if image.callback_timestamp_ns is not None:
            if image.callback_timestamp_ns < image.timestamp_ns:
                report.error("image_callback_time", f"{label}: callback precedes image timestamp")
            if session.end_ns is not None and image.callback_timestamp_ns >= session.end_ns:
                report.error("image_callback_time", f"{label}: callback is at/after timeline end")
        metadata = image.metadata
        missing = [
            name
            for name in (
                "width",
                "height",
                "focal_length_mm",
                "camera_id",
                "exif_orientation",
                "exposure_time_s",
                "iso",
            )
            if getattr(metadata, name) is None
        ]
        if missing:
            report.warn("missing_image_metadata", f"{label}: missing {', '.join(missing)}")
        missing_exposure_times |= metadata.sensor_timestamp_ns is None
        try:
            path = session.image_path(image)
        except (DatasetFormatError, OSError, RuntimeError) as error:
            report.error("image_path", str(error))
            continue
        if not path.is_file():
            report.error("missing_image", f"{label}: file does not exist")
            continue
        try:
            # M1 metadata describes stored pixels BEFORE EXIF rotation. Never use the
            # default auto-oriented dimensions to validate that metadata.
            pixels = cv2.imread(str(path), cv2.IMREAD_COLOR | cv2.IMREAD_IGNORE_ORIENTATION)
        except (cv2.error, OSError) as error:
            report.error("image_decode", f"{label}: cannot decode image: {error}")
            continue
        if pixels is None or pixels.size == 0:
            report.error("image_decode", f"{label}: image cannot be decoded")
            continue
        height, width = pixels.shape[:2]
        if (metadata.width is not None and width != metadata.width) or (
            metadata.height is not None and height != metadata.height
        ):
            report.error(
                "image_dimensions",
                f"{label}: stored size {width}x{height} disagrees "
                f"with metadata {metadata.width}x{metadata.height}",
            )
    if approximate_times:
        report.warn(
            "request_timestamps",
            "Image timestamps are button requests, not exposures; "
            "exact visual/IMU synchronization is unavailable",
        )
    if missing_exposure_times:
        report.warn(
            "missing_exposure_timestamps", "Camera sensor exposure timestamps are unavailable"
        )
