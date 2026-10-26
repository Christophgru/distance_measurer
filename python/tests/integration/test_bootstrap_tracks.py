import hashlib
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import cv2
import numpy as np
import pytest
from numpy.testing import assert_allclose
from scipy.spatial.transform import Rotation

from distance_measuring.camera import CameraExtrinsics, CameraIntrinsics
from distance_measuring.scene import load_scene
from distance_measuring.scene.model import COORDINATE_FRAME
from distance_measuring.vision import bootstrap_pipeline
from distance_measuring.vision.bootstrap import BootstrapConfig

COMMAND = Path(__file__).resolve().parents[2] / "scripts/bootstrap_tracks.py"


def prepare(tmp_path, *, concentrated=False, rotation_only=False):
    root = tmp_path / "parallax"
    root.mkdir()
    camera = CameraIntrinsics(820, 790, 639.5, 479.5, 1280, 960)
    world = np.random.default_rng(47).uniform([-2, -1.5, 5], [2, 1.5, 10], (64, 3))
    ids = [4, 7, 9]
    pixels = []
    for step in range(3):
        pose = CameraExtrinsics(
            Rotation.from_rotvec(np.array([0.02, -0.03, 0.01]) * step).as_matrix(),
            np.array([0 if rotation_only else 0.8 * step, 0, 0]),
        )
        pixels.append(camera.project(world, pose))
    tracks = np.arange(1000, 1064)
    pairs = []
    for index, (i, j) in enumerate(zip(ids[:-1], ids[1:], strict=True)):
        first = pixels[index].copy()
        if concentrated and index == 0:
            first[:] = [20.0, 20.0]
        second = pixels[index + 1]
        filename = f"pair_{i}_{j}.npz"
        np.savez_compressed(
            root / filename,
            track_ids=tracks,
            source_pixels=first,
            target_pixels=second,
            parallax_px=np.full(64, 50.0),
            selected_indices=np.arange(64),
        )
        pairs.append(
            {
                "image_indices": [i, j],
                "available": True,
                "arrays_file": filename,
                "selected_count": 64,
                "selected_tracks": [
                    {
                        "track_id": int(track),
                        "source_pixel": a.tolist(),
                        "target_pixel": b.tolist(),
                        "parallax_px": 50.0,
                    }
                    for track, a, b in zip(tracks, first, second, strict=True)
                ],
            }
        )
    manifest = {
        "schema_version": 1,
        "session_id": "synthetic",
        "coordinate_frame": COORDINATE_FRAME,
        "selected_image_indices": ids,
        "quality_status": "synthetic_unverified",
        "warnings": ["Test provenance warning"],
        "images": [
            {"index": i, "timestamp_ns": i * 100000000, "working_intrinsics": camera.to_dict()}
            for i in ids
        ],
        "pairs": pairs,
    }
    (root / "parallax.json").write_text(json.dumps(manifest))
    return root


def run(source, output, *options):
    return subprocess.run(
        [sys.executable, str(COMMAND), str(source), "--output", str(output), *options],
        capture_output=True,
        text=True,
    )


def hashes(source):
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in source.iterdir()}


def test_command_skips_bad_pair_and_saves_loadable_scene_with_original_ids(tmp_path):
    source = prepare(tmp_path, concentrated=True)
    before = hashes(source)
    output = tmp_path / "bootstrap"
    result = run(source, output)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Bootstrap thresholds:" in result.stdout and "R_second_first=" in result.stdout
    report = json.loads((output / "bootstrap.json").read_text())
    assert report["bootstrap_pair"] == [7, 9]
    assert "insufficient_coverage" in report["candidates"][0]["reasons"][0]
    candidate = report["candidates"][1]
    assert candidate["accepted"] and candidate["pose"]["input_track_count"] == 64
    assert candidate["pose"]["ransac_inlier_count"] >= 60
    scene = load_scene(output / "scene.json")
    assert len(scene.points) >= 60 and [c.image_id for c in scene.cameras] == [7, 9]
    assert {p.point_id for p in scene.points} <= set(range(1000, 1064))
    assert scene.metadata["warnings"] == ["Test provenance warning"]
    assert scene.metadata["scale_status"] == "arbitrary_visual_scale_unverified"
    assert_allclose(scene.cameras[0].extrinsics.C_w, 0)
    assert np.linalg.norm(scene.cameras[1].extrinsics.C_w) == pytest.approx(1)
    for point in candidate["landmarks"]["landmarks"]:
        if point["accepted"]:
            assert min(point["depths_in_baseline_units"]) > 0
            assert max(point["reprojection_errors_px"]) <= 3
            assert point["triangulation_angle_degrees"] >= 1
    with np.load(output / candidate["arrays_file"], allow_pickle=False) as arrays:
        assert arrays["track_ids"].tolist() == list(range(1000, 1064))
        assert arrays["essential_inliers"].dtype == bool
        assert arrays["pose_inliers"].shape == (64,)
    assert cv2.imread(str(output / "bootstrap_preview.png")) is not None
    assert report["input_files_sha256"] == before
    assert hashes(source) == before
    assert run(source, output).returncode == 2


def test_earliest_success_stops_before_later_pairs(tmp_path):
    source = prepare(tmp_path)
    report = bootstrap_pipeline.bootstrap_session(source, tmp_path / "out")
    assert report["bootstrap_pair"] == [4, 7]
    assert len(report["candidates"]) == report["remaining_pair_count"] == 1
    assert report["unregistered_image_indices"] == [9]


@pytest.mark.parametrize("rotation_only", [True, False])
def test_geometric_failures_report_reasons_without_emitting_scene(tmp_path, rotation_only):
    source = prepare(tmp_path, rotation_only=rotation_only)
    output = tmp_path / "out"
    options = [] if rotation_only else ["--min-triangulation-angle-degrees", "89"]
    result = run(source, output, *options)
    assert result.returncode == 1, result.stdout + result.stderr
    report = json.loads((output / "bootstrap.json").read_text())
    assert not report["success"] and report["bootstrap_pair"] is None
    assert len(report["candidates"]) == 2 and report["failure_reason"]
    reason = "rotation_only_motion" if rotation_only else "insufficient_landmarks"
    assert all(reason in c["reasons"][0] for c in report["candidates"])
    assert not (output / "scene.json").exists()
    assert not (output / "bootstrap_preview.png").exists()


def test_pose_failure_continues_to_next_pair(tmp_path, monkeypatch):
    source = prepare(tmp_path)
    original = bootstrap_pipeline.estimate_bootstrap_pose

    def reject_first(pair, config):
        pose = original(pair, config)
        if pair.image_ids == (4, 7):
            return replace(
                pose, report={**pose.report, "success": False, "reasons": ["test failure"]}
            )
        return pose

    monkeypatch.setattr(bootstrap_pipeline, "estimate_bootstrap_pose", reject_first)
    report = bootstrap_pipeline.bootstrap_session(source, tmp_path / "out")
    assert report["bootstrap_pair"] == [7, 9]
    assert report["candidates"][0]["stage"] == "pose"


@pytest.mark.parametrize("mode", ["mismatch", "unsafe_path", "missing_intrinsics", "bad_time"])
def test_bad_saved_inputs_fail_with_actionable_errors(tmp_path, mode):
    source = prepare(tmp_path)
    path = source / "parallax.json"
    manifest = json.loads(path.read_text())
    if mode == "mismatch":
        manifest["pairs"][0]["selected_tracks"][0]["track_id"] = 9999
    elif mode == "unsafe_path":
        manifest["pairs"][0]["arrays_file"] = "../outside.npz"
    elif mode == "missing_intrinsics":
        del manifest["images"]
    else:
        manifest["images"][1]["timestamp_ns"] = 0
    path.write_text(json.dumps(manifest))
    result = run(source, tmp_path / "out")
    assert result.returncode == 2
    assert "Cannot bootstrap tracks:" in result.stderr and "Traceback" not in result.stderr
    assert not (tmp_path / "out/scene.json").exists()


def test_output_protection_and_invalid_configuration(tmp_path):
    source = prepare(tmp_path)
    alias = tmp_path / "source_alias"
    alias.symlink_to(source, target_is_directory=True)
    for output in (source / "output", alias / "output"):
        with pytest.raises(ValueError, match="outside"):
            bootstrap_pipeline.bootstrap_session(source, output)
        assert not output.exists()
    result = run(source, tmp_path / "out", "--min-tracks", "2")
    assert result.returncode == 2 and not (tmp_path / "out").exists()
    with pytest.raises(ValueError):
        BootstrapConfig(min_triangulation_angle_degrees=90)
