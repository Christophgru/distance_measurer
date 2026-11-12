import hashlib
import json
import struct
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np
from scipy.spatial.transform import Rotation

from distance_measuring.camera import CameraExtrinsics, CameraIntrinsics, load_intrinsics
from distance_measuring.camera.calibration_audit import detect_capture_boards
from distance_measuring.dataset import load_session

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "tools/calibration/intake_capture.py"


def run(*args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *map(str, args)], text=True, capture_output=True, check=False
    )


def test_intake_detects_native_EXIF_pixels_and_exports_training_only_profile(
    capture_factory, tmp_path
):
    capture = capture_factory(image_count=6)
    camera = CameraIntrinsics(800, 810, 399.5, 299.5, 800, 600)
    square = 0.025
    texture = np.zeros((600, 800), np.uint8)
    for row in range(6):
        for col in range(8):
            texture[row * 100 : (row + 1) * 100, col * 100 : (col + 1) * 100] = (
                255 if (row + col) % 2 else 0
            )
    outer = np.array(
        [
            [-square, -square, 0],
            [7 * square, -square, 0],
            [7 * square, 5 * square, 0],
            [-square, 5 * square, 0],
        ]
    )
    source = np.array([[0, 0], [800, 0], [800, 600], [0, 600]], np.float32)
    angles = [
        [0.3, -0.25, 0.1],
        [-0.35, 0.2, -0.1],
        [0.2, 0.4, 0.15],
        [-0.3, -0.3, 0.05],
        [0.4, 0.15, -0.1],
        [-0.2, 0.35, 0.05],
    ]
    # Valid JPEG EXIF=6, while all camera/calibration coordinates stay native.
    exif = (
        b"Exif\x00\x00II\x2a\x00\x08\x00\x00\x00"
        + struct.pack("<H", 1)
        + struct.pack("<HHI", 0x0112, 3, 1)
        + struct.pack("<H", 6)
        + b"\x00" * 6
    )
    for event in capture.events:
        if event["type"] != "image":
            continue
        i = event["index"]
        rotation = Rotation.from_rotvec(angles[i]).as_matrix()
        pose = CameraExtrinsics(rotation, -rotation.T @ np.array([-0.08, -0.05, 0.6 + i * 0.01]))
        h = cv2.getPerspectiveTransform(source, camera.project(outer, pose).astype(np.float32))
        image = cv2.warpPerspective(texture, h, camera.image_size, borderValue=255)
        ok, encoded = cv2.imencode(".jpg", image)
        assert ok
        jpeg = encoded.tobytes()
        (capture.path / event["filename"]).write_bytes(
            jpeg[:2] + b"\xff\xe1" + struct.pack(">H", len(exif) + 2) + exif + jpeg[2:]
        )
        event["metadata"].update(
            width=800,
            height=600,
            physical_camera_id="lens0",
            zoom_ratio=1.0,
            crop_region=[0, 0, 800, 600],
            focus_distance_diopters=1.0,
        )
    capture.write()
    measurement = capture_factory()
    measurement.metadata["session_id"] = "synthetic_measurement"
    for event in measurement.events:
        if event["type"] == "image":
            event["metadata"].update(
                width=800,
                height=600,
                physical_camera_id="lens0",
                zoom_ratio=1.0,
                crop_region=[0, 0, 800, 600],
                focus_distance_diopters=1.0,
            )
            (measurement.path / event["filename"]).write_bytes(
                (capture.path / "images/image_000.jpg").read_bytes()
            )
    measurement.write()
    session = load_session(capture.path)
    detections, rows = detect_capture_boards(session, 7, 5, max_detection_size=400)
    assert len(detections) == 6 and all(r["native_size"] == [800, 600] for r in rows)
    assert all(r["detection_size"] == [400, 300] for r in rows)
    for xy in detections.values():
        assert xy.shape == (35, 2)
    before = {
        str(p): hashlib.sha256(p.read_bytes()).hexdigest()
        for recording in [capture, measurement]
        for p in recording.path.rglob("*")
        if p.is_file()
    }
    out = tmp_path / "intake"
    result = run(
        capture.path,
        "--measurement-session",
        measurement.path,
        "--columns",
        7,
        "--rows",
        5,
        "--output",
        out,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    data = json.loads((out / "intake.json").read_text())
    assert data["calibration_audit_passed"] and data["candidate_profile_saved"]
    assert data["training_image_ids"] == [0, 1, 3, 4] and data["reserved_image_ids"] == [2, 5]
    assert not data["profile_fitted_on_reserved_views"]
    assert data["ready_for_calibrated_reconstruction"] and not data["reconstruction_started"]
    assert data["capture_settings_comparison"]["status"] == "matched"
    assert len(data["next_commands"]) == 1
    assert "--calibration" in data["next_commands"][0]
    assert not data["metric_measurement_accepted"]
    assert data["input_sha256"] == before
    assert all(hashlib.sha256(Path(p).read_bytes()).hexdigest() == h for p, h in before.items())
    profile = load_intrinsics(out / "candidate_camera.yaml")
    np.testing.assert_allclose([profile.fx, profile.fy], [camera.fx, camera.fy], rtol=0.03)
    assert profile.image_size == (800, 600)


def test_no_board_reports_failure_without_profile_or_reconstruction(capture, tmp_path):
    out = tmp_path / "intake"
    result = run(capture.path, "--output", out)
    assert result.returncode == 1, result.stderr
    data = json.loads((out / "intake.json").read_text())
    assert data["detected_views"] == 0
    assert not data["candidate_profile_saved"] and not (out / "candidate_camera.yaml").exists()
    assert not data["next_commands"] and not data["metric_measurement_accepted"]
    again = run(capture.path, "--output", out)
    assert again.returncode == 2 and "fresh directory" in again.stderr


def test_output_protection_happens_before_input_read(capture):
    result = run(capture.path, "--output", capture.path / "audit")
    assert result.returncode == 2 and "outside raw" in result.stderr
    assert not (capture.path / "audit").exists()
    result = run(capture.path, "--output", ROOT / "data/raw/must_not_be_created")
    assert result.returncode == 2 and "outside raw" in result.stderr
    assert not (ROOT / "data/raw/must_not_be_created").exists()
