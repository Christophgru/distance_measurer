"""Read immutable Android captures; see docs/architecture/capture_format.md."""

import csv
import json
import math
from pathlib import Path
from typing import Any

from .schema import DatasetFormatError, Event, SensorSample, Session, SessionMetadata

CSV_COLUMNS = ("timestamp_ns", "sensor_type", "x", "y", "z", "w", "accuracy")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _invalid_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number {value}")


def _finite_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        _invalid_constant(value)
    return number


def _json(text: str, location: str) -> Any:
    try:
        return json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_invalid_constant,
            parse_float=_finite_float,
        )
    except ValueError as error:
        raise DatasetFormatError(f"{location}: invalid JSON: {error}") from error


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise DatasetFormatError(f"{path}: cannot read UTF-8 text: {error}") from error


def load_session(
    path: str | Path, *, validate: bool = True, max_sensor_gap_s: float = 0.25
) -> Session:
    """Load a session and validate it by default, including decoding each image.

    ``validate=False`` permits inspection of structurally readable but inconsistent
    recordings. It never disables field/type checks or modifies input files.
    """
    root = Path(path).resolve()
    if not root.is_dir():
        raise DatasetFormatError(f"{root}: session directory does not exist")
    metadata_path = root / "session.json"
    metadata = SessionMetadata.from_dict(
        _json(_read_text(metadata_path), str(metadata_path)), str(metadata_path)
    )
    csv_path = root / "sensors.csv"
    reader = csv.DictReader(_read_text(csv_path).splitlines(), strict=True)
    samples = []
    try:
        if reader.fieldnames is None or tuple(reader.fieldnames) != CSV_COLUMNS:
            raise DatasetFormatError(f"{csv_path}: expected CSV header {','.join(CSV_COLUMNS)}")
        for row in reader:
            location = f"{csv_path}:{reader.line_num}"
            if None in row or any(value is None for value in row.values()):
                raise DatasetFormatError(f"{location}: expected exactly {len(CSV_COLUMNS)} columns")
            samples.append(SensorSample.from_csv(row, location, reader.line_num))
    except csv.Error as error:
        raise DatasetFormatError(f"{csv_path}:{reader.line_num}: invalid CSV: {error}") from error
    event_path = root / "events.jsonl"
    events = tuple(
        Event.from_dict(_json(line, f"{event_path}:{number}"), f"{event_path}:{number}")
        for number, line in enumerate(_read_text(event_path).splitlines(), 1)
        if line.strip()
    )
    from .tracking import load_tracking

    session = Session(root, metadata, tuple(samples), events, tracking=load_tracking(root))
    if validate:
        from .validation import validate_session

        validate_session(session, max_sensor_gap_s=max_sensor_gap_s).raise_for_errors()
    return session
