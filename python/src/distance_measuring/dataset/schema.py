"""Typed capture records; contract: docs/architecture/capture_format.md."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

SENSOR_TYPES = ("accelerometer", "gyroscope", "rotation_vector")
OPTIONAL_SENSOR_TYPES = ("game_rotation_vector", "magnetometer")
ALL_SENSOR_TYPES = SENSOR_TYPES + OPTIONAL_SENSOR_TYPES
CLOCK_DOMAIN = "android_elapsed_realtime_ns"


class DatasetFormatError(ValueError):
    """A capture field or file is malformed, with its source location attached."""


def _object(value: Any, location: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DatasetFormatError(f"{location}: expected a JSON object")
    return value


def _integer(value: Any, location: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise DatasetFormatError(f"{location}: expected an integer >= {minimum}, got {value!r}")
    return value


def _text(value: Any, location: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DatasetFormatError(f"{location}: expected a nonempty string")
    return value


def _number(value: Any, location: str, *, positive: bool = False) -> float:
    try:
        valid = type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        valid = False
    if not valid or (positive and value <= 0):
        qualifier = "positive finite" if positive else "finite"
        raise DatasetFormatError(f"{location}: expected a {qualifier} number, got {value!r}")
    return float(value)


@dataclass(frozen=True, slots=True)
class SessionMetadata:
    session_id: str
    schema_version: int
    device: dict[str, Any]
    sensors: dict[str, bool]
    state: str | None = None
    clock_domain: str | None = None
    timeline_start_ns: int | None = None
    timeline_end_ns: int | None = None
    image_count: int | None = None
    sensor_counts: dict[str, int] | None = None
    dropped_sensor_samples: int = 0
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_dict(cls, value: Any, location: str = "session") -> SessionMetadata:
        data = _object(value, location)
        version = _integer(data.get("schema_version"), f"{location}.schema_version", 1)
        if version != 1:
            raise DatasetFormatError(f"{location}.schema_version: unsupported version {version}")
        sensors = _object(data.get("sensors"), f"{location}.sensors")
        for name in SENSOR_TYPES:
            if type(sensors.get(name)) is not bool:
                raise DatasetFormatError(f"{location}.sensors.{name}: expected a boolean")
        for name in sensors:
            if name not in ALL_SENSOR_TYPES:
                raise DatasetFormatError(f"{location}.sensors: unsupported sensor {name!r}")
            if type(sensors[name]) is not bool:
                raise DatasetFormatError(f"{location}.sensors.{name}: expected a boolean")
        kwargs = {}
        for name in ("timeline_start_ns", "timeline_end_ns", "image_count"):
            if name in data:
                kwargs[name] = _integer(data[name], f"{location}.{name}")
        counts = data.get("sensor_counts")
        if counts is not None:
            counts = _object(counts, f"{location}.sensor_counts")
            counts = {
                name: _integer(value, f"{location}.sensor_counts.{name}")
                for name, value in counts.items()
            }
        state = data.get("state")
        if state is not None and state not in ("recording", "ending", "complete", "interrupted"):
            raise DatasetFormatError(f"{location}.state: unsupported state {state!r}")
        clock = data.get("clock_domain")
        if clock is not None:
            clock = _text(clock, f"{location}.clock_domain")
        return cls(
            session_id=_text(data.get("session_id"), f"{location}.session_id"),
            schema_version=version,
            device=dict(_object(data.get("device"), f"{location}.device")),
            sensors=dict(sensors),
            state=state,
            clock_domain=clock,
            sensor_counts=counts,
            dropped_sensor_samples=_integer(
                data.get("dropped_sensor_samples", 0), f"{location}.dropped_sensor_samples"
            ),
            raw=dict(data),
            **kwargs,
        )


@dataclass(frozen=True, slots=True)
class SensorSample:
    timestamp_ns: int
    sensor_type: str
    x: float
    y: float
    z: float
    w: float | None
    accuracy: int
    source_line: int = field(default=0, compare=False, repr=False)

    @classmethod
    def from_csv(cls, row: dict[str, str], location: str, line: int) -> SensorSample:
        def integer(name: str, minimum: int = 0) -> int:
            try:
                value = int(row[name])
            except (ValueError, TypeError) as error:
                raise DatasetFormatError(f"{location}.{name}: expected an integer") from error
            return _integer(value, f"{location}.{name}", minimum)

        def number(name: str) -> float:
            try:
                value = float(row[name])
            except (ValueError, TypeError) as error:
                raise DatasetFormatError(f"{location}.{name}: expected a finite number") from error
            return _number(value, f"{location}.{name}")

        kind = row["sensor_type"]
        if kind not in ALL_SENSOR_TYPES:
            raise DatasetFormatError(f"{location}.sensor_type: unsupported sensor {kind!r}")
        w = number("w") if row["w"].strip() else None
        if (kind in ("rotation_vector", "game_rotation_vector")) != (w is not None):
            raise DatasetFormatError(f"{location}.w: required only for quaternion sensor samples")
        accuracy = integer("accuracy", -1)
        if accuracy > 3:
            raise DatasetFormatError(
                f"{location}.accuracy: expected an Android accuracy in [-1, 3]"
            )
        return cls(
            integer("timestamp_ns"), kind, number("x"), number("y"), number("z"), w, accuracy, line
        )


@dataclass(frozen=True, slots=True)
class ImageMetadata:
    width: int | None = None
    height: int | None = None
    focal_length_mm: float | None = None
    focal_length_35mm: float | None = None
    exposure_time_s: float | None = None
    iso: float | None = None
    camera_id: str | None = None
    exif_orientation: int | None = None
    rotation_degrees: int | None = None
    is_flipped: bool | None = None
    target_rotation: int | None = None
    lens_facing: int | None = None
    sensor_orientation_degrees: int | None = None
    sensor_width_mm: float | None = None
    sensor_height_mm: float | None = None
    sensor_timestamp_ns: int | None = None
    sensor_timestamp_clock_domain: str | None = None
    metadata_source: str | None = None
    physical_camera_id: str | None = None
    focus_distance_diopters: float | None = None
    zoom_ratio: float | None = None
    crop_region: tuple[int, int, int, int] | None = None
    ae_mode: int | None = None
    ae_state: int | None = None
    ae_lock: bool | None = None
    awb_mode: int | None = None
    awb_state: int | None = None
    awb_lock: bool | None = None
    af_mode: int | None = None
    af_state: int | None = None
    lens_state: int | None = None
    capture_result_source: str | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_dict(cls, value: Any, location: str = "image.metadata") -> ImageMetadata:
        data = _object(value, location)
        kwargs = {}
        for name in ("width", "height", "sensor_timestamp_ns"):
            if data.get(name) is not None:
                kwargs[name] = _integer(
                    data[name], f"{location}.{name}", 1 if name != "sensor_timestamp_ns" else 0
                )
        for name in (
            "focal_length_mm",
            "focal_length_35mm",
            "exposure_time_s",
            "iso",
            "sensor_width_mm",
            "sensor_height_mm",
            "zoom_ratio",
        ):
            if data.get(name) is not None:
                kwargs[name] = _number(data[name], f"{location}.{name}", positive=True)
        if data.get("focus_distance_diopters") is not None:
            focus = _number(data["focus_distance_diopters"], f"{location}.focus_distance_diopters")
            if focus < 0:
                raise DatasetFormatError(f"{location}.focus_distance_diopters: expected >= 0")
            kwargs["focus_distance_diopters"] = focus
        if data.get("crop_region") is not None:
            crop = data["crop_region"]
            if not isinstance(crop, (list, tuple)) or len(crop) != 4:
                raise DatasetFormatError(
                    f"{location}.crop_region: expected [left, top, right, bottom]"
                )
            crop = tuple(_integer(v, f"{location}.crop_region") for v in crop)
            if crop[2] <= crop[0] or crop[3] <= crop[1]:
                raise DatasetFormatError(f"{location}.crop_region: expected a nonempty rectangle")
            kwargs["crop_region"] = crop
        for name in (
            "ae_mode",
            "ae_state",
            "awb_mode",
            "awb_state",
            "af_mode",
            "af_state",
            "lens_state",
        ):
            if data.get(name) is not None:
                kwargs[name] = _integer(data[name], f"{location}.{name}")
        for name in (
            "camera_id",
            "physical_camera_id",
            "sensor_timestamp_clock_domain",
            "metadata_source",
            "capture_result_source",
        ):
            if data.get(name) is not None:
                kwargs[name] = _text(data[name], f"{location}.{name}")
        for name, allowed in (
            ("exif_orientation", range(1, 9)),
            ("rotation_degrees", (0, 90, 180, 270)),
            ("target_rotation", range(4)),
            ("lens_facing", range(3)),
            ("sensor_orientation_degrees", (0, 90, 180, 270)),
        ):
            if data.get(name) is not None:
                value = _integer(data[name], f"{location}.{name}")
                if value not in allowed:
                    raise DatasetFormatError(f"{location}.{name}: unsupported value {value}")
                kwargs[name] = value
        for name in ("is_flipped", "ae_lock", "awb_lock"):
            if data.get(name) is not None:
                if type(data[name]) is not bool:
                    raise DatasetFormatError(f"{location}.{name}: expected a boolean")
                kwargs[name] = data[name]
        return cls(**kwargs, raw=dict(data))


@dataclass(frozen=True, slots=True)
class Event:
    type: str
    timestamp_ns: int
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_dict(cls, value: Any, location: str) -> Event | ImageEvent:
        data = _object(value, location)
        kind = _text(data.get("type"), f"{location}.type")
        timestamp = _integer(data.get("timestamp_ns"), f"{location}.timestamp_ns")
        if kind == "image":
            return ImageEvent.from_dict(data, location)
        if kind in ("image_request", "image_error"):
            _integer(data.get("index"), f"{location}.index")
        return cls(kind, timestamp, dict(data))


@dataclass(frozen=True, slots=True)
class ImageEvent:
    index: int
    filename: str
    timestamp_ns: int
    metadata: ImageMetadata
    request_timestamp_ns: int | None = None
    callback_timestamp_ns: int | None = None
    timestamp_source: str | None = None
    clock_domain: str | None = None
    type: str = field(default="image", init=False)
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_dict(cls, value: Any, location: str = "image") -> ImageEvent:
        data = _object(value, location)
        filename = _text(data.get("filename"), f"{location}.filename")
        parts = filename.split("/")
        if (
            PurePosixPath(filename).is_absolute()
            or PureWindowsPath(filename).drive
            or "\\" in filename
            or "\x00" in filename
            or any(part in ("", ".", "..") for part in parts)
        ):
            raise DatasetFormatError(f"{location}.filename: expected a safe relative path")
        kwargs = {}
        for name in ("request_timestamp_ns", "callback_timestamp_ns"):
            if data.get(name) is not None:
                kwargs[name] = _integer(data[name], f"{location}.{name}")
        for name in ("timestamp_source", "clock_domain"):
            if data.get(name) is not None:
                kwargs[name] = _text(data[name], f"{location}.{name}")
        return cls(
            index=_integer(data.get("index"), f"{location}.index"),
            filename=filename,
            timestamp_ns=_integer(data.get("timestamp_ns"), f"{location}.timestamp_ns"),
            metadata=ImageMetadata.from_dict(data.get("metadata", {}), f"{location}.metadata"),
            raw=dict(data),
            **kwargs,
        )


@dataclass(frozen=True, slots=True)
class Session:
    path: Path
    metadata: SessionMetadata
    sensor_samples: tuple[SensorSample, ...]
    events: tuple[Event | ImageEvent, ...]

    @property
    def images(self) -> tuple[ImageEvent, ...]:
        return tuple(event for event in self.events if isinstance(event, ImageEvent))

    @property
    def start_ns(self) -> int | None:
        return next(
            (event.timestamp_ns for event in self.events if event.type == "timeline_start"), None
        )

    @property
    def end_ns(self) -> int | None:
        return next(
            (event.timestamp_ns for event in self.events if event.type == "timeline_end"), None
        )

    @property
    def duration_s(self) -> float | None:
        if self.start_ns is None or self.end_ns is None:
            return None
        return (self.end_ns - self.start_ns) / 1e9

    @property
    def sensor_streams(self) -> dict[str, tuple[SensorSample, ...]]:
        return {
            name: tuple(sample for sample in self.sensor_samples if sample.sensor_type == name)
            for name in ALL_SENSOR_TYPES
            if name in self.metadata.sensors
            or any(s.sensor_type == name for s in self.sensor_samples)
        }

    def image_path(self, image: ImageEvent) -> Path:
        path = (self.path / image.filename).resolve()
        if not path.is_relative_to(self.path.resolve()):
            raise DatasetFormatError(
                f"{image.filename}: image resolves outside the session directory"
            )
        return path
