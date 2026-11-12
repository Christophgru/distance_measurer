import base64
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

from distance_measuring.camera import CameraExtrinsics, resolve_intrinsics
from distance_measuring.dataset import load_session
from distance_measuring.scene import Scene, SceneCamera, save_scene
from distance_measuring.scene.model import COORDINATE_FRAME
from distance_measuring.vision.pipeline import working_camera

SCRIPT = Path(__file__).resolve().parents[3] / "python/scripts/measure_scene.py"


@pytest.fixture
def inputs(capture, tmp_path):
    session = load_session(capture.path)
    cameras = tuple(
        SceneCamera(
            e.index,
            working_camera(resolve_intrinsics(e.metadata), (40, 24))[0],
            CameraExtrinsics(np.eye(3), [e.index - 1, 0, 0]),
        )
        for e in session.images
    )
    scene = Scene(
        cameras,
        (),
        (),
        {"session_id": session.metadata.session_id, "quality_status": "synthetic_unverified"},
    )
    source = tmp_path / "scene/scene.json"
    save_scene(source, scene)
    positions = {"a": [0, 0, 6], "b": [1, 0, 6], "c": [0.2, 0.3, 6], "d": [0.7, 0.3, 6]}
    data = {
        "schema_version": 1,
        "session_id": session.metadata.session_id,
        "scene_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "coordinate_frame": COORDINATE_FRAME,
        "image_sizes": {str(c.image_id): list(c.intrinsics.image_size) for c in cameras},
        "points": {
            name: {
                "observations": {
                    str(c.image_id): {
                        "pixel": c.intrinsics.project(p, c.extrinsics).tolist(),
                        "reviewed": True,
                    }
                    for c in cameras
                }
            }
            for name, p in positions.items()
        },
        "reference": {"endpoints": ["a", "b"], "length_m": 0.1},
        "measurements": [{"id": "held", "endpoints": ["c", "d"], "expected_length_m": 0.05}],
        "view_groups": [[0, 1], [1, 2]],
        "localization_radius_px": 0.01,
    }
    annotation = tmp_path / "annotations.json"
    annotation.write_text(json.dumps(data))
    return source, annotation, capture.path


def run(source, annotation, *args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(source), str(annotation), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def test_cli_full_resolution_review_has_correct_EXIF_resize_and_input_hashes(inputs, tmp_path):
    source, annotation, raw = inputs
    paths = [source, annotation, *(p for p in raw.rglob("*") if p.is_file())]
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    output, html = tmp_path / "out/result.json", tmp_path / "out/index.html"
    result = run(
        source,
        annotation,
        "--output",
        str(output),
        "--review-html",
        str(html),
        "--session",
        str(raw),
        "--sensitivity-trials",
        "20",
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(output.read_text())
    assert not report["metric_measurement_accepted"]
    assert report["blocking_reasons"] == ["visual_cameras_unverified"]
    assert report["input_sha256"][str(source)] == before[source]
    assert report["input_sha256"][str(annotation)] == before[annotation]
    assert all(hashlib.sha256(p.read_bytes()).hexdigest() == h for p, h in before.items())
    payload = json.loads(
        html.read_text().split('id="measurement-data">', 1)[1].split("</script>", 1)[0]
    )
    assert len(payload["photos"]) == 3
    for p in payload["photos"]:
        assert (p["width"], p["height"]) == (48, 80)
        image = cv2.imdecode(
            np.frombuffer(base64.b64decode(p["photo"].split(",")[1]), np.uint8), cv2.IMREAD_COLOR
        )
        assert image.shape[:2] == (80, 48)
        working = np.array([12.3, 5.9, 1])
        display = np.array(p["working_to_display"]) @ working
        # resize 0.5, EXIF-6, then integer pixel center -> canvas center.
        np.testing.assert_allclose(display, [35.2, 25.6, 1])
        np.testing.assert_allclose(np.array(p["display_to_working"]) @ display, working)


@pytest.mark.parametrize("target", ["source", "annotation", "raw"])
def test_cli_cannot_overwrite_inputs_or_raw(inputs, tmp_path, target):
    source, annotation, raw = inputs
    output = {"source": source, "annotation": annotation, "raw": raw / "result.json"}[target]
    result = run(
        source,
        annotation,
        "--output",
        str(output),
        "--sensitivity-trials",
        "20",
        "--session",
        str(raw),
    )
    assert result.returncode == 2
    assert "Outputs must be outside" in result.stderr
    if target == "raw":
        assert not output.exists()


def test_cli_rejects_annotation_from_changed_scene(inputs, tmp_path):
    source, annotation, _ = inputs
    source.write_text(source.read_text() + "\n")
    output = tmp_path / "out/result.json"
    result = run(source, annotation, "--output", str(output), "--sensitivity-trials", "20")
    assert result.returncode == 2
    assert "SHA-256" in result.stderr
    assert not output.exists()
