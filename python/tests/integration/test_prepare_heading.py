import hashlib
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from distance_measuring.scene import save_scene

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/prepare_heading.py"


@pytest.mark.parametrize("isolated", [False, True])
def test_heading_cli_preserves_sources_and_aborts_on_isolated_image(
    capture, bundle_scene_factory, tmp_path, isolated
):
    scene, _, _ = bundle_scene_factory()
    mapping = {4: 0, 7: 1, 9: 2}
    tracks = tuple(
        replace(
            t,
            observations=tuple(
                replace(o, image_id=mapping[o.image_id])
                for o in t.observations
                if not isolated or o.image_id != 9
            ),
        )
        for t in scene.tracks
    )
    scene = replace(
        scene,
        cameras=tuple(replace(c, image_id=mapping[c.image_id]) for c in scene.cameras),
        tracks=tracks,
        metadata={**scene.metadata, "session_id": capture.metadata["session_id"]},
    )
    source = tmp_path / "source/scene.json"
    save_scene(source, scene)
    for e in capture.events:
        if e["type"] == "image":
            e["metadata"].update(lens_facing=1, target_rotation=0, sensor_orientation_degrees=90)
    capture.write()
    inputs = [source, *[p for p in capture.path.rglob("*") if p.is_file()]]
    hashes = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs}
    output = tmp_path / "heading"
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            str(capture.path),
            "--scene",
            str(source),
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if isolated:
        assert result.returncode != 0
        assert "Disconnected image overlap" in result.stderr and "[2]" in result.stderr
        assert not output.exists()
    else:
        assert result.returncode == 0, result.stderr
        report = json.loads((output / "heading.json").read_text())
        assert report["relative_image_orientation_source"] == "visual_reconstruction"
        assert not report["compass"]["used"] and not report["metric_scale_accepted"]
        assert (output / "heading.png").exists() and (output / "orientation.npz").exists()
    assert hashes == {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs}
