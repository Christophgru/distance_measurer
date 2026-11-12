"""P9.1 optional preview sidecar; see docs/architecture/tracking_capture_format.md."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .schema import (
    CLOCK_DOMAIN,
    DatasetFormatError,
    ImageMetadata,
    _integer,
    _number,
    _object,
    _relative_path,
    _text,
    capture_path,
)

if TYPE_CHECKING:
    from distance_measuring.camera import CameraIntrinsics

RECORDS_FILE = "tracking/events.jsonl"
UNKNOWN_CAMERA_CLOCK = "camera_unknown"


@dataclass(frozen=True, slots=True)
class TrackingEvent:
    type: str
    sequence: int
    recorded_timestamp_ns: int
    raw: dict[str, Any] = field(repr=False, compare=False)

    @classmethod
    def from_dict(cls, value, location):
        data = _object(value, location)
        kind = _text(data.get("type"), f"{location}.type")
        sequence = _integer(data.get("sequence"), f"{location}.sequence")
        recorded = _integer(data.get("recorded_timestamp_ns"), f"{location}.recorded_timestamp_ns")
        if kind == "frame":
            return TrackingFrame.from_dict(data, location)
        if kind == "drop":
            _integer(data.get("first_frame_id"), f"{location}.first_frame_id")
            _integer(data.get("count"), f"{location}.count", 1)
            _text(data.get("reason"), f"{location}.reason")
        elif kind == "reset":
            _integer(data.get("next_frame_id"), f"{location}.next_frame_id")
            _text(data.get("reason"), f"{location}.reason")
        elif kind == "capture":
            _integer(data.get("scheduled_timestamp_ns"), f"{location}.scheduled_timestamp_ns")
            if "missed_tick_count" in data:
                _integer(data["missed_tick_count"], f"{location}.missed_tick_count", 1)
                _integer(
                    data.get("last_skipped_timestamp_ns"), f"{location}.last_skipped_timestamp_ns"
                )
                if data.get("status") != "skipped":
                    raise DatasetFormatError(
                        f"{location}: only skipped ticks can aggregate missed intervals"
                    )
            elif "last_skipped_timestamp_ns" in data:
                raise DatasetFormatError(
                    f"{location}: last_skipped_timestamp_ns requires missed_tick_count"
                )
            if data.get("status") == "requested":
                _integer(data.get("image_index"), f"{location}.image_index")
                _integer(data.get("request_timestamp_ns"), f"{location}.request_timestamp_ns")
                if data.get("reason") is not None:
                    raise DatasetFormatError(
                        f"{location}.reason: requested capture needs null reason"
                    )
            elif data.get("status") == "skipped":
                if (
                    data.get("image_index") is not None
                    or data.get("request_timestamp_ns") is not None
                ):
                    raise DatasetFormatError(
                        f"{location}: skipped capture cannot name a request/image"
                    )
                _text(data.get("reason"), f"{location}.reason")
            else:
                raise DatasetFormatError(f"{location}.status: expected requested or skipped")
        else:
            raise DatasetFormatError(f"{location}.type: unsupported tracking event {kind!r}")
        return cls(kind, sequence, recorded, dict(data))


@dataclass(frozen=True, slots=True)
class TrackingFrame:
    sequence: int
    recorded_timestamp_ns: int
    frame_id: int
    filename: str
    timestamp_ns: int
    clock_domain: str
    received_timestamp_ns: int
    metadata: ImageMetadata
    buffer_size: tuple[int, int]
    buffer_crop_region: tuple[int, int, int, int]
    sample_step: int
    intrinsics: CameraIntrinsics | None
    intrinsics_unavailable_reason: str | None
    raw: dict[str, Any] = field(repr=False, compare=False)
    type: str = field(default="frame", init=False)

    @classmethod
    def from_dict(cls, value, location):
        data = _object(value, location)
        if data.get("type") != "frame":
            raise DatasetFormatError(f"{location}.type: expected frame")
        frame_id = _integer(data.get("frame_id"), f"{location}.frame_id")
        filename = _relative_path(data.get("filename"), f"{location}.filename")
        if not filename.startswith("tracking/frames/") or not filename.endswith(".png"):
            raise DatasetFormatError(f"{location}.filename: expected tracking/frames/*.png")
        clock = data.get("clock_domain")
        if clock not in (CLOCK_DOMAIN, UNKNOWN_CAMERA_CLOCK):
            raise DatasetFormatError(
                f"{location}.clock_domain: expected declared REALTIME or camera_unknown"
            )
        metadata = ImageMetadata.from_dict(data.get("metadata"), f"{location}.metadata")
        if (
            metadata.width is None
            or metadata.height is None
            or metadata.camera_id is None
            or metadata.rotation_degrees is None
            or metadata.is_flipped is None
        ):
            raise DatasetFormatError(
                f"{location}.metadata: dimensions, camera_id, rotation_degrees "
                "and is_flipped are required"
            )

        def integers(name, length, minimum=0):
            values = data.get(name)
            if not isinstance(values, list) or len(values) != length:
                raise DatasetFormatError(f"{location}.{name}: expected {length} integers")
            return tuple(_integer(v, f"{location}.{name}", minimum) for v in values)

        size = integers("buffer_size", 2, 1)
        crop = integers("buffer_crop_region", 4)
        step = _integer(data.get("sample_step"), f"{location}.sample_step", 1)
        left, top, right, bottom = crop
        if not (left < right <= size[0] and top < bottom <= size[1]):
            raise DatasetFormatError(
                f"{location}.buffer_crop_region: crop must lie inside native buffer"
            )
        saved_size = ((right - left - 1) // step + 1, (bottom - top - 1) // step + 1)
        if saved_size != (metadata.width, metadata.height):
            raise DatasetFormatError(
                f"{location}.metadata: saved dimensions disagree with crop/sample transform"
            )
        intrinsics, reason = _intrinsics(data, metadata, location)
        if "diagnostics" in data:
            _object(data["diagnostics"], f"{location}.diagnostics")
        return cls(
            _integer(data.get("sequence"), f"{location}.sequence"),
            _integer(data.get("recorded_timestamp_ns"), f"{location}.recorded_timestamp_ns"),
            frame_id,
            filename,
            _integer(data.get("timestamp_ns"), f"{location}.timestamp_ns", 1),
            clock,
            _integer(data.get("received_timestamp_ns"), f"{location}.received_timestamp_ns"),
            metadata,
            size,
            crop,
            step,
            intrinsics,
            reason,
            dict(data),
        )

    @property
    def buffer_to_saved_affine(self):
        """Same integer crop/subsample mapping as Android PreviewLuma.sampledPixel."""
        import numpy as np

        left, top, _, _ = self.buffer_crop_region
        step = self.sample_step
        return np.array([[1 / step, 0, -left / step], [0, 1 / step, -top / step], [0, 0, 1]])


def _intrinsics(data, metadata, location):
    # Import at parse time: the camera package itself uses dataset.ImageMetadata.
    from distance_measuring.camera import CameraIntrinsics

    if "intrinsics" not in data:
        raise DatasetFormatError(f"{location}.intrinsics: explicit object or null required")
    reason = data.get("intrinsics_unavailable_reason")
    if data["intrinsics"] is None:
        return None, _text(reason, f"{location}.intrinsics_unavailable_reason")
    if reason is not None:
        raise DatasetFormatError(
            f"{location}.intrinsics_unavailable_reason: must be null when K is available"
        )
    value = _object(data["intrinsics"], f"{location}.intrinsics")
    try:
        if value.get("pixel_convention") != "stored_pixels_ignore_exif":
            raise ValueError("pixel_convention must describe stored pixels ignoring EXIF")
        if type(value.get("approximate")) is not bool:
            raise ValueError("approximate must be an explicit boolean")
        size = tuple(
            _integer(value.get(k), f"{location}.intrinsics.{k}", 1)
            for k in ("image_width", "image_height")
        )
        matrix = value.get("camera_matrix")
        if (
            not isinstance(matrix, list)
            or len(matrix) != 3
            or any(not isinstance(r, list) or len(r) != 3 for r in matrix)
        ):
            raise ValueError("camera_matrix must be a 3x3 array")
        matrix = [
            [_number(v, f"{location}.intrinsics.camera_matrix") for v in row] for row in matrix
        ]
        coefficients = value.get("distortion")
        if not isinstance(coefficients, list):
            raise ValueError("distortion must be an explicit array")
        coefficients = [_number(v, f"{location}.intrinsics.distortion") for v in coefficients]
        assumptions = value.get("assumptions")
        if not isinstance(assumptions, list) or any(
            not isinstance(v, str) or not v.strip() for v in assumptions
        ):
            raise ValueError("assumptions must be an explicit array of nonempty strings")
        camera = CameraIntrinsics.from_K(
            matrix,
            size,
            distortion=coefficients,
            source=value.get("source"),
            camera_id=value.get("camera_id"),
            assumptions=assumptions,
        )
        if camera.approximate is not value["approximate"]:
            raise ValueError("approximate flag contradicts intrinsic source")
        if camera.approximate and not assumptions:
            raise ValueError("approximate intrinsics require documented assumptions")
        if size != (metadata.width, metadata.height) or camera.camera_id != metadata.camera_id:
            raise ValueError("intrinsics must match saved dimensions and camera_id")
        return camera, None
    except (ValueError, TypeError, OverflowError) as error:
        raise DatasetFormatError(f"{location}.intrinsics: {error}") from error


@dataclass(frozen=True, slots=True)
class TrackingRecording:
    session_id: str
    state: str
    failure_reason: str | None
    settings: dict[str, Any]
    counts: dict[str, int]
    records: tuple[TrackingEvent | TrackingFrame, ...]
    schema_version: int = 1

    @property
    def frames(self):
        return tuple(r for r in self.records if isinstance(r, TrackingFrame))


def load_tracking(root: Path) -> TrackingRecording | None:
    """Load structural fields only; consistency/decode checks belong to validation."""
    from .loader import _json, _read_text

    candidate = root / "tracking.json"
    if not candidate.exists() and not candidate.is_symlink():
        if (root / "tracking").exists() or (root / "tracking").is_symlink():
            raise DatasetFormatError(f"{candidate}: missing manifest for tracking directory")
        return None
    path = capture_path(root, "tracking.json")
    location = str(path)
    data = _object(_json(_read_text(path), location), location)
    version = _integer(data.get("schema_version"), f"{location}.schema_version", 1)
    if version != 1:
        raise DatasetFormatError(f"{location}.schema_version: unsupported version {version}")
    if data.get("pixel_format") != "gray8_png":
        raise DatasetFormatError(f"{location}.pixel_format: expected gray8_png")
    name = _relative_path(data.get("records_file"), f"{location}.records_file")
    if name != RECORDS_FILE:
        raise DatasetFormatError(f"{location}.records_file: expected {RECORDS_FILE}")
    state = data.get("state")
    if state not in ("recording", "ending", "complete", "interrupted", "unavailable"):
        raise DatasetFormatError(f"{location}.state: unsupported tracking state")
    reason = data.get("failure_reason")
    if state in ("interrupted", "unavailable") or reason is not None:
        reason = _text(reason, f"{location}.failure_reason")
    settings = dict(_object(data.get("settings"), f"{location}.settings"))
    for key in ("requested_width", "requested_height", "writer_queue_capacity"):
        settings[key] = _integer(settings.get(key), f"{location}.settings.{key}", 1)
    for key in ("max_analysis_fps", "still_interval_s"):
        settings[key] = _number(settings.get(key), f"{location}.settings.{key}", positive=True)
    if "automatic_stills" in settings and type(settings["automatic_stills"]) is not bool:
        raise DatasetFormatError(f"{location}.settings.automatic_stills: expected a boolean")
    if "diagnostic_queue_capacity" in settings:
        _integer(
            settings["diagnostic_queue_capacity"],
            f"{location}.settings.diagnostic_queue_capacity",
            1,
        )
    counts = _object(data.get("counts"), f"{location}.counts")
    counts = {
        key: _integer(counts.get(key), f"{location}.counts.{key}")
        for key in ("frames", "dropped_frames", "resets", "capture_events")
    }
    records_path = capture_path(root, name)
    records = tuple(
        TrackingEvent.from_dict(_json(line, f"{records_path}:{number}"), f"{records_path}:{number}")
        for number, line in enumerate(_read_text(records_path).splitlines(), 1)
        if line.strip()
    )
    return TrackingRecording(
        _text(data.get("session_id"), f"{location}.session_id"),
        state,
        reason,
        settings,
        counts,
        records,
        version,
    )
