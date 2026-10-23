import base64
import copy
import json
from dataclasses import replace

import numpy as np
import pytest
from numpy.testing import assert_allclose

from distance_measuring.scene import save_scene
from distance_measuring.visualization.scene_view import camera_drawing
from distance_measuring.visualization.web_export import browser_payload, export_browser_scene


def unpack(payload, key, dtype):
    return np.frombuffer(base64.b64decode(payload[key]), dtype=dtype)


def test_gpu_buffers_preserve_world_geometry_ids_and_every_observation(bundle_scene_factory):
    scene, _, _ = bundle_scene_factory()
    before = copy.deepcopy(scene.to_dict())
    payload = browser_payload(scene, "test_hash")
    positions = unpack(payload, "positions", "<f4").reshape(-1, 3) + payload["origin"]
    assert_allclose(positions, [p.position for p in scene.points], atol=1e-6)
    assert unpack(payload, "point_ids", "<u4").tolist() == [p.point_id for p in scene.points]
    assert unpack(payload, "track_ids", "<u4").tolist() == [p.track_id for p in scene.points]
    assert len(unpack(payload, "colors", "u1")) == len(scene.points) * 3
    offsets = unpack(payload, "observation_offsets", "<u4")
    images = unpack(payload, "observation_images", "<u4")
    pixels = unpack(payload, "observation_pixels", "<f4").reshape(-1, 2)
    tracks = {t.track_id: t for t in scene.tracks}
    for row, point in enumerate(scene.points):
        obs = tracks[point.track_id].observations
        assert images[offsets[row] : offsets[row + 1]].tolist() == [o.image_id for o in obs]
        assert_allclose(pixels[offsets[row] : offsets[row + 1]], [o.pixel for o in obs], atol=1e-4)
    assert scene.to_dict() == before
    assert payload["geometry_modified"] is False and payload["metric_scale_applied"] is False


def test_browser_frustums_use_saved_lecture_camera_geometry(bundle_scene_factory):
    scene, _, _ = bundle_scene_factory()
    payload = browser_payload(scene, "hash")
    for row, camera in enumerate(scene.cameras):
        entry = payload["cameras"][row]
        expected = camera_drawing(camera, payload["frustum_depth"], row)
        assert_allclose(np.array(entry["vertices"]) + entry["world_center"], expected.vertices)
        assert_allclose(np.array(entry["center"]) + payload["origin"], camera.extrinsics.C_w)
        assert_allclose(entry["R_cw"], camera.extrinsics.R_cw)


def test_large_world_translation_preserves_local_float32_detail(bundle_scene_factory):
    scene, _, _ = bundle_scene_factory()
    shift = np.array([1e9, -1e9, 1e9])
    moved = replace(
        scene,
        cameras=tuple(
            replace(c, extrinsics=replace(c.extrinsics, C_w=c.extrinsics.C_w + shift))
            for c in scene.cameras
        ),
        points=tuple(replace(p, position=np.array(p.position) + shift) for p in scene.points),
    )
    payload = browser_payload(moved, "hash")
    positions = unpack(payload, "positions", "<f4").reshape(-1, 3) + payload["origin"]
    assert_allclose(positions - shift, [p.position for p in scene.points], rtol=0, atol=5e-7)


def test_offline_html_escapes_metadata_and_preserves_source(tmp_path, bundle_scene_factory):
    scene, _, _ = bundle_scene_factory()
    scene = replace(
        scene, metadata={**scene.metadata, "session_id": "</script><script>alert('bad')</script>"}
    )
    source = tmp_path / "input/scene.json"
    save_scene(source, scene)
    before = source.read_bytes()
    output = tmp_path / "viewer/index.html"
    report = export_browser_scene(source, output)
    html = output.read_text()
    assert "__SCENE_DATA__" not in html and "__VIEWER_SCRIPT__" not in html
    assert "</script><script>alert" not in html
    assert "<script src=" not in html
    assert "Permission is hereby granted" in html and "WebGLRenderer" in html
    encoded = html.split('id="scene-data">', 1)[1].split("</script>", 1)[0]
    assert json.loads(encoded)["session_id"] == scene.metadata["session_id"]
    assert source.read_bytes() == before
    assert report["points"] == len(scene.points)
    with pytest.raises(ValueError, match="outside"):
        export_browser_scene(source, source.parent / "viewer.html")


def test_point_ids_are_not_silently_truncated(bundle_scene_factory):
    scene, _, _ = bundle_scene_factory()
    payload = browser_payload(scene, "hash")
    assert unpack(payload, "point_ids", "<u4")[0] == scene.points[0].point_id
    point = replace(scene.points[0], point_id=2**32 + 1)
    track = replace(scene.tracks[0], point_id=point.point_id)
    invalid = replace(scene, points=(point, *scene.points[1:]), tracks=(track, *scene.tracks[1:]))
    with pytest.raises(ValueError, match="never truncated"):
        browser_payload(invalid, "hash")
