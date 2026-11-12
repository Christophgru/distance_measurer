import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from distance_measuring.scene import load_scene
from distance_measuring.vision.hybrid_tracking import track_hybrid_session
from distance_measuring.vision.tracking import TrackingConfig

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/reconstruct_incremental.py"
CONFIG = TrackingConfig(max_points=350, min_distance_px=6)


def replay(data, output):
    return track_hybrid_session(
        data.path, output, calibration_path=data.calibration, max_image_size=640, config=CONFIG
    )


def run(data, output, *options):
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            str(data.path),
            "--output",
            str(output),
            "--calibration",
            str(data.calibration),
            "--max-image-size",
            "640",
            *map(str, options),
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def test_replay_exports_continuous_preview_ids_and_actual_jpeg_observations(
    hybrid_scene_factory, tmp_path
):
    data = hybrid_scene_factory()
    paths = [p for p in data.path.rglob("*") if p.is_file()]
    hashes = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    output = tmp_path / "tracks"
    report = replay(data, output)
    assert report["success"]
    assert len(report["hybrid"]["preview_frames"]) == 10
    assert report["hybrid"]["eligible_image_indices"] == [0, 1, 2]
    assert report["hybrid"]["preview_segment_count"] == 1
    assert report["summary"]["tracks_spanning_sequence"] > 20
    assert all(p["accepted_count"] > 20 for p in report["pairs"])
    assert "tracking.json" in report["input_sha256"]
    assert "tracking/frames/frame_000000.png" in report["input_sha256"]
    assert hashes == {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    observations = [json.loads(x) for x in (output / "observations.jsonl").read_text().splitlines()]
    assert {r["image_id"] for r in observations} == {0, 1, 2}
    assert report["hybrid"]["reserved_track_id"] >= max(r["track_id"] for r in observations)
    with pytest.raises(FileExistsError):
        replay(data, output)


@pytest.mark.parametrize("missing,expect_segments", [((4,), 1), ((3, 4, 5), 2)])
def test_missing_frames_are_not_silently_connected_across_large_gaps(
    hybrid_scene_factory, tmp_path, missing, expect_segments
):
    data = hybrid_scene_factory(missing_frames=missing)
    report = replay(data, tmp_path / "tracks")
    assert report["hybrid"]["preview_segment_count"] == expect_segments
    if expect_segments > 1:
        assert any(r["status"] == "unsupported" for r in report["hybrid"]["still_associations"])
        assert (
            report["pairs"][0]["accepted_count"] == 0 or report["pairs"][1]["accepted_count"] == 0
        )


def test_geometry_change_splits_namespaces_and_preserves_unsupported_segment_diagnostics(
    hybrid_scene_factory, tmp_path
):
    data = hybrid_scene_factory(geometry_change=True, image_count=5)
    report = replay(data, tmp_path / "tracks")
    assert report["hybrid"]["preview_segment_count"] == 2
    supported = report["hybrid"]["image_segments"]
    assert supported["0"] == 0 and supported["4"] == 1
    assert report["pairs"][1]["accepted_count"] == 0 or report["pairs"][2]["accepted_count"] == 0


@pytest.mark.parametrize("mode", ["translation", "rotation", "unknown_clock"])
def test_one_command_hybrid_reconstruction_has_honest_success_and_failure_status(
    hybrid_scene_factory,
    tmp_path,
    mode,
):
    data = hybrid_scene_factory(
        mode="rotation" if mode == "rotation" else "translation",
        unknown_clock=mode == "unknown_clock",
    )
    output = tmp_path / "reconstruction"
    result = run(data, output)
    assert result.returncode == (0 if mode == "translation" else 1), result.stdout + result.stderr
    report = json.loads((output / "reconstruction.json").read_text())
    assert report["settings"]["tracking_source"] == "hybrid"
    assert len(report["hybrid"]["preview_frames"]) == 10
    if mode == "translation":
        scene = load_scene(output / "scene.json")
        assert len(scene.cameras) == 3
        assert len(scene.points) >= 8
        assert report["diagnostics"]["quality"]["reprojection_rmse_px"] < 2
    else:
        assert report["status"] == "failed" and not (output / "scene.json").exists()


def test_hybrid_mode_requires_a_sidecar_but_still_only_mode_remains_explicit(capture, tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            str(capture.path),
            "--output",
            str(tmp_path / "bad"),
            "--tracking-source",
            "hybrid",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2 and "saved preview frames" in result.stderr
    assert not (tmp_path / "bad").exists()
