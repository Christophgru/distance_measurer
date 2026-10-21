import hashlib
import json
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest
import yaml
from numpy.testing import assert_allclose
from scipy.spatial.transform import Rotation

from distance_measuring.camera import (
    CameraExtrinsics,
    CameraIntrinsics,
    load_intrinsics,
    save_intrinsics,
)

ROOT = Path(__file__).resolve().parents[3]
CHECKERBOARD = ROOT / "tools/calibration/calibrate_checkerboard.py"
CORRESPONDENCES = ROOT / "tools/calibration/calibrate_correspondences.py"
INSPECT_CAMERA = ROOT / "python/scripts/inspect_camera.py"


def run_script(script, *arguments):
    return subprocess.run(
        [sys.executable, str(script), *map(str, arguments)],
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture(scope="module")
def checkerboard_result(tmp_path_factory):
    root = tmp_path_factory.mktemp("checkerboard")
    images = root / "images"
    images.mkdir()
    columns, rows, square = 7, 5, 0.025
    texture = np.zeros(((rows + 1) * 100, (columns + 1) * 100), dtype=np.uint8)
    for row in range(rows + 1):
        for column in range(columns + 1):
            texture[row * 100 : (row + 1) * 100, column * 100 : (column + 1) * 100] = (
                255 if (row + column) % 2 else 0
            )
    truth = CameraIntrinsics(800, 810, 399.5, 299.5, 800, 600)
    outer = np.array(
        [
            [-square, -square, 0],
            [columns * square, -square, 0],
            [columns * square, rows * square, 0],
            [-square, rows * square, 0],
        ]
    )
    source = np.array(
        [
            [0, 0],
            [texture.shape[1], 0],
            [texture.shape[1], texture.shape[0]],
            [0, texture.shape[0]],
        ],
        np.float32,
    )
    views = [
        ([0.3, -0.25, 0.1], [-0.08, -0.05, 0.6]),
        ([-0.35, 0.2, -0.1], [-0.07, -0.05, 0.65]),
        ([0.2, 0.4, 0.15], [-0.1, -0.06, 0.6]),
        ([-0.3, -0.3, 0.05], [-0.04, -0.04, 0.7]),
        ([0.4, 0.15, -0.1], [-0.08, -0.03, 0.55]),
    ]
    for index, (rvec, translation) in enumerate(views):
        R_cw = Rotation.from_rotvec(rvec).as_matrix()
        pose = CameraExtrinsics(R_cw, -R_cw.T @ np.array(translation))
        H = cv2.getPerspectiveTransform(source, truth.project(outer, pose).astype(np.float32))
        image = cv2.warpPerspective(texture, H, truth.image_size, borderValue=255)
        assert cv2.imwrite(str(images / f"board_{index:03d}.png"), image)
    assert cv2.imwrite(str(images / "blank.png"), np.full((600, 800), 255, np.uint8))
    hashes = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in images.iterdir()}
    output = root / "calibration.yaml"
    result = run_script(
        CHECKERBOARD,
        images,
        "--columns",
        columns,
        "--rows",
        rows,
        "--square-size-m",
        square,
        "--camera-id",
        "test",
        "--output",
        output,
    )
    assert result.returncode == 0, result.stderr
    assert hashes == {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in hashes}
    return output, truth, result


def test_checkerboard_folder_command_creates_usable_yaml(checkerboard_result):
    output, truth, result = checkerboard_result
    loaded = load_intrinsics(output)
    assert loaded.source == "calibrated"
    assert not loaded.approximate
    assert loaded.camera_id == "test"
    assert_allclose([loaded.fx, loaded.fy], [truth.fx, truth.fy], rtol=0.03)
    assert "Intrinsic source: calibrated" in result.stdout
    assert "Solver: eigh" in result.stdout


def test_export_has_lecture_matrices_and_quality_diagnostics(checkerboard_result):
    output, _, _ = checkerboard_result
    data = yaml.safe_load(output.read_text())
    calibration = data["calibration"]
    assert calibration["coordinate_units"] == "m"
    assert calibration["rmse_px"] < 0.5
    assert calibration["rmse_px"] <= calibration["initial_rmse_px"]
    assert calibration["diagnostics"]["skipped_images"] == ["blank.png"]
    assert len(calibration["views"]) == 5
    for view in calibration["views"]:
        pose = CameraExtrinsics(np.array(view["R_cw"]), np.array(view["C_w"]))
        assert_allclose(view["O_cw"], pose.O_cw)
        assert_allclose(view["T_scene"], pose.T_scene)
        assert_allclose(view["M"], pose.projection_matrix(data["camera_matrix"]))


def test_3d_correspondence_command_uses_eigen_solve(tmp_path):
    camera = CameraIntrinsics(800, 810, 320, 240, 640, 480, skew=2)
    points = np.random.default_rng(34).uniform(-1, 1, (50, 3))
    pose = CameraExtrinsics(np.eye(3), np.array([0.0, 0.0, -4.0]))
    source = tmp_path / "known_points.json"
    source.write_text(
        json.dumps(
            {
                "world_points": points.tolist(),
                "image_points": camera.project(points, pose).tolist(),
                "image_width": 640,
                "image_height": 480,
                "coordinate_units": "m",
            }
        )
    )
    output = tmp_path / "camera.yaml"
    result = run_script(CORRESPONDENCES, source, "--output", output)
    assert result.returncode == 0, result.stderr
    assert "solver: eigh" in result.stdout
    assert_allclose(load_intrinsics(output).K, camera.K, atol=1e-6)


def test_coplanar_3d_input_fails_clearly(tmp_path):
    points = [[column, row, 0] for row in range(3) for column in range(3)]
    path = tmp_path / "coplanar.json"
    path.write_text(
        json.dumps(
            {
                "world_points": points,
                "image_points": [p[:2] for p in points],
                "image_width": 100,
                "image_height": 100,
            }
        )
    )
    result = run_script(CORRESPONDENCES, path, "--output", tmp_path / "out.yaml")
    assert result.returncode == 1
    assert "coplanar" in result.stderr
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize("source", ["metadata-derived", "heuristic", "calibrated"])
def test_camera_inspector_clearly_reports_intrinsic_source(capture, tmp_path, source):
    arguments = []
    if source == "metadata-derived":
        for event in capture.events:
            if event["type"] == "image":
                event["metadata"].update(sensor_width_mm=6.4, sensor_height_mm=3.84)
        capture.write()
    elif source == "calibrated":
        path = save_intrinsics(
            tmp_path / "camera.yaml", CameraIntrinsics(100, 101, 39.5, 23.5, 80, 48, camera_id="0")
        )
        arguments = ["--calibration", path]
    report = tmp_path / "processed" / "camera.json"
    result = run_script(INSPECT_CAMERA, capture.path, *arguments, "--output", report)
    assert result.returncode == 0, result.stderr
    assert f"Intrinsic source: {source}" in result.stdout
    data = json.loads(report.read_text())
    assert data["source"] == source
    assert data["approximate"] == (source != "calibrated")


def test_camera_report_cannot_overwrite_raw_session(capture):
    result = run_script(INSPECT_CAMERA, capture.path, "--output", capture.path / "session.json")
    assert result.returncode == 1
    assert "outside the raw session" in result.stderr


@pytest.mark.parametrize("script", [CHECKERBOARD, CORRESPONDENCES])
def test_calibration_commands_protect_repository_raw_data(tmp_path, script):
    output = ROOT / "data/raw/must_not_be_created.yaml"
    arguments = [tmp_path / "input.json", "--output", output]
    if script == CHECKERBOARD:
        arguments.extend(["--columns", 7, "--rows", 5, "--square-size-m", 0.025])
    result = run_script(script, *arguments)
    assert result.returncode == 1
    assert "outside data/raw" in result.stderr
    assert not output.exists()
