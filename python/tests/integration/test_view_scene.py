import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import cv2
import pytest

from distance_measuring.scene import save_scene

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "view_scene.py"


def run_viewer(source, *options, env=None):
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(source), *options],
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )


def test_headless_listing_preview_and_selection_preserve_input_and_IDs(
    bundle_scene_factory, tmp_path
):
    scene, _, _ = bundle_scene_factory(count=12)
    source = save_scene(tmp_path / "m7" / "scene.json", scene)
    before = source.read_bytes()
    preview, selection = tmp_path / "m8" / "preview.png", tmp_path / "m8" / "selection.json"
    result = run_viewer(
        source,
        "--no-window",
        "--list-points",
        "--point-ids",
        "5",
        "3",
        "--preview",
        str(preview),
        "--selection-output",
        str(selection),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "point_id track_id views world_X world_Y world_Z" in result.stdout
    assert "A: point_id=5" in result.stdout and "B: point_id=3" in result.stdout
    for image_id, color in ((4, "red"), (7, "blue"), (9, "green")):
        assert f"Camera {image_id}: {color}" in result.stdout
    assert "Original synthetic fixture" in result.stdout
    image = cv2.imread(str(preview))
    assert image.shape == (1040, 1950, 3)
    assert image.std() > 10
    report = json.loads(selection.read_text())
    assert report["source_scene_sha256"] == hashlib.sha256(before).hexdigest()
    assert report["points"][0]["point_id"] == 5
    assert report["points"][0]["highlight_color"] == "gold"
    assert report["points"][1]["position_world"] == list(scene.points[0].position)
    assert source.read_bytes() == before
    assert "distance" not in report


@pytest.mark.parametrize(
    "options",
    [
        ("--point-ids", "0"),
        ("--point-ids", "3", "3"),
        ("--point-ids", "3", "5", "7"),
        ("--frustum-depth", "nan"),
    ],
)
def test_invalid_ID_or_depth_does_not_write_preview(bundle_scene_factory, tmp_path, options):
    scene, _, _ = bundle_scene_factory(count=12)
    source = save_scene(tmp_path / "input" / "scene.json", scene)
    output = tmp_path / "invalid.png"
    result = run_viewer(source, "--no-window", "--preview", str(output), *options)
    assert result.returncode == 2
    assert "Cannot view scene:" in result.stderr and "Traceback" not in result.stderr
    assert not output.exists()


def test_selection_output_requires_ids_and_paths_cannot_collide(bundle_scene_factory, tmp_path):
    scene, _, _ = bundle_scene_factory(count=12)
    source = save_scene(tmp_path / "input" / "scene.json", scene)
    output = tmp_path / "selection.json"
    result = run_viewer(source, "--no-window", "--selection-output", str(output))
    assert result.returncode == 2 and not output.exists()
    result = run_viewer(
        source,
        "--no-window",
        "--point-ids",
        "3",
        "--preview",
        str(output),
        "--selection-output",
        str(output),
    )
    assert result.returncode == 2 and "different output paths" in result.stderr
    assert not output.exists()


def test_raw_and_input_outputs_are_rejected_including_symlink(bundle_scene_factory, tmp_path):
    scene, _, _ = bundle_scene_factory(count=12)
    source = save_scene(tmp_path / "input" / "scene.json", scene)
    alias = tmp_path / "alias"
    alias.symlink_to(source.parent, target_is_directory=True)
    for output in (
        source.parent / "preview.png",
        alias / "preview.png",
        SCRIPT.parents[2] / "data/raw/m8_forbidden.png",
    ):
        result = run_viewer(source, "--no-window", "--preview", str(output))
        assert result.returncode == 2 and "outside data/raw" in result.stderr
        assert not output.exists()


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux X-display handling")
def test_default_window_mode_without_display_reports_fallback(bundle_scene_factory, tmp_path):
    scene, _, _ = bundle_scene_factory(count=12)
    source = save_scene(tmp_path / "input" / "scene.json", scene)
    env = os.environ.copy()
    env.pop("DISPLAY", None)
    result = run_viewer(source, env=env)
    assert result.returncode == 2
    assert "--no-window --preview" in result.stderr
