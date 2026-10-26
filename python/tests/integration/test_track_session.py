import hashlib
import json
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

from distance_measuring.camera import CameraIntrinsics, save_intrinsics

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/track_session.py"


def run(capture, output, *options):
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            str(capture.path),
            "--output",
            str(output),
            *map(str, options),
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def render(capture_factory):
    capture = capture_factory(image_count=5)
    texture = cv2.resize(
        np.random.default_rng(5).integers(0, 256, (60, 90), dtype=np.uint8),
        (360, 240),
        interpolation=cv2.INTER_CUBIC,
    )
    images = [e for e in capture.events if e["type"] == "image"]
    for i, image in enumerate(images):
        pixels = cv2.warpAffine(texture, np.array([[1.0, 0, i * 2], [0, 1.0, 0]]), (360, 240))
        assert cv2.imwrite(str(capture.path / image["filename"]), pixels)
        image["metadata"].update(width=360, height=240, exif_orientation=1, rotation_degrees=0)
    capture.write()
    return capture


def test_ordered_capture_exports_persistent_tracks_masks_and_debug_without_touching_raw(
    capture_factory, tmp_path
):
    capture = render(capture_factory)
    paths = [p for p in capture.path.rglob("*") if p.is_file()]
    hashes = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    output = tmp_path / "tracked"
    calibration = save_intrinsics(
        tmp_path / "camera.yaml", CameraIntrinsics(300, 305, 179.5, 119.5, 360, 240, camera_id="0")
    )
    result = run(capture, output, "--max-points", 120, "--calibration", calibration)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "median FB=" in result.stdout and "flow=" in result.stdout
    report = json.loads((output / "tracking.json").read_text())
    assert report["coordinate_frame"] == "undistorted_working_pixels_ignore_exif"
    assert report["summary"]["tracks_spanning_sequence"] > 80
    assert len(report["pairs"]) == 4
    observations = [
        json.loads(line) for line in (output / "observations.jsonl").read_text().splitlines()
    ]
    assert max(o["age"] for o in observations) == 5
    assert all(o["response"] > 0 for o in observations)
    for pair in report["pairs"]:
        assert pair["accepted_count"] + pair["rejected_count"] == pair["input_count"]
        assert sum(pair["rejection_counts"].values()) == pair["rejected_count"]
        with np.load(output / pair["arrays_file"], allow_pickle=False) as arrays:
            assert len(arrays["track_ids"]) == pair["input_count"]
            assert arrays["accepted"].sum() == pair["accepted_count"]
            assert (arrays["forward_backward_error_px"][arrays["accepted"]] <= 1.5).all()
        assert cv2.imread(str(output / pair["debug_image"])) is not None
    assert report["images"][0]["working_intrinsics"]["source"] == "calibrated"
    assert hashes == {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    assert run(capture, output).returncode == 2  # Preserve existing results.


def test_blank_capture_writes_current_diagnostics_without_fabricating_tracks(capture, tmp_path):
    output = tmp_path / "blank"
    result = run(capture, output)
    assert result.returncode == 1, result.stdout + result.stderr
    report = json.loads((output / "tracking.json").read_text())
    assert not report["success"] and report["summary"]["track_count"] == 0
    assert all(p["median_forward_backward_error_px"] is None for p in report["pairs"])
    assert (output / "observations.jsonl").read_text() == ""


@pytest.mark.parametrize(
    "options",
    [
        ("--image-indices", 2, 0, 1),
        ("--image-indices", 0, 0),
        ("--image-indices", 0, 9),
        ("--max-points", 0),
        ("--fb-threshold-px", "nan"),
        ("--replenish-ratio", 0),
    ],
)
def test_invalid_selections_and_options_are_clear_errors(capture, tmp_path, options):
    output = tmp_path / "bad"
    result = run(capture, output, *options)
    assert result.returncode == 2 and "Cannot track session:" in result.stderr
    assert "Traceback" not in result.stderr
    assert not output.exists()


def test_raw_and_symlink_outputs_are_rejected(capture, tmp_path):
    alias = tmp_path / "alias"
    alias.symlink_to(capture.path, target_is_directory=True)
    for output in (capture.path / "tracks", alias / "tracks"):
        result = run(capture, output)
        assert result.returncode == 2 and "outside data/raw" in result.stderr
        assert not output.exists()
