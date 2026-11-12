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


@dataclass
class HybridCaptureFixture:
    capture: CaptureFixture
    manifest: dict
    records: list[dict]

    @property
    def path(self):
        return self.capture.path

    def write(self):
        self.capture.write()
        (self.path / "tracking.json").write_text(json.dumps(self.manifest), encoding="utf-8")
        (self.path / "tracking/events.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in self.records), encoding="utf-8"
        )
        return self.path


@pytest.fixture
def hybrid_capture(capture_factory):
    """Original PNG preview samples, drops/resets and automatic capture decisions."""
    from distance_measuring.camera import CameraIntrinsics

    capture = capture_factory()
    (capture.path / "tracking/frames").mkdir(parents=True)
    camera = CameraIntrinsics(45, 46, 19.5, 11.5, 40, 24, camera_id="0")
    pixels = np.arange(96 * 64, dtype=np.uint8).reshape(64, 96)
    records = [
        {
            "type": "reset",
            "sequence": 0,
            "recorded_timestamp_ns": 1_150_000_000,
            "next_frame_id": 0,
            "reason": "session_start",
        }
    ]
    for frame_id, timestamp in ((0, 1_200_000_000), (2, 1_400_000_000), (3, 1_800_000_000)):
        filename = f"tracking/frames/frame_{frame_id:06d}.png"
        assert cv2.imwrite(str(capture.path / filename), pixels[4:52:2, 8:88:2])
        records.append(
            {
                "type": "frame",
                "sequence": 0,
                "recorded_timestamp_ns": timestamp + 20_000_000,
                "frame_id": frame_id,
                "filename": filename,
                "timestamp_ns": timestamp,
                "clock_domain": CLOCK_DOMAIN,
                "received_timestamp_ns": timestamp + 1_000_000,
                "buffer_size": [96, 64],
                "buffer_crop_region": [8, 4, 88, 52],
                "sample_step": 2,
                "metadata": {
                    "width": 40,
                    "height": 24,
                    "camera_id": "0",
                    "physical_camera_id": "wide",
                    "rotation_degrees": 90,
                    "target_rotation": 0,
                    "is_flipped": False,
                    "lens_facing": 1,
                    "sensor_orientation_degrees": 90,
                },
                "intrinsics": camera.to_dict(),
                "intrinsics_unavailable_reason": None,
                "diagnostics": {"quality_status": "synthetic_contract_fixture"},
            }
        )
    records.extend(
        [
            {
                "type": "drop",
                "sequence": 0,
                "recorded_timestamp_ns": 1_320_000_000,
                "first_frame_id": 1,
                "count": 1,
                "reason": "writer_queue_full",
            },
            {
                "type": "capture",
                "sequence": 0,
                "recorded_timestamp_ns": 1_510_000_000,
                "scheduled_timestamp_ns": 1_490_000_000,
                "status": "requested",
                "request_timestamp_ns": 1_500_000_000,
                "image_index": 1,
                "reason": None,
            },
            {
                "type": "capture",
                "sequence": 0,
                "recorded_timestamp_ns": 1_610_000_000,
                "scheduled_timestamp_ns": 1_600_000_000,
                "status": "skipped",
                "request_timestamp_ns": None,
                "image_index": None,
                "reason": "camera_busy",
            },
        ]
    )
    records.sort(key=lambda row: row["recorded_timestamp_ns"])
    for sequence, row in enumerate(records):
        row["sequence"] = sequence
    manifest = {
        "schema_version": 1,
        "session_id": capture.metadata["session_id"],
        "state": "complete",
        "failure_reason": None,
        "records_file": "tracking/events.jsonl",
        "pixel_format": "gray8_png",
        "settings": {
            "requested_width": 640,
            "requested_height": 480,
            "max_analysis_fps": 10,
            "still_interval_s": 1,
            "writer_queue_capacity": 4,
        },
        "counts": {"frames": 3, "dropped_frames": 1, "resets": 1, "capture_events": 2},
    }
    fixture = HybridCaptureFixture(capture, manifest, records)
    fixture.write()
    return fixture


@pytest.fixture
def bundle_scene_factory():
    """Original calibrated three-view scenes, with known geometry and optional outliers."""
    from scipy.spatial.transform import Rotation

    from distance_measuring.camera import CameraExtrinsics, CameraIntrinsics
    from distance_measuring.scene import Observation, Scene, SceneCamera, ScenePoint, SceneTrack

    def make(*, count=50, noise_px=0.25, perturb=True, outliers=0, seed=39):
        rng = np.random.default_rng(seed)
        points = rng.uniform([-1.2, -0.8, 4], [1.2, 0.8, 8], (count, 3))
        origin = np.array([0.3, -0.2, 0.1])
        centers = [origin, origin + [1, 0.04, 0.02], origin + [2.1, 0.2, 0.1]]
        cameras, initial = [], []
        for i, image_id in enumerate((4, 7, 9)):
            K = CameraIntrinsics(900 + 80 * i, 870 + 60 * i, 639.5, 479.5, 1280, 960, skew=i)
            R = Rotation.from_rotvec([0.02, -0.06 * i, 0.01 * i]).as_matrix()
            cameras.append(SceneCamera(image_id, K, CameraExtrinsics(R, centers[i])))
            changed_R, changed_C = R, centers[i]
            if perturb and i:
                changed_R = Rotation.from_rotvec(rng.normal(0, 0.008, 3)).as_matrix() @ R
                delta = centers[i] - origin + rng.normal(0, 0.025, 3)
                if i == 1:
                    delta *= np.linalg.norm(centers[1] - origin) / np.linalg.norm(delta)
                changed_C = origin + delta
            initial.append(SceneCamera(image_id, K, CameraExtrinsics(changed_R, changed_C)))
        changed_points = points + (rng.normal(0, 0.04, points.shape) if perturb else 0)
        truth_tracks, tracks, truth_points, initial_points, bad = [], [], [], [], set()
        for j, (point, changed) in enumerate(zip(points, changed_points, strict=True)):
            point_id, track_id = 3 + 2 * j, 7 + 3 * j
            observations, exact = [], []
            for i, (camera, guess) in enumerate(zip(cameras, initial, strict=True)):
                pixel = camera.intrinsics.project(point, camera.extrinsics)
                measured = pixel + rng.normal(0, noise_px, 2)
                if j < outliers and i == j % 3:
                    measured += [65, -50]
                    bad.add((track_id, camera.image_id))
                error = float(
                    np.linalg.norm(guess.intrinsics.project(changed, guess.extrinsics) - measured)
                )
                observations.append(Observation(camera.image_id, j, tuple(measured), error))
                exact.append(Observation(camera.image_id, j, tuple(pixel), 0))
            tracks.append(SceneTrack(track_id, tuple(observations), point_id))
            truth_tracks.append(SceneTrack(track_id, tuple(exact), point_id))
            truth_points.append(ScenePoint(point_id, track_id, tuple(point), 10))
            initial_points.append(ScenePoint(point_id, track_id, tuple(changed), 10))
        rejected = SceneTrack(
            100000,
            (Observation(4, 999, (300, 200)), Observation(7, 999, (200, 200))),
            rejection_reason="high_reprojection_error",
        )
        metadata = {
            "scale_status": "arbitrary_global_scale",
            "quality_status": "synthetic_initialization",
            "baseline_convention": {
                "4-7": float(np.linalg.norm(centers[1] - origin)),
                "7-9": float(np.linalg.norm(centers[2] - centers[1])),
            },
            "warnings": ["Original synthetic fixture"],
            "timestamp_ns": 2**60 + 3,
        }
        return (
            Scene(tuple(cameras), tuple(truth_tracks + [rejected]), tuple(truth_points), metadata),
            Scene(tuple(initial), tuple(tracks + [rejected]), tuple(initial_points), metadata),
            bad,
        )

    return make


@pytest.fixture
def hybrid_scene_factory(capture_factory, tmp_path):
    """Original nonplanar preview/JPEG sequences with actual independent exposure times."""
    from types import SimpleNamespace

    from distance_measuring.camera import CameraExtrinsics, CameraIntrinsics, save_intrinsics

    def make(
        *,
        image_count=3,
        cropped_jpeg=False,
        mode="translation",
        missing_frames=(),
        geometry_change=False,
        unknown_clock=False,
        exposure_offset_s=0.03,
    ):
        capture = capture_factory(image_count=image_count)
        (capture.path / "tracking/frames").mkdir(parents=True)
        camera = CameraIntrinsics(400, 400, 319.5, 239.5, 640, 480, camera_id="0")
        jpeg_camera = (
            CameraIntrinsics(480, 480, 335.5, 230.5, 640, 480, camera_id="0")
            if cropped_jpeg
            else camera
        )
        rng = np.random.default_rng(31)
        source = np.array([(x, y) for y in range(55, 440, 55) for x in range(160, 610, 55)])
        depth = rng.uniform(4, 12, len(source))
        world = np.c_[camera.normalize_pixels(source), np.ones(len(source))] * depth[:, None]
        patches = rng.integers(0, 255, (len(source), 31, 31), np.uint8)
        base_texture = cv2.resize(rng.integers(0, 255, (120, 160), np.uint8), (640, 480))

        def pose(timestamp):
            elapsed = (timestamp - 1_050_000_000) / 1e9
            if mode == "rotation":
                from scipy.spatial.transform import Rotation

                return CameraExtrinsics(
                    Rotation.from_rotvec([0, 0, elapsed * 0.15]).as_matrix(), [0, 0, 0]
                )
            return CameraExtrinsics(np.eye(3), [elapsed * 2.8, 0, 0])

        def render(timestamp, intrinsics):
            extrinsics = pose(timestamp)
            if mode == "rotation":
                warp = intrinsics.K @ extrinsics.R_cw @ np.linalg.inv(camera.K)
                return cv2.warpPerspective(base_texture, warp, intrinsics.image_size)
            image = np.full((480, 640), 65, np.uint8)
            pixels = intrinsics.project(world, extrinsics)
            diameter = int(round(31 * intrinsics.fx / camera.fx)) | 1
            radius = diameter // 2
            for pixel, patch in zip(pixels, patches, strict=True):
                x, y = np.rint(pixel).astype(int)
                if radius <= x < 640 - radius and radius <= y < 480 - radius:
                    image[y - radius : y + radius + 1, x - radius : x + radius + 1] = cv2.resize(
                        patch, (diameter, diameter)
                    )
            return image

        for event in (e for e in capture.events if e["type"] == "image"):
            event["timestamp_ns"] += round(exposure_offset_s * 1e9)
            event["timestamp_source"] = "camera_frame"
            event["callback_timestamp_ns"] = event["timestamp_ns"] + 5_000_000
            metadata = event["metadata"]
            metadata.update(
                width=640,
                height=480,
                camera_id="0",
                physical_camera_id="synthetic",
                exif_orientation=1,
                rotation_degrees=0,
                target_rotation=0,
                sensor_orientation_degrees=0,
                lens_facing=1,
                is_flipped=False,
                sensor_timestamp_ns=event["timestamp_ns"],
                sensor_timestamp_clock_domain=CLOCK_DOMAIN,
            )
            assert cv2.imwrite(
                str(capture.path / event["filename"]), render(event["timestamp_ns"], jpeg_camera)
            )
        if mode == "rotation":
            for row in capture.rows:
                if row["sensor_type"] == "gyroscope":
                    row["z"] = 0.15
        records = []
        missing_frames = set(missing_frames)
        for i in range(10):
            timestamp = 1_050_000_000 + i * 100_000_000
            common = {"sequence": 0, "recorded_timestamp_ns": timestamp + 2_000_000}
            if i in missing_frames:
                records.append(
                    {
                        **common,
                        "type": "drop",
                        "first_frame_id": i,
                        "count": 1,
                        "reason": "synthetic_missing_frame",
                    }
                )
                continue
            left = 16 if geometry_change and i >= 5 else 0
            gray = render(timestamp, camera)[0:480:2, left:640:2]
            affine = np.array([[0.5, 0, -left * 0.5], [0, 0.5, 0], [0, 0, 1]])
            intrinsics = CameraIntrinsics.from_K(affine @ camera.K, gray.shape[::-1], camera_id="0")
            filename = f"tracking/frames/frame_{i:06d}.png"
            assert cv2.imwrite(str(capture.path / filename), gray)
            records.append(
                {
                    **common,
                    "type": "frame",
                    "frame_id": i,
                    "filename": filename,
                    "timestamp_ns": timestamp + (90_000_000_000 if unknown_clock else 0),
                    "clock_domain": "camera_unknown" if unknown_clock else CLOCK_DOMAIN,
                    "received_timestamp_ns": timestamp + 1_000_000,
                    "buffer_size": [640, 480],
                    "buffer_crop_region": [left, 0, 640, 480],
                    "sample_step": 2,
                    "metadata": {
                        "width": gray.shape[1],
                        "height": gray.shape[0],
                        "camera_id": "0",
                        "physical_camera_id": "synthetic",
                        "rotation_degrees": 0,
                        "target_rotation": 0,
                        "lens_facing": 1,
                        "sensor_orientation_degrees": 0,
                        "is_flipped": False,
                    },
                    "intrinsics": intrinsics.to_dict(),
                    "intrinsics_unavailable_reason": None,
                }
            )
        for seq, row in enumerate(records):
            row["sequence"] = seq
        manifest = {
            "schema_version": 1,
            "session_id": capture.metadata["session_id"],
            "state": "complete",
            "failure_reason": None,
            "records_file": "tracking/events.jsonl",
            "pixel_format": "gray8_png",
            "settings": {
                "requested_width": 640,
                "requested_height": 480,
                "max_analysis_fps": 10,
                "still_interval_s": 1,
                "writer_queue_capacity": 4,
            },
            "counts": {
                "frames": 10 - len(missing_frames),
                "dropped_frames": len(missing_frames),
                "resets": 0,
                "capture_events": 0,
            },
        }
        fixture = HybridCaptureFixture(capture, manifest, records)
        fixture.write()
        calibration = save_intrinsics(tmp_path / (capture.path.name + "_camera.yaml"), jpeg_camera)
        return SimpleNamespace(
            fixture=fixture,
            path=fixture.path,
            calibration=calibration,
            camera=camera,
            jpeg_camera=jpeg_camera,
            world=world,
            pose=pose,
        )

    return make
