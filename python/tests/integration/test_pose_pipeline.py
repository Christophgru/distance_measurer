import hashlib
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from numpy.testing import assert_allclose, assert_array_equal
from scipy.spatial.transform import Rotation

from distance_measuring.camera import CameraExtrinsics, CameraIntrinsics
from distance_measuring.vision import pose_pipeline
from distance_measuring.vision.pose_pipeline import estimate_poses

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "estimate_poses.py"


@pytest.fixture
def m4_export(tmp_path):
    root = tmp_path / "m4"
    root.mkdir()
    points = np.random.default_rng(18).uniform([-2, -1.5, 4], [2, 1.5, 9], (180, 3))
    ids = [4, 7, 9]
    cameras = [
        CameraIntrinsics(900 + i * 100, 870 + i * 50, 639.5, 479.5, 1280, 960, skew=i)
        for i in range(3)
    ]
    poses = [
        CameraExtrinsics(np.eye(3), np.zeros(3)),
        CameraExtrinsics(
            Rotation.from_rotvec([0.04, -0.08, 0.01]).as_matrix(), np.array([0.6, 0.02, 0.03])
        ),
        CameraExtrinsics(
            Rotation.from_rotvec([-0.03, -0.12, 0.05]).as_matrix(), np.array([1.2, 0.2, 0.05])
        ),
    ]
    images = []
    for index, camera, pose in zip(ids, cameras, poses, strict=True):
        name = f"image_{index:03d}_features.npz"
        np.savez_compressed(root / name, points=camera.project(points, pose))
        images.append(
            {
                "index": index,
                "features_file": name,
                "working_intrinsics": camera.to_dict(),
                "stored_intrinsics": camera.to_dict(),
                "feature_count": len(points),
            }
        )
    pairs = []
    for i, j in ((4, 7), (7, 9), (4, 9)):
        indices = np.c_[np.arange(len(points)), np.arange(len(points))]
        indices[-20:, 1] = indices[-20:, 1][::-1]
        mask = np.ones(len(points), bool)
        mask[::9] = False
        name = f"pair_{i:03d}_{j:03d}_matches.npz"
        np.savez_compressed(root / name, indices=indices, inliers=mask)
        pairs.append(
            {
                "indices": [i, j],
                "matches_file": name,
                "retained_count": len(indices),
                "inlier_count": int(mask.sum()),
            }
        )
    manifest = {
        "schema_version": 1,
        "session_id": "synthetic_pose_capture",
        "selected_image_indices": ids,
        "coordinate_frame": "undistorted_working_pixels_ignore_exif",
        "images": images,
        "pairs": pairs,
    }
    (root / "matches.json").write_text(json.dumps(manifest))
    return root, manifest, poses


def run_poses(root, output, *options):
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(root), "--output", str(output), *options],
        text=True,
        capture_output=True,
        check=False,
    )


def test_cli_loads_calibrated_M4_and_exports_three_world_frames(m4_export, tmp_path):
    root, source, expected = m4_export
    before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in root.iterdir()}
    output = tmp_path / "m5"
    result = run_poses(root, output)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "relative baseline scales unresolved" in result.stdout
    report = json.loads((output / "poses.json").read_text())
    assert report["success"]
    assert report["selected_image_indices"] == [4, 7, 9]
    assert report["scale_status"] == "independent_unit_baselines_relative_scales_unresolved"
    assert (output / "camera_frames.png").read_bytes().startswith(b"\x89PNG")
    assert len(report["cameras"]) == 3
    first = report["cameras"][0]
    assert_array_equal(first["C_w"], [0, 0, 0])
    assert_array_equal(first["R_cw"], np.eye(3))
    for i, camera in enumerate(report["cameras"]):
        R = np.array(camera["R_cw"])
        assert Rotation.from_matrix(R @ expected[i].R_cw.T).magnitude() < 1e-5
        assert_allclose(camera["O_cw"], -R @ np.array(camera["C_w"]), atol=1e-12)
        assert np.array(camera["M"]).shape == (3, 4)
        assert np.array(camera["T_scene"]).shape == (4, 4)
    assert report["closure"]["rotation_error_degrees"] < 0.001
    for pair, original in zip(report["pairs"], source["pairs"], strict=True):
        assert pair["pose_inlier_count"] > 130
        with np.load(output / pair["arrays_file"]) as data:
            assert data["indices"].shape == (180, 2)
            with np.load(root / original["matches_file"]) as old:
                assert_array_equal(data["indices"], old["indices"])
                assert_array_equal(data["m4_inliers"], old["inliers"])
            assert np.all(~data["pose_inliers"] | data["essential_inliers"])
            assert np.all(~data["essential_inliers"] | data["m4_inliers"])
            assert np.linalg.norm(data["t_second_first_direction"]) == pytest.approx(1)
    assert before == {
        path: hashlib.sha256(path.read_bytes()).hexdigest() for path in root.iterdir()
    }


def test_failed_pair_preserves_diagnostics_and_removes_stale_plot(m4_export, tmp_path):
    root, source, _ = m4_export
    pair = source["pairs"][0]
    path = root / pair["matches_file"]
    with np.load(path) as data:
        indices = data["indices"]
    np.savez_compressed(path, indices=indices, inliers=np.zeros(180, bool))
    pair["inlier_count"] = 0
    (root / "matches.json").write_text(json.dumps(source))
    output = tmp_path / "failed"
    output.mkdir()
    (output / "camera_frames.png").write_bytes(b"stale")
    result = run_poses(root, output)
    assert result.returncode == 1, result.stderr
    report = json.loads((output / "poses.json").read_text())
    assert not report["success"]
    assert len(report["cameras"]) == 1
    assert report["pairs"][0]["failure"]
    assert report["pairs"][0]["R_second_first"] is None
    assert not (output / "camera_frames.png").exists()


def test_inconsistent_rotation_loop_is_explicitly_flagged(m4_export, tmp_path, monkeypatch):
    recover = pose_pipeline.recover_relative_pose
    calls = 0

    def inconsistent_direct_pair(*args, **kwargs):
        nonlocal calls
        calls += 1
        result = recover(*args, **kwargs)
        if calls == 3:
            result = replace(
                result, R=Rotation.from_euler("z", 20, degrees=True).as_matrix() @ result.R
            )
        return result

    monkeypatch.setattr(pose_pipeline, "recover_relative_pose", inconsistent_direct_pair)
    report = estimate_poses(m4_export[0], tmp_path / "loop_warning")
    assert report["success"]  # Frames were produced; quality is a separate finding.
    assert report["quality_status"] == "rotation_loop_inconsistent"
    assert not report["closure"]["rotation_loop_consistent"]
    assert report["closure"]["rotation_error_degrees"] == pytest.approx(20, abs=0.001)
    assert any(
        "not a verified consistent reconstruction" in warning for warning in report["warnings"]
    )


@pytest.mark.parametrize(
    "change", ["frame", "distortion", "path", "count", "pair", "index", "mask"]
)
def test_rejects_inconsistent_coordinate_frames_or_artifacts(m4_export, tmp_path, change):
    root, source, _ = m4_export
    pair = source["pairs"][0]
    if change == "frame":
        source["coordinate_frame"] = "rectified_pixels"
    elif change == "distortion":
        source["images"][0]["working_intrinsics"]["distortion"][0] = 0.1
    elif change == "path":
        source["images"][0]["features_file"] = "../outside.npz"
    elif change == "count":
        pair["inlier_count"] -= 1
    elif change == "pair":
        source["pairs"] = source["pairs"][:2]
    else:
        with np.load(root / pair["matches_file"]) as data:
            indices, mask = data["indices"], data["inliers"]
        if change == "index":
            indices[0, 0] = 1000
        else:
            mask = mask.astype(np.uint8)
        np.savez_compressed(root / pair["matches_file"], indices=indices, inliers=mask)
    (root / "matches.json").write_text(json.dumps(source))
    result = run_poses(root, tmp_path / "invalid")
    assert result.returncode == 2
    assert "Cannot estimate poses:" in result.stderr
    assert "Traceback" not in result.stderr
    assert not (tmp_path / "invalid").exists()


@pytest.mark.parametrize(
    "options", [("--threshold-px", "nan"), ("--min-parallax-degrees", "0"), ("--seed", "-1")]
)
def test_invalid_cli_options(m4_export, tmp_path, options):
    result = run_poses(m4_export[0], tmp_path / "bad_options", *options)
    assert result.returncode == 2
    assert "Traceback" not in result.stderr


def test_raw_and_source_outputs_including_symlink_are_rejected(m4_export, tmp_path):
    root, _, _ = m4_export
    alias = tmp_path / "alias"
    alias.symlink_to(root, target_is_directory=True)
    raw_output = SCRIPT.parents[2] / "data" / "raw" / "m5_forbidden"
    for output in (root / "m5", alias / "m5", raw_output):
        with pytest.raises(ValueError, match="outside data/raw"):
            estimate_poses(root, output)
        assert not output.exists()
