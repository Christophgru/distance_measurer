"""Generate small, original capture fixtures; never depend on personal raw sessions."""

import csv
import json
import struct
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import pytest

from distance_measuring.dataset.loader import CSV_COLUMNS
from distance_measuring.dataset.schema import CLOCK_DOMAIN, SENSOR_TYPES


@dataclass
class CaptureFixture:
    path: Path
    metadata: dict
    events: list[dict]
    rows: list[dict]

    def write(self):
        (self.path / "session.json").write_text(json.dumps(self.metadata), encoding="utf-8")
        (self.path / "events.jsonl").write_text(
            "".join(json.dumps(event) + "\n" for event in self.events), encoding="utf-8"
        )
        with (self.path / "sensors.csv").open("w", newline="", encoding="utf-8") as output:
            writer = csv.DictWriter(output, CSV_COLUMNS)
            writer.writeheader()
            writer.writerows(self.rows)
        return self.path


@pytest.fixture
def capture_factory(tmp_path):
    sequence = 0

    def create(*, image_count=3, rotation_vector=True):
        nonlocal sequence
        sequence += 1
        root = tmp_path / f"capture_{sequence}"
        (root / "images").mkdir(parents=True)
        start, end = 1_000_000_000, 2_000_000_000
        availability = {name: name != "rotation_vector" or rotation_vector for name in SENSOR_TYPES}
        metadata = {
            "schema_version": 1,
            "session_id": "synthetic_capture",
            "device": {"manufacturer": "Synthetic", "model": "Test camera"},
            "state": "complete",
            "clock_domain": CLOCK_DOMAIN,
            "timeline_start_ns": start,
            "timeline_end_ns": end,
            "sensors": availability,
            "image_count": image_count,
            "sensor_counts": {name: 99 if enabled else 0 for name, enabled in availability.items()},
            "dropped_sensor_samples": 0,
        }
        rows = []
        for timestamp in range(start + 10_000_000, end, 10_000_000):
            for name in sorted(SENSOR_TYPES):
                if availability[name]:
                    rows.append(
                        {
                            "timestamp_ns": timestamp,
                            "sensor_type": name,
                            "x": 0,
                            "y": 0,
                            "z": 9.81 if name == "accelerometer" else 0,
                            "w": 1 if name == "rotation_vector" else "",
                            "accuracy": 3,
                        }
                    )
        # A generated 80x48 JPEG with an EXIF orientation=6 (90-degree) APP1 segment.
        # Inserting the minimal TIFF tag keeps fixtures independent of Pillow.
        success, encoded = cv2.imencode(".jpg", np.full((48, 80, 3), 127, dtype=np.uint8))
        assert success
        exif = (
            b"Exif\x00\x00II\x2a\x00\x08\x00\x00\x00"
            + struct.pack("<H", 1)
            + struct.pack("<HHI", 0x0112, 3, 1)
            + struct.pack("<H", 6)
            + b"\x00" * 6
        )
        jpeg = encoded.tobytes()
        jpeg = jpeg[:2] + b"\xff\xe1" + struct.pack(">H", len(exif) + 2) + exif + jpeg[2:]
        events = [{"type": "timeline_start", "timestamp_ns": start}]
        for index in range(image_count):
            timestamp = start + (index + 1) * (end - start) // (image_count + 1)
            filename = f"images/image_{index:03d}.jpg"
            (root / filename).write_bytes(jpeg)
            events.append({"type": "image_request", "index": index, "timestamp_ns": timestamp})
            events.append(
                {
                    "type": "image",
                    "index": index,
                    "filename": filename,
                    "timestamp_ns": timestamp,
                    "request_timestamp_ns": timestamp,
                    "callback_timestamp_ns": timestamp + 5_000_000,
                    "clock_domain": CLOCK_DOMAIN,
                    "timestamp_source": "button_request",
                    "metadata": {
                        "width": 80,
                        "height": 48,
                        "camera_id": "0",
                        "focal_length_mm": 4.2,
                        "exposure_time_s": 0.01,
                        "iso": 100,
                        "exif_orientation": 6,
                        "rotation_degrees": 90,
                        "is_flipped": False,
                        "sensor_timestamp_ns": None,
                        "sensor_timestamp_clock_domain": None,
                    },
                }
            )
        events.append({"type": "timeline_end", "timestamp_ns": end})
        fixture = CaptureFixture(root, metadata, events, rows)
        fixture.write()
        return fixture

    return create


@pytest.fixture
def capture(capture_factory):
    return capture_factory()
