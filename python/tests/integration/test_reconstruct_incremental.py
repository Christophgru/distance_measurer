import hashlib
import json
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

from distance_measuring.camera import CameraExtrinsics, CameraIntrinsics, save_intrinsics
from distance_measuring.scene import load_scene
from distance_measuring.vision.prototype_pipeline import reconstruct_incremental
from distance_measuring.vision.tracking import TrackingConfig

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/reconstruct_incremental.py"


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


def render(capture_factory, tmp_path, *, blank=False, missing_last=False):
    capture = capture_factory()
    camera = CameraIntrinsics(400, 400, 319.5, 239.5, 640, 480, camera_id="0")
    rng = np.random.default_rng(31)
    source = np.array([(x, y) for y in range(55, 440, 55) for x in range(160, 610, 55)])
    depths = rng.uniform(4, 12, len(source))
    world = np.c_[camera.normalize_pixels(source), np.ones(len(source))] * depths[:, None]
    patches = rng.integers(0, 255, (len(source), 31, 31), np.uint8)
    for i, event in enumerate(e for e in capture.events if e["type"] == "image"):
        image = np.full((480, 640), 65, np.uint8)
        if not blank and not (missing_last and i == 2):
            pixels = camera.project(world, CameraExtrinsics(np.eye(3), [i * 0.7, 0, 0]))
            for pixel, patch in zip(pixels, patches, strict=True):
                x, y = np.rint(pixel).astype(int)
                image[y - 15 : y + 16, x - 15 : x + 16] = patch
        assert cv2.imwrite(str(capture.path / event["filename"]), image)
        event["metadata"].update(
            width=640,
            height=480,
            exif_orientation=1,
            rotation_degrees=0,
            sensor_orientation_degrees=0,
            lens_facing=1,
            target_rotation=0,
        )
    capture.write()
    calibration = save_intrinsics(tmp_path / "camera.yaml", camera)
    return capture, calibration


@pytest.mark.parametrize("missing_last", [False, True])
def test_real_stage_chain_preserves_raw_and_reports_complete_or_partial(
    capture_factory,
    tmp_path,
    missing_last,
):
    capture, calibration = render(capture_factory, tmp_path, missing_last=missing_last)
    hashes = {
        p: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in capture.path.rglob("*")
        if p.is_file()
    }
    output = tmp_path / "result"
    result = run(
        capture, output, "--calibration", calibration, "--initialize-motion", "--max-points", 350
    )
    assert result.returncode == (1 if missing_last else 0), result.stdout + result.stderr
    report = json.loads((output / "reconstruction.json").read_text())
    assert report["status"] == ("partial" if missing_last else "complete")
    assert report["success"] is (not missing_last)
    scene = load_scene(output / "scene.json")
    assert len(scene.cameras) == (2 if missing_last else 3)
    assert len(scene.points) >= 8
    assert report["raw_inputs_unchanged"] is True
    assert hashes == {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in hashes}
    q = report["diagnostics"]["quality"]
    assert q["reprojection_rmse_px"] < 3
    # BA changes ray angles; report the final geometry, including excluded landmarks.
    assert q["best_triangulation_angle_degrees"]["min"] == pytest.approx(
        min(p.triangulation_angle_degrees for p in scene.points)
    )
    assert q["best_triangulation_angle_degrees"]["median"] > 1
    components = report["diagnostics"]["component_runtime_s"]
    assert all(v >= 0 for v in components.values())
    assert 0 <= sum(components.values()) <= report["stage_runtime_s"]["total"]
    assert (output / "REPORT.md").exists()
    for r in report["diagnostics"]["cameras"]:
        assert r["pnp_runtime_s"] == sum(a["runtime_s"] for a in r["pnp_attempts"])
        assert len(r["refinement_rounds"]) <= 2
    assert run(capture, output).returncode == 2


@pytest.mark.parametrize("gyro_available", [True, False])
def test_blank_capture_and_missing_gyro_do_not_invent_a_scene(
    capture_factory, tmp_path, gyro_available
):
    capture, calibration = render(capture_factory, tmp_path, blank=True)
    # A missing gyro interval must remain unavailable and not become identity rotation.
    if not gyro_available:
        capture.rows = [r for r in capture.rows if r["sensor_type"] != "gyroscope"]
        capture.metadata["sensors"]["gyroscope"] = False
        capture.metadata["sensor_counts"]["gyroscope"] = 0
    capture.write()
    output = tmp_path / "blank"
    result = run(capture, output, "--calibration", calibration)
    assert result.returncode == 1, result.stdout + result.stderr
    report = json.loads((output / "reconstruction.json").read_text())
    assert report["status"] == "failed"
    assert not (output / "scene.json").exists()
    assert report["diagnostics"]["quality"]["camera_count"] == 0
    assert all(r["gyro_available"] is gyro_available for r in report["diagnostics"]["pairs"])


@pytest.mark.parametrize(
    "kwargs",
    [
        {"image_indices": [2, 0]},
        {"image_indices": [0, 0]},
        {"image_indices": [0, 99]},
        {"best_n": 0},
        {"max_image_size": 0},
        {"max_gyro_gap_s": float("nan")},
    ],
)
def test_invalid_selection_and_options_fail_before_creating_outputs(capture, tmp_path, kwargs):
    output = tmp_path / "bad"
    with pytest.raises(ValueError):
        reconstruct_incremental(capture.path, output, **kwargs)
    assert not output.exists()


def test_outputs_inside_raw_or_a_symlink_are_rejected(capture, tmp_path):
    alias = tmp_path / "alias"
    alias.symlink_to(capture.path, target_is_directory=True)
    for output in (capture.path / "output", alias / "output"):
        with pytest.raises(ValueError, match="outside data/raw"):
            reconstruct_incremental(capture.path, output)
        assert not output.exists()


def test_cli_invalid_tracking_option_is_a_clear_error(capture, tmp_path):
    result = run(capture, tmp_path / "bad", "--max-points", 0)
    assert result.returncode == 2
    assert "Cannot reconstruct capture:" in result.stderr
    assert "Traceback" not in result.stderr


def test_stage_error_is_saved_with_its_source(capture_factory, tmp_path, monkeypatch):
    from distance_measuring.vision import prototype_pipeline

    capture, calibration = render(capture_factory, tmp_path)

    def fail(*args, **kwargs):
        raise ValueError("synthetic stage error")

    monkeypatch.setattr(prototype_pipeline, "analyze_track_parallax", fail)
    output = tmp_path / "error"
    with pytest.raises(ValueError, match="synthetic stage error"):
        reconstruct_incremental(
            capture.path,
            output,
            calibration_path=calibration,
            tracking_config=TrackingConfig(max_points=120),
        )
    report = json.loads((output / "reconstruction.json").read_text())
    assert report["status"] == "error" and not report["success"]
    assert report["failed_stage"] == "gyro_parallax"
    assert (output / "tracking/tracking.json").exists()
