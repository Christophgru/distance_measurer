import hashlib
import json
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

import cv2
import numpy as np
import pytest

from distance_measuring.camera import CameraExtrinsics, CameraIntrinsics, save_intrinsics
from distance_measuring.scene import load_scene
from distance_measuring.vision.prototype_pipeline import reconstruct_incremental
from distance_measuring.vision.tracking import TrackingConfig

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/reconstruct_incremental.py"
PROCESS_SCRIPT = SCRIPT.with_name("process_session.py")


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


@pytest.mark.parametrize("missing_last", [False, True])
def test_one_command_exports_complete_and_partial_scenes_with_blank_review(
    capture_factory, tmp_path, missing_last
):
    capture, calibration = render(capture_factory, tmp_path, missing_last=missing_last)
    before = {p: p.read_bytes() for p in capture.path.rglob("*") if p.is_file()}
    output = tmp_path / "run with spaces"
    result = subprocess.run(
        [
            sys.executable,
            str(PROCESS_SCRIPT),
            str(capture.path),
            "--output",
            str(output),
            "--calibration",
            str(calibration),
            "--initialize-motion",
            "--max-points",
            "350",
            "--measurement-reference-m",
            "0.1",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == (1 if missing_last else 0), result.stdout + result.stderr
    workflow = json.loads((output / "workflow.json").read_text())
    assert workflow["status"] == ("partial" if missing_last else "complete")
    assert [s["exit_code"] for s in workflow["stages"]] == [0, int(missing_last), 0, 0]
    assert all(s["runtime_s"] >= 0 for s in workflow["stages"])
    assert all((output / s["log_file"]).is_file() for s in workflow["stages"])
    assert (output / "web/index.html").is_file()
    assert (output / "web/measurement/index.html").is_file()
    assert json.loads((output / "inspection.json").read_text())["valid"]
    annotations = json.loads((output / "measurement/annotations.json").read_text())
    source = output / "reconstruction/scene.json"
    assert annotations["scene_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert annotations["reference"]["length_m"] == 0.1
    assert all(p["observations"] == {} for p in annotations["points"].values())
    assert not json.loads((output / "measurement/measurements.json").read_text())[
        "metric_measurement_accepted"
    ]
    assert workflow["serve_command"][-1] == str(output / "web")
    assert "http://localhost:8765/measurement/" in result.stdout
    assert before == {p: p.read_bytes() for p in before}
    repeated = subprocess.run(
        [sys.executable, str(PROCESS_SCRIPT), str(capture.path), "--output", str(output)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert repeated.returncode == 2
    assert "already exists" in repeated.stderr
    assert json.loads((output / "workflow.json").read_text()) == workflow


def test_one_command_keeps_reports_when_no_geometry_is_supported(capture_factory, tmp_path):
    capture, calibration = render(capture_factory, tmp_path, blank=True)
    output = tmp_path / "no geometry"
    result = subprocess.run(
        [
            sys.executable,
            str(PROCESS_SCRIPT),
            str(capture.path),
            "--output",
            str(output),
            "--calibration",
            str(calibration),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    report = json.loads((output / "workflow.json").read_text())
    assert report["status"] == "no_scene"
    assert [s["name"] for s in report["stages"]] == ["inspection", "reconstruction"]
    assert report["viewer"] is None
    assert (output / "reconstruction/REPORT.md").exists()
    assert not (output / "web").exists()
    assert "No supported scene:" in result.stderr


@pytest.mark.parametrize("malformed", [False, True])
def test_one_command_stops_on_inspection_errors(capture, tmp_path, malformed):
    if malformed:
        (capture.path / "session.json").write_text("[]")
    else:
        capture.metadata["sensor_counts"]["accelerometer"] += 1
        capture.write()
    output = tmp_path / "invalid"
    result = subprocess.run(
        [sys.executable, str(PROCESS_SCRIPT), str(capture.path), "--output", str(output)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == (2 if malformed else 1), result.stdout + result.stderr
    report = json.loads((output / "workflow.json").read_text())
    assert report["status"] == "inspection_failed"
    assert report["failure_reason"]
    assert len(report["stages"]) == 1
    assert not (output / "reconstruction").exists()


def test_one_command_rejects_output_alias_inside_raw(capture, tmp_path):
    alias = tmp_path / "raw alias"
    alias.symlink_to(capture.path, target_is_directory=True)
    result = subprocess.run(
        [sys.executable, str(PROCESS_SCRIPT), str(capture.path), "--output", str(alias / "run")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "outside data/raw" in result.stderr
    assert not (capture.path / "run").exists()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX SIGINT test")
def test_one_command_serves_viewer_and_retains_success_on_ctrl_c(capture_factory, tmp_path):
    capture, calibration = render(capture_factory, tmp_path)
    output = tmp_path / "served run"
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    with (tmp_path / "server.log").open("w+") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                str(PROCESS_SCRIPT),
                str(capture.path),
                "--output",
                str(output),
                "--calibration",
                str(calibration),
                "--initialize-motion",
                "--max-points",
                "350",
                "--serve",
                "--port",
                str(port),
            ],
            stdout=log,
            stderr=log,
        )
        try:
            deadline = time.monotonic() + 120
            while True:
                try:
                    with urlopen(f"http://127.0.0.1:{port}/", timeout=1) as response:
                        assert response.status == 200
                        assert b'id="scene-data"' in response.read()
                    break
                except URLError:
                    if process.poll() is not None or time.monotonic() >= deadline:
                        log.seek(0)
                        pytest.fail(log.read())
                    time.sleep(0.05)
            report = json.loads((output / "workflow.json").read_text())
            assert report["status"] == "complete"
            assert report["processing_runtime_s"] > 0
            process.send_signal(signal.SIGINT)
            assert process.wait(timeout=15) == 0
            final = json.loads((output / "workflow.json").read_text())
            assert final["status"] == "complete"
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=15)


@pytest.mark.parametrize(
    "options",
    [
        ["--port", "0"],
        ["--port", "65536"],
        ["--max-points", "0"],
        ["--max-image-size", "1"],
        ["--measurement-reference-m", "nan"],
        ["--measurement-reference-m", "-1"],
    ],
)
def test_one_command_rejects_invalid_options_before_creating_outputs(capture, tmp_path, options):
    output = tmp_path / "bad options"
    result = subprocess.run(
        [sys.executable, str(PROCESS_SCRIPT), str(capture.path), "--output", str(output), *options],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "error:" in result.stderr
    assert not output.exists()
