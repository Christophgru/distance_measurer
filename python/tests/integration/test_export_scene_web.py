import base64
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from distance_measuring.camera import CameraExtrinsics, resolve_intrinsics
from distance_measuring.dataset import load_session
from distance_measuring.scene import (
    Observation,
    Scene,
    SceneCamera,
    ScenePoint,
    SceneTrack,
    save_scene,
)

SCRIPT = Path(__file__).resolve().parents[3] / "python/scripts/export_scene_web.py"


@pytest.mark.parametrize("suffix", [".json", ".bson"])
def test_cli_exports_matching_photos_colors_and_source_hashes_without_changing_inputs(
    capture, tmp_path, suffix
):
    session = load_session(capture.path)
    cameras = tuple(
        SceneCamera(
            e.index,
            resolve_intrinsics(e.metadata),
            CameraExtrinsics(
                np.eye(3),
                [e.index, 0, 0],
            ),
        )
        for e in session.images
    )
    position = [0, 0, 8]
    track = SceneTrack(
        13,
        tuple(
            Observation(
                c.image_id,
                20 + c.image_id,
                tuple(c.intrinsics.project(position, c.extrinsics)),
                0.2,
            )
            for c in cameras
        ),
        37,
    )
    scene = Scene(
        cameras,
        (track,),
        (ScenePoint(37, 13, position, 10),),
        {
            "session_id": session.metadata.session_id,
            "quality_status": "synthetic_unverified",
            "scale_status": "arbitrary_visual_scale_unverified",
        },
    )
    source = tmp_path / "scene" / ("scene" + suffix)
    save_scene(source, scene)
    inputs = [source, *[p for p in capture.path.rglob("*") if p.is_file()]]
    hashes = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs}
    output = tmp_path / "web/index.html"
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            str(source),
            "--session",
            str(capture.path),
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    data = json.loads(output.read_text().split('id="scene-data">', 1)[1].split("</script>", 1)[0])
    assert data["has_photo_colors"] and data["point_count"] == 1 and data["camera_count"] == 3
    assert all(
        c["photo"].startswith("data:image/jpeg;base64,") and c["exif_orientation"] == 6
        for c in data["cameras"]
    )
    colors = np.frombuffer(base64.b64decode(data["colors"]), dtype=np.uint8)
    assert np.all(np.abs(colors.astype(int) - 127) <= 2)
    assert data["source_scene_sha256"] == hashes[source]
    assert hashes == {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs}
    bad = subprocess.run(
        [sys.executable, str(SCRIPT), str(source), "--output", str(source.parent / "bad.html")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert bad.returncode == 2 and not (source.parent / "bad.html").exists()
