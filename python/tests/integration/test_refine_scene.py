import hashlib
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from numpy.testing import assert_allclose

from distance_measuring.optimization import bundle as bundle_module
from distance_measuring.optimization import bundle_adjust
from distance_measuring.optimization.pipeline import refine_scene
from distance_measuring.scene import load_scene, save_scene

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "refine_scene.py"


def write_input(tmp_path, factory):
    _, scene, _ = factory(count=18)
    return save_scene(tmp_path / "m6" / "scene.json", scene), scene


def run_refinement(source, output, *options):
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(source), "--output", str(output), *options],
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize("suffix", [".json", ".bson"])
def test_cli_exports_refined_scene_metrics_and_input_hash(bundle_scene_factory, tmp_path, suffix):
    source, initial = write_input(tmp_path, bundle_scene_factory)
    if suffix == ".bson":
        source = save_scene(source.with_suffix(suffix), initial)
    before = source.read_bytes()
    output = tmp_path / "m7"
    result = run_refinement(source, output)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Reprojection RMSE:" in result.stdout and "Robust cost:" in result.stdout
    report = json.loads((output / "bundle_adjustment.json").read_text())
    scene = load_scene(output / "scene.json")
    assert source.read_bytes() == before
    assert report["input_scene_sha256"] == hashlib.sha256(before).hexdigest()
    assert scene.metadata["bundle_adjustment"] == report
    assert scene.metadata["timestamp_ns"] == 2**60 + 3
    assert report["accepted"] and report["converged"]
    assert report["after"]["reprojection_rmse_px"] < report["before"]["reprojection_rmse_px"]
    assert scene.cameras[0].to_dict() == initial.cameras[0].to_dict()
    assert [point.point_id for point in scene.points] == [
        point.point_id for point in initial.points
    ]
    assert scene.tracks[-1] == initial.tracks[-1]
    assert_allclose(scene.cameras[1].intrinsics.K, initial.cameras[1].intrinsics.K, atol=0)


def test_cli_reports_iteration_limit_and_saves_useful_candidate(bundle_scene_factory, tmp_path):
    source, _ = write_input(tmp_path, bundle_scene_factory)
    result = run_refinement(source, tmp_path / "limited", "--max-nfev", "1")
    assert result.returncode == 1
    report = json.loads((tmp_path / "limited" / "bundle_adjustment.json").read_text())
    assert report["accepted"] and not report["converged"]
    assert load_scene(tmp_path / "limited" / "scene.json").points


def test_invalid_depth_candidate_is_rejected_without_altering_input_geometry(
    bundle_scene_factory, monkeypatch
):
    _, initial, _ = bundle_scene_factory(count=12)

    def invalid_solver(fun, x0, **kwargs):
        candidate = x0.copy()
        points = candidate[fun.__self__.point_start :].reshape(-1, 3)
        points[:, 2] = -5
        return SimpleNamespace(
            x=candidate,
            success=True,
            status=1,
            message="Injected invalid candidate",
            nfev=1,
            optimality=0,
        )

    monkeypatch.setattr(bundle_module, "least_squares", invalid_solver)
    result = bundle_adjust(initial)
    assert not result.report["accepted"]
    assert result.report["after"] == result.report["before"]
    assert [camera.to_dict() for camera in result.scene.cameras] == [
        camera.to_dict() for camera in initial.cameras
    ]
    assert result.scene.points == initial.points
    assert result.scene.tracks == initial.tracks
    assert result.scene.metadata["scale_status"] == initial.metadata["scale_status"]


@pytest.mark.parametrize("options", [("--f-scale", "nan"), ("--f-scale", "0"), ("--max-nfev", "0")])
def test_invalid_cli_options(bundle_scene_factory, tmp_path, options):
    source, _ = write_input(tmp_path, bundle_scene_factory)
    output = tmp_path / "bad_options"
    result = run_refinement(source, output, *options)
    assert result.returncode == 2
    assert "Cannot refine scene:" in result.stderr and "Traceback" not in result.stderr
    assert not output.exists()


def test_empty_scene_input_fails_before_writing_output(bundle_scene_factory, tmp_path):
    source, scene = write_input(tmp_path, bundle_scene_factory)
    save_scene(source, replace(scene, points=(), tracks=()))
    result = run_refinement(source, tmp_path / "empty")
    assert result.returncode == 2
    assert "accepted 3D points" in result.stderr
    assert not (tmp_path / "empty").exists()


def test_input_and_raw_output_aliases_are_rejected(bundle_scene_factory, tmp_path):
    source, _ = write_input(tmp_path, bundle_scene_factory)
    alias = tmp_path / "alias"
    alias.symlink_to(source.parent, target_is_directory=True)
    for output in (source.parent / "m7", alias / "m7", SCRIPT.parents[2] / "data/raw/m7_forbidden"):
        with pytest.raises(ValueError, match="outside data/raw"):
            refine_scene(source, output)
        assert not output.exists()
