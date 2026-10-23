"""A geometry failure must abort ruler/acceleration stages reproducibly."""

import json
import subprocess
import sys
from pathlib import Path

import numpy as np

from distance_measuring.camera import CameraExtrinsics, CameraIntrinsics

SCRIPT = Path(__file__).resolve().parents[3] / "tools/validation/reconstruct_visual.py"


def test_disconnected_cameras_write_failed_prerequisite_without_a_scene(tmp_path):
    source = tmp_path / "matching"
    source.mkdir()
    matches = source / "correspondences.json"
    K = CameraIntrinsics(100, 100, 159.5, 119.5, 320, 240)
    matches.write_text(
        json.dumps(
            {
                "session_id": "synthetic",
                "review_id": "original",
                "image_ids": [0, 1],
                "images": [{"image_id": i, "working_intrinsics": K.to_dict()} for i in (0, 1)],
                "input_sha256": {},
                "pairwise_matches": [],
            }
        )
    )
    output = tmp_path / "cameras"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(matches), "--output", str(output)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2 and "Disconnected" in result.stderr
    report = json.loads((output / "camera_validation.json").read_text())
    assert report["camera_fit_sources"] == "images_only_no_ruler_no_imu"
    assert report["camera_quality"]["passed"] is False
    assert report["metric_scale_accepted"] is False
    assert report["ruler_reference_status"] == "aborted_before_ruler_triangulation"
    assert report["acceleration_scale_status"] == "not_run_camera_prerequisite_failed"
    assert not (output / "scene.json").exists()


def test_subset_cli_keeps_a_valid_pair_and_reports_an_excluded_image(tmp_path):
    source = tmp_path / "matching"
    source.mkdir()
    matches = source / "correspondences.json"
    K = CameraIntrinsics(600, 600, 799.5, 599.5, 1600, 1200)
    world = np.random.default_rng(92).uniform([-1, -1, 4], [1, 1, 8], (80, 3))
    pixels = [K.project(world, CameraExtrinsics(np.eye(3), C)) for C in ([0, 0, 0], [1, 0, 0])]
    matches.write_text(
        json.dumps(
            {
                "session_id": "synthetic_subset",
                "review_id": "original",
                "image_ids": [0, 1, 2],
                "images": [{"image_id": i, "working_intrinsics": K.to_dict()} for i in range(3)],
                "input_sha256": {},
                "pairwise_matches": [
                    {
                        "usable_pairwise": True,
                        "group_ids": [k, None, k],
                        "working_pixels": [pixels[0][k].tolist(), None, pixels[1][k].tolist()],
                        "correlation": {"score": 0.99},
                    }
                    for k in range(80)
                ],
            }
        )
    )
    output = tmp_path / "cameras"
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            str(matches),
            "--output",
            str(output),
            "--allow-unusable-images",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads((output / "camera_validation.json").read_text())
    assert report["retained_image_ids"] == [0, 2]
    assert [r["image_id"] for r in report["excluded_images"]] == [1]
    assert report["camera_quality"]["passed"]
    assert not report["metric_scale_applied"]
    assert len(json.loads((output / "scene.json").read_text())["cameras"]) == 2
