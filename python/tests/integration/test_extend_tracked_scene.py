import hashlib
import json
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest
from numpy.testing import assert_allclose

from distance_measuring.camera import CameraExtrinsics, CameraIntrinsics
from distance_measuring.scene import (
    Observation,
    Scene,
    SceneCamera,
    ScenePoint,
    SceneTrack,
    load_scene,
    save_scene,
)
from distance_measuring.scene.model import COORDINATE_FRAME

COMMAND = Path(__file__).resolve().parents[2] / "scripts/extend_tracked_scene.py"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare(capture, tmp_path, *, unsupported=False):
    rng = np.random.default_rng(91)
    camera = CameraIntrinsics(400, 400, 319.5, 239.5, 640, 480)
    first = np.array([(x, y) for y in range(80, 401, 80) for x in range(250, 551, 75)], float)
    depth = rng.uniform(8, 12, len(first))
    world = np.c_[camera.normalize_pixels(first), np.ones(len(first))] * depth[:, None]
    image_count = len([e for e in capture.events if e["type"] == "image"])
    poses = [CameraExtrinsics(np.eye(3), [i, 0, 0]) for i in range(image_count)]
    pixels = [camera.project(world, p) for p in poses]
    patches = [rng.integers(0, 255, (31, 31), np.uint8) for _ in world]
    image_events = [e for e in capture.events if e["type"] == "image"]
    for i, event in enumerate(image_events):
        image = np.full((480, 640), 70, np.uint8)
        if not (unsupported and i == 2):
            for pixel, patch in zip(pixels[i], patches, strict=True):
                x, y = np.rint(pixel).astype(int)
                image[y - 15 : y + 16, x - 15 : x + 16] = patch
        assert cv2.imwrite(str(capture.path / event["filename"]), image)
        event["metadata"].update(width=640, height=480, exif_orientation=1, rotation_degrees=0)
    capture.write()
    raw_hashes = {
        str(p.relative_to(capture.path)): sha(p) for p in capture.path.rglob("*") if p.is_file()
    }
    tracking, bootstrap = tmp_path / "tracking", tmp_path / "bootstrap"
    tracking.mkdir()
    bootstrap.mkdir()
    history = [
        {
            "image_id": i,
            "track_id": k,
            "pixel": pixel.tolist(),
            "status": "seeded" if i == 0 else "tracked",
        }
        for i in range(image_count)
        for k, pixel in enumerate(pixels[i])
        if not (unsupported and i == 2)
    ]
    (tracking / "observations.jsonl").write_text("".join(json.dumps(o) + "\n" for o in history))
    manifest = {
        "schema_version": 1,
        "coordinate_frame": COORDINATE_FRAME,
        "session_id": capture.metadata["session_id"],
        "selected_image_indices": list(range(image_count)),
        "input_sha256": raw_hashes,
        "observations_file": "observations.jsonl",
        "images": [
            {
                "index": i,
                "timestamp_ns": e["timestamp_ns"],
                "stored_intrinsics": camera.to_dict(),
                "working_intrinsics": camera.to_dict(),
                "stored_ideal_to_working_ideal_affine": np.eye(3).tolist(),
            }
            for i, e in enumerate(image_events)
        ],
    }
    (tracking / "tracking.json").write_text(json.dumps(manifest))
    tracks = tuple(
        SceneTrack(k, tuple(Observation(i, k, tuple(pixels[i][k]), 0) for i in range(2)), k)
        for k in range(12)
    )
    scene = Scene(
        tuple(SceneCamera(i, camera, poses[i]) for i in range(2)),
        tracks,
        tuple(ScenePoint(k, k, tuple(p), 5) for k, p in enumerate(world[:12])),
        {
            "session_id": capture.metadata["session_id"],
            "feature_id_source": "P2_temporal_track_id",
            "warnings": ["Synthetic geometry"],
            "scale_status": "arbitrary_visual_scale_unverified",
            "gauge": {"fixed_camera_id": 0, "first_baseline_length": 1},
        },
    )
    save_scene(bootstrap / "scene.json", scene)
    (bootstrap / "bootstrap.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "success": True,
                "session_id": capture.metadata["session_id"],
                "bootstrap_pair": [0, 1],
                "raw_input_sha256": raw_hashes,
            }
        )
    )
    return tracking, bootstrap


def run(capture, tracking, bootstrap, output, *options):
    return subprocess.run(
        [
            sys.executable,
            str(COMMAND),
            str(capture.path),
            str(tracking),
            str(bootstrap),
            "--output",
            str(output),
            "--search-radius-px",
            "8",
            *options,
        ],
        capture_output=True,
        text=True,
    )


def snapshot(*roots):
    return {p: sha(p) for root in roots for p in root.rglob("*") if p.is_file()}


def test_command_adds_a_camera_and_landmarks_without_changing_sources(capture, tmp_path):
    tracking, bootstrap = prepare(capture, tmp_path)
    before = snapshot(capture.path, tracking, bootstrap)
    output = tmp_path / "incremental"
    result = run(capture, tracking, bootstrap, output)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (
        "descriptor candidates/query=" in result.stdout
        and "patch candidates/landmark=" in result.stdout
    )
    report = json.loads((output / "incremental.json").read_text())
    assert report["success"] and report["summary"]["camera_count"] == 3
    assert report["summary"]["all_selected_cameras_registered"]
    assert report["summary"]["reprojection_rmse_px"] < 1
    assert report["summary"]["added_landmark_count"] >= 5
    assert not report["unregistered_image_indices"]
    scene = load_scene(output / "scene.json")
    assert_allclose(scene.cameras[-1].extrinsics.C_w, [2, 0, 0], atol=0.05)
    assert_allclose(scene.cameras[0].extrinsics.C_w, 0)
    assert np.linalg.norm(scene.cameras[1].extrinsics.C_w) == pytest.approx(1)
    assert scene.metadata["warnings"] == ["Synthetic geometry"]
    assert set(range(12)) <= {p.point_id for p in scene.points}
    for record in report["images"]:
        assert record["pnp"]["inlier_count"] >= 8
        assert record["local_search"]["candidate_fraction_of_exhaustive"] < 0.01
        assert cv2.imread(str(output / record["debug_image"])) is not None
        assert cv2.imread(str(output / record["growth"]["debug_image"])) is not None
    assert cv2.imread(str(output / report["preview_file"])) is not None
    assert before == snapshot(capture.path, tracking, bootstrap)
    assert run(capture, tracking, bootstrap, output).returncode == 2


def test_no_verified_support_retains_scene_and_reports_failure(capture, tmp_path):
    tracking, bootstrap = prepare(capture, tmp_path, unsupported=True)
    output = tmp_path / "out"
    result = run(capture, tracking, bootstrap, output)
    assert result.returncode == 1, result.stdout + result.stderr
    report = json.loads((output / "incremental.json").read_text())
    assert not report["success"] and report["summary"]["added_camera_count"] == 0
    assert not report["summary"]["all_selected_cameras_registered"]
    assert report["unregistered_image_indices"] == [2]
    assert report["images"][0]["pnp"]["reason"] == "insufficient_3d_2d_correspondences"
    assert len(load_scene(output / "scene.json").cameras) == 2


def test_failed_frame_does_not_block_a_later_supported_camera(capture_factory, tmp_path):
    capture = capture_factory(image_count=4)
    tracking, bootstrap = prepare(capture, tmp_path, unsupported=True)
    output = tmp_path / "out"
    result = run(capture, tracking, bootstrap, output, "--no-seed-growth")
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads((output / "incremental.json").read_text())
    assert [r["success"] for r in report["images"]] == [False, True]
    assert report["unregistered_image_indices"] == [2]
    scene = load_scene(output / "scene.json")
    assert [c.image_id for c in scene.cameras] == [0, 1, 3]
    assert_allclose(scene.cameras[-1].extrinsics.C_w, [3, 0, 0], atol=0.05)


def test_optional_inconsistent_gyro_is_rejected_and_seed_growth_can_be_disabled(capture, tmp_path):
    tracking, bootstrap = prepare(capture, tmp_path)
    parallax = tmp_path / "parallax"
    parallax.mkdir()
    # A valid SO(3) rotation with an intentionally wrong one-radian turn.
    from scipy.spatial.transform import Rotation

    (parallax / "parallax.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "coordinate_frame": COORDINATE_FRAME,
                "session_id": capture.metadata["session_id"],
                "tracking_manifest_sha256": sha(tracking / "tracking.json"),
                "pairs": [
                    {
                        "image_indices": [1, 2],
                        "available": True,
                        "R_second_first": Rotation.from_rotvec([1, 0, 0]).as_matrix().tolist(),
                    }
                ],
            }
        )
    )
    output = tmp_path / "out"
    result = run(
        capture,
        tracking,
        bootstrap,
        output,
        "--no-seed-growth",
        "--parallax-directory",
        str(parallax),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads((output / "incremental.json").read_text())
    assert report["seed_growth"] is None
    assert not report["images"][0]["prediction"]["gyro_used"]
    assert report["images"][0]["prediction"]["gyro_rejection_reason"]


@pytest.mark.parametrize("mode", ["changed_raw", "wrong_session", "bad_history", "unsafe_history"])
def test_input_mismatches_fail_without_a_traceback(capture, tmp_path, mode):
    tracking, bootstrap = prepare(capture, tmp_path)
    path = tracking / "tracking.json"
    manifest = json.loads(path.read_text())
    if mode == "changed_raw":
        (capture.path / "images/image_002.jpg").write_bytes(b"changed")
    elif mode == "wrong_session":
        manifest["session_id"] = "other"
    elif mode == "unsafe_history":
        manifest["observations_file"] = "../history.jsonl"
    else:
        history = tracking / "observations.jsonl"
        rows = [json.loads(line) for line in history.read_text().splitlines()]
        rows[0]["pixel"][0] += 10
        history.write_text("".join(json.dumps(r) + "\n" for r in rows))
    path.write_text(json.dumps(manifest))
    result = run(capture, tracking, bootstrap, tmp_path / "out")
    assert result.returncode == 2
    assert "Cannot extend tracked scene:" in result.stderr and "Traceback" not in result.stderr
    assert not (tmp_path / "out").exists()


def test_protected_paths_and_invalid_options_are_rejected(capture, tmp_path):
    tracking, bootstrap = prepare(capture, tmp_path)
    alias = tmp_path / "alias"
    alias.symlink_to(bootstrap, target_is_directory=True)
    for root in (capture.path, tracking, bootstrap, alias):
        output = root / "out"
        result = run(capture, tracking, bootstrap, output)
        assert result.returncode == 2 and not output.exists()
    output = tmp_path / "out"
    assert run(capture, tracking, bootstrap, output, "--corridor-px", "0").returncode == 2
    assert not output.exists()


def test_p6_command_refines_cameras_with_fixed_gauge_and_bounded_rounds(capture, tmp_path):
    tracking, bootstrap = prepare(capture, tmp_path)
    before = snapshot(capture.path, tracking, bootstrap)
    output = tmp_path / "refined"
    result = run(capture, tracking, bootstrap, output, "--refine")
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads((output / "incremental.json").read_text())
    assert report["refinement_success"] and report["registration_success"]
    assert report["summary"]["camera_count"] == 3
    assert report["bundle_adjustment_runtime_s"] > 0
    refinements = [report["seed_refinement"], report["images"][0]["refinement"]]
    for refinement in refinements:
        assert 1 <= refinement["round_count"] <= 2
        radius = 8
        for row in refinement["rounds"]:
            assert row["search_radius_px"] <= radius
            radius = row["search_radius_px"]
            ba = row["bundle_adjustment"]
            assert ba["after_rmse_px"] <= ba["before_rmse_px"] + 1e-9
            if row["observation_retry"]:
                assert cv2.imread(str(output / row["observation_retry"]["debug_image"])) is not None
                assert cv2.imread(str(output / row["growth"]["debug_image"])) is not None
    scene = load_scene(output / "scene.json")
    assert_allclose(scene.cameras[0].extrinsics.C_w, 0, atol=0)
    assert_allclose(scene.cameras[0].extrinsics.R_cw, np.eye(3), atol=0)
    assert np.linalg.norm(scene.cameras[1].extrinsics.C_w) == pytest.approx(1, abs=1e-12)
    assert_allclose(scene.cameras[-1].extrinsics.C_w, [2, 0, 0], atol=0.1)
    assert scene.metadata["landmark_confidence"]["diagnostics_source"] == "current_scene_geometry"
    assert before == snapshot(capture.path, tracking, bootstrap)


def test_p6_confidence_threshold_has_no_untrusted_fallback(capture, tmp_path):
    tracking, bootstrap = prepare(capture, tmp_path)
    output = tmp_path / "refined"
    result = run(
        capture,
        tracking,
        bootstrap,
        output,
        "--refine",
        "--no-seed-growth",
        "--confidence-min-score",
        "1",
    )
    assert result.returncode == 1, result.stdout + result.stderr
    report = json.loads((output / "incremental.json").read_text())
    assert not report["refinement_success"] and not report["registration_success"]
    assert report["seed_refinement"]["round_count"] == 1
    assert report["seed_refinement"]["rounds"][0]["confidence"]["excluded_point_count"] == 12
    assert report["images"][0]["pnp"]["input_count"] == 0
    assert report["images"][0]["confidence"]["excluded_point_count"] == 12


def test_p6_can_refine_the_seed_even_when_later_registration_lacks_support(capture, tmp_path):
    tracking, bootstrap = prepare(capture, tmp_path, unsupported=True)
    output = tmp_path / "refined"
    result = run(capture, tracking, bootstrap, output, "--refine")
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads((output / "incremental.json").read_text())
    assert report["refinement_success"] and not report["registration_success"]
    assert report["summary"]["camera_count"] == 2 and report["unregistered_image_indices"] == [2]
    assert report["failure_reason"] is None


@pytest.mark.parametrize(
    "option,value",
    [
        ("--refinement-max-rounds", "3"),
        ("--ba-window-size", "0"),
        ("--confidence-min-score", "1.1"),
    ],
)
def test_invalid_p6_configuration_is_rejected_before_output_creation(
    capture, tmp_path, option, value
):
    tracking, bootstrap = prepare(capture, tmp_path)
    output = tmp_path / "refined"
    result = run(capture, tracking, bootstrap, output, "--refine", option, value)
    assert result.returncode == 2
    assert "Traceback" not in result.stderr and not output.exists()
