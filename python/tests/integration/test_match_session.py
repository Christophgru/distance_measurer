import hashlib
import json
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest
from numpy.testing import assert_allclose, assert_array_equal

from distance_measuring.camera import CameraIntrinsics, save_intrinsics
from distance_measuring.vision.pipeline import match_session

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "match_session.py"


def render_capture(capture_factory):
    """Original textured patches at varying depths, three translated pinhole views."""
    capture = capture_factory(image_count=5)
    rng = np.random.default_rng(4)
    camera = CameraIntrinsics(450, 450, 319.5, 239.5, 640, 480)
    points = rng.uniform([-2.5, -1.7, 4], [2.5, 1.7, 9], (150, 3))
    patches = [rng.integers(20, 240, (31, 31, 3), dtype=np.uint8) for _ in points]
    image_events = [event for event in capture.events if event["type"] == "image"]
    for i, event in enumerate(image_events):
        image = np.full((480, 640, 3), 127, np.uint8)
        moved = points - [i * 0.13, 0, 0]
        for point, patch in zip(camera.project(moved), patches, strict=True):
            x, y = np.rint(point).astype(int)
            if 17 < x < 622 and 17 < y < 462:
                image[y - 15 : y + 16, x - 15 : x + 16] = patch
        assert cv2.imwrite(str(capture.path / event["filename"]), image)
        event["metadata"].update(width=640, height=480, exif_orientation=1)
    capture.write()
    return capture


def run_matcher(capture, output, *options):
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(capture.path), "--output", str(output), *options],
        capture_output=True,
        text=True,
        check=False,
    )


def test_end_to_end_three_pairs_retain_indices_and_raw_capture(capture_factory, tmp_path):
    capture = render_capture(capture_factory)
    paths = [path for path in capture.path.rglob("*") if path.is_file()]
    before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    output = tmp_path / "matches"
    result = run_matcher(capture, output, "--max-features", "1200")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "raw=" in result.stdout and "retained=" in result.stdout
    assert "intrinsic source: heuristic (approximate)" in result.stdout
    report = json.loads((output / "matches.json").read_text())
    assert report["success"]
    assert report["selected_image_indices"] == [0, 1, 2]
    assert [pair["indices"] for pair in report["pairs"]] == [[0, 1], [1, 2], [0, 2]]
    assert report["coordinate_frame"] == "undistorted_working_pixels_ignore_exif"
    assert len(report["images"]) == 3
    for pair in report["pairs"]:
        assert (
            pair["raw_count"]
            >= pair["ratio_count"]
            >= pair["retained_count"]
            >= pair["inlier_count"]
            > 30
        )
        assert pair["outlier_count"] == pair["retained_count"] - pair["inlier_count"]
        assert cv2.imread(str(output / pair["rectified_image"])).shape == (1112, 1280, 3)
        assert cv2.imread(str(output / pair["debug_image"])) is not None
        with np.load(output / pair["matches_file"], allow_pickle=False) as data:
            assert data["indices"].shape == (pair["retained_count"], 2)
            assert_array_equal(data["inliers"].sum(), pair["inlier_count"])
            assert np.linalg.matrix_rank(data["F"]) == 2
            assert data["H_first"].shape == data["H_second"].shape == (3, 3)
        assert pair["rectified_vertical_error_px"]["median"] < 0.25
    assert before == {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


def test_explicit_image_selection_resize_and_calibrated_distortion(capture_factory, tmp_path):
    capture = render_capture(capture_factory)
    profile = tmp_path / "camera.yaml"
    camera = CameraIntrinsics(
        450, 450, 319.5, 239.5, 640, 480, distortion=(-0.03, 0, 0, 0, 0), camera_id="0"
    )
    save_intrinsics(profile, camera)
    output = tmp_path / "calibrated"
    result = run_matcher(
        capture,
        output,
        "--image-indices",
        "1",
        "3",
        "4",
        "--max-image-size",
        "480",
        "--calibration",
        str(profile),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads((output / "matches.json").read_text())
    assert [pair["indices"] for pair in report["pairs"]] == [[1, 3], [3, 4], [1, 4]]
    image = report["images"][0]
    assert image["stored_intrinsics"]["distortion"] == list(camera.distortion)
    assert image["working_intrinsics"]["distortion"] == [0] * 5
    assert image["working_intrinsics"]["source"] == "calibrated"
    assert_allclose(
        image["working_intrinsics"]["camera_matrix"],
        [[337.5, 0, 239.5], [0, 337.5, 179.5], [0, 0, 1]],
    )


def test_blank_images_report_failure_and_write_diagnostics(capture, tmp_path):
    output = tmp_path / "blank"
    output.mkdir()
    stale = output / "pair_000_001_rectified.jpg"
    stale.write_bytes(b"old output")
    result = run_matcher(capture, output)
    assert result.returncode == 1, result.stderr
    assert "At least eight" in result.stdout
    report = json.loads((output / "matches.json").read_text())
    assert not report["success"]
    assert not stale.exists()
    for pair in report["pairs"]:
        assert pair["rectified_image"] is None
        assert pair["F"] is None
        assert (output / pair["debug_image"]).is_file()
        with np.load(output / pair["matches_file"]) as matches:
            assert matches["indices"].shape == (0, 2)


def test_stored_image_orientation_is_preserved(capture, tmp_path):
    report = match_session(capture.path, tmp_path / "orientation")
    # Fixture JPEG EXIF orientation=6 would swap 80x48 if automatically applied.
    assert report["images"][0]["working_intrinsics"]["image_width"] == 80
    assert report["images"][0]["working_intrinsics"]["image_height"] == 48


@pytest.mark.parametrize(
    "options",
    [
        ("--image-indices", "0", "0", "1"),
        ("--image-indices", "0", "1", "9"),
        ("--ratio", "nan"),
        ("--threshold-px", "0"),
        ("--max-image-size", "0"),
        ("--seed", "-1"),
    ],
)
def test_cli_bad_options_have_actionable_errors(capture, tmp_path, options):
    output = tmp_path / "bad_options"
    result = run_matcher(capture, output, *options)
    assert result.returncode == 2
    assert "Cannot match session:" in result.stderr
    assert "Traceback" not in result.stderr
    assert not output.exists()


def test_raw_outputs_and_symlink_alias_are_rejected(capture, tmp_path):
    for output in (capture.path / "matches", tmp_path / "alias" / "matches"):
        if output.parent.name == "alias":
            output.parent.symlink_to(capture.path, target_is_directory=True)
        result = run_matcher(capture, output)
        assert result.returncode == 2
        assert "outside data/raw" in result.stderr
        assert not output.exists()
