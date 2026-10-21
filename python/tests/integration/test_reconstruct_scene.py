import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from numpy.testing import assert_allclose
from scipy.spatial.transform import Rotation

from distance_measuring.camera import CameraExtrinsics, CameraIntrinsics
from distance_measuring.scene import Scene, load_scene
from distance_measuring.scene.reconstruction import reconstruct_scene

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "reconstruct_scene.py"


@pytest.fixture
def reconstruction_inputs(tmp_path):
    m4, m5 = tmp_path / "m4", tmp_path / "m5"
    m4.mkdir()
    m5.mkdir()
    points = np.random.default_rng(12).uniform([-1, -1, 4], [1, 1, 8], (30, 3))
    ids = [4, 7, 9]
    images, cameras = [], []
    for i, index in enumerate(ids):
        K = CameraIntrinsics(900 + 100 * i, 870 + 80 * i, 639.5, 479.5, 1280, 960, skew=i)
        pose = CameraExtrinsics(
            Rotation.from_rotvec([0.02 * i, -0.05 * i, 0.01 * i]).as_matrix(),
            np.array([float(i), 0, 0]),
        )
        filename = f"image_{index:03d}_features.npz"
        np.savez_compressed(m4 / filename, points=K.project(points, pose))
        images.append(
            {
                "index": index,
                "features_file": filename,
                "working_intrinsics": K.to_dict(),
                "stored_intrinsics": K.to_dict(),
            }
        )
        cameras.append(
            {
                "index": index,
                "R_cw": pose.R_cw.tolist(),
                "C_w": pose.C_w.tolist(),
                "O_cw": pose.O_cw.tolist(),
                "T_scene": pose.T_scene.tolist(),
                "M": pose.projection_matrix(K.K).tolist(),
            }
        )
    pairs4, pairs5 = [], []
    for pair, excluded in zip(
        ((4, 7), (7, 9), (4, 9)), ((27,), (24, 26), (24, 26, 27)), strict=True
    ):
        indices = np.c_[np.arange(30), np.arange(30)]
        m4_mask = np.arange(30) < 28
        mask = m4_mask.copy()
        mask[[25, *excluded]] = False
        name4 = f"pair_{pair[0]}_{pair[1]}_matches.npz"
        name5 = f"pair_{pair[0]}_{pair[1]}_pose.npz"
        np.savez_compressed(m4 / name4, indices=indices, inliers=m4_mask)
        np.savez_compressed(
            m5 / name5,
            indices=indices,
            m4_inliers=m4_mask,
            essential_inliers=m4_mask,
            pose_inliers=mask,
            sampson_error_normalized=np.zeros(30),
        )
        pairs4.append(
            {
                "indices": list(pair),
                "matches_file": name4,
                "retained_count": 30,
                "inlier_count": int(m4_mask.sum()),
            }
        )
        pairs5.append(
            {
                "indices": list(pair),
                "arrays_file": name5,
                "pose_inlier_count": int(mask.sum()),
                "threshold_normalized": 0.0015,
            }
        )
    source = {
        "schema_version": 1,
        "session_id": "synthetic_reconstruction",
        "selected_image_indices": ids,
        "coordinate_frame": "undistorted_working_pixels_ignore_exif",
        "images": images,
        "pairs": pairs4,
    }
    poses = {
        "schema_version": 1,
        "session_id": source["session_id"],
        "selected_image_indices": ids,
        "coordinate_convention": "world_to_camera_X_right_Y_down_Z_forward",
        "images": copy.deepcopy(images),
        "cameras": cameras,
        "pairs": pairs5,
        "success": True,
        "scale_status": "independent_unit_baselines_relative_scales_unresolved",
        "quality_status": "pairwise_pose_checks_passed",
        "closure": {"rotation_error_degrees": 0},
        "warnings": [],
        "baseline_convention": {"4-7": 1.0, "7-9": 1.0},
    }
    (m4 / "matches.json").write_text(json.dumps(source))
    (m5 / "poses.json").write_text(json.dumps(poses))
    return m4, m5, points


def run_reconstruction(m4, m5, output, *options):
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(m4), str(m5), "--output", str(output), *options],
        text=True,
        capture_output=True,
        check=False,
    )


def test_cli_reconstructs_known_scene_and_round_trips_without_touching_inputs(
    reconstruction_inputs, tmp_path
):
    m4, m5, points = reconstruction_inputs
    paths = list(m4.iterdir()) + list(m5.iterdir())
    before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    output = tmp_path / "m6"
    result = run_reconstruction(m4, m5, output)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "tracks: 27; points: 27" in result.stdout
    scene = load_scene(output / "scene.json")
    report = json.loads((output / "triangulation.json").read_text())
    assert report["summary"]["track_view_counts"] == {"2": 3, "3": 24}
    assert report["summary"]["reprojection_rmse_px"] < 1e-8
    assert scene.metadata["scale_status"] == "independent_unit_baselines_relative_scales_unresolved"
    assert scene.metadata["lecture_reference"]["page_numbering"] == "1-based PPTX slide order"
    assert any("relative baseline ratio" in warning for warning in scene.metadata["warnings"])
    tracks = {track.track_id: track for track in scene.tracks}
    for point in scene.points:
        feature = tracks[point.track_id].observations[0].feature_id
        assert_allclose(point.position, points[feature], atol=1e-8)
    assert Scene.from_dict(scene.to_dict()).to_dict() == scene.to_dict()
    assert before == {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


def test_bad_third_view_rejection_is_retained_in_scene(reconstruction_inputs, tmp_path):
    m4, m5, _ = reconstruction_inputs
    path = m4 / "image_009_features.npz"
    with np.load(path) as data:
        points = data["points"]
    points[0] += [60, -40]
    np.savez_compressed(path, points=points)
    report = reconstruct_scene(m4, m5, tmp_path / "bad_view")
    assert report["summary"]["point_count"] == 26
    assert report["summary"]["rejected_tracks"] == {"high_reprojection_error": 1}
    scene = load_scene(tmp_path / "bad_view" / "scene.json")
    rejected = [track for track in scene.tracks if track.rejection_reason]
    assert len(rejected[0].observations) == 3 and rejected[0].point_id is None


def test_cross_image_track_conflict_is_exported(reconstruction_inputs, tmp_path):
    m4, m5, _ = reconstruction_inputs
    for path in (m4 / "pair_4_9_matches.npz", m5 / "pair_4_9_pose.npz"):
        with np.load(path) as data:
            arrays = dict(data)
        arrays["indices"][1, 1] = 0
        np.savez_compressed(path, **arrays)
    report = reconstruct_scene(m4, m5, tmp_path / "conflict")
    assert report["summary"]["conflict_count"] == 1
    assert report["conflicts"][0]["reason"] == "conflicting_image_features"
    assert report["summary"]["point_count"] == 27


def test_no_accepted_matches_writes_empty_current_scene(reconstruction_inputs, tmp_path):
    m4, m5, _ = reconstruction_inputs
    poses = json.loads((m5 / "poses.json").read_text())
    for pair in poses["pairs"]:
        path = m5 / pair["arrays_file"]
        with np.load(path) as data:
            arrays = dict(data)
        arrays["pose_inliers"][:] = False
        pair["pose_inlier_count"] = 0
        np.savez_compressed(path, **arrays)
    (m5 / "poses.json").write_text(json.dumps(poses))
    output = tmp_path / "empty"
    output.mkdir()
    (output / "scene.json").write_text("old scene")
    result = run_reconstruction(m4, m5, output)
    assert result.returncode == 1, result.stderr
    scene = load_scene(output / "scene.json")
    assert len(scene.cameras) == 3 and not scene.points and not scene.tracks


@pytest.mark.parametrize(
    "change", ["session", "calibration", "camera_M", "incomplete", "path", "indices", "mask"]
)
def test_incompatible_m4_m5_inputs_are_rejected(reconstruction_inputs, tmp_path, change):
    m4, m5, _ = reconstruction_inputs
    path = m5 / "poses.json"
    data = json.loads(path.read_text())
    if change == "session":
        data["session_id"] = "different"
    elif change == "calibration":
        data["images"][0]["working_intrinsics"]["camera_matrix"][0][0] += 1
    elif change == "camera_M":
        data["cameras"][0]["M"][0][0] += 1
    elif change == "incomplete":
        data["cameras"] = data["cameras"][:2]
    elif change == "path":
        data["pairs"][0]["arrays_file"] = "../escape.npz"
    else:
        arrays_path = m5 / data["pairs"][0]["arrays_file"]
        with np.load(arrays_path) as archive:
            arrays = dict(archive)
        if change == "indices":
            arrays["indices"][0, 0] = 2
        else:
            arrays["pose_inliers"][29] = True
        np.savez_compressed(arrays_path, **arrays)
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        reconstruct_scene(m4, m5, tmp_path / "bad")
    assert not (tmp_path / "bad").exists()


def test_warnings_are_carried_into_scene_and_command_output(reconstruction_inputs, tmp_path):
    m4, m5, _ = reconstruction_inputs
    path = m5 / "poses.json"
    poses = json.loads(path.read_text())
    poses.update(
        quality_status="rotation_loop_inconsistent",
        warnings=["Rotation loop disagrees by 17 degrees"],
    )
    path.write_text(json.dumps(poses))
    result = run_reconstruction(m4, m5, tmp_path / "warning")
    assert result.returncode == 0
    assert "17 degrees" in result.stdout
    assert (
        load_scene(tmp_path / "warning" / "scene.json").metadata["quality_status"]
        == "rotation_loop_inconsistent"
    )


@pytest.mark.parametrize(
    "options", [("--max-reprojection-error-px", "nan"), ("--min-angle-degrees", "0")]
)
def test_bad_options_have_actionable_cli_error(reconstruction_inputs, tmp_path, options):
    result = run_reconstruction(*reconstruction_inputs[:2], tmp_path / "options", *options)
    assert result.returncode == 2 and "Cannot reconstruct scene:" in result.stderr
    assert "Traceback" not in result.stderr


def test_source_and_raw_outputs_are_rejected_including_alias(reconstruction_inputs, tmp_path):
    m4, m5, _ = reconstruction_inputs
    alias = tmp_path / "alias"
    alias.symlink_to(m5, target_is_directory=True)
    for output in (m4 / "m6", m5 / "m6", alias / "m6", SCRIPT.parents[2] / "data/raw/m6_forbidden"):
        with pytest.raises(ValueError, match="outside data/raw"):
            reconstruct_scene(m4, m5, output)
        assert not output.exists()
