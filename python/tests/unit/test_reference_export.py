import json
import struct
import sys
from types import SimpleNamespace

import cv2
import numpy as np

from distance_measuring.scene import save_scene
from distance_measuring.visualization import reference_export


def test_reference_export_uses_bson_sidecars_correct_poses_and_preserves_old_viewer(
    tmp_path, bundle_scene_factory, monkeypatch
):
    scene, _, _ = bundle_scene_factory(count=5)
    reference, output = tmp_path / "reference", tmp_path / "web"
    (reference / "mvs/images").mkdir(parents=True)
    manifest = {"session_id": "synthetic-reference", "input_sha256": {}, "images": []}
    order = []
    for c in scene.cameras:
        name = f"image_{c.image_id}.png"
        cv2.imwrite(
            str(reference / "mvs/images" / name),
            np.full((*c.intrinsics.image_size[::-1], 3), 127, np.uint8),
        )
        manifest["images"].append(
            {
                "image_id": c.image_id,
                "name": name,
                "timestamp_ns": 2**60 + c.image_id,
                "exif_orientation": 6,
            }
        )
        order.append({"colmap_image_id": c.image_id, "name": name})
    (reference / "manifest.json").write_text(json.dumps(manifest))
    (reference / "mvs_image_order.json").write_text(json.dumps(order))
    (reference / "dense_report.json").write_text('{"minimum_distinct_support_views":3}')
    fields = [(k, "<f4") for k in ["x", "y", "z", "nx", "ny", "nz"]]
    fields += [(k, "u1") for k in ["red", "green", "blue"]]
    vertices = np.zeros(3, dtype=fields)
    for k, axis in enumerate(["x", "y", "z"]):
        vertices[axis] = [p.position[k] for p in scene.points[:3]]
    vertices["nz"] = 1
    header = "ply\nformat binary_little_endian 1.0\nelement vertex 3\n"
    header += "".join(f"property {'uchar' if t == 'u1' else 'float'} {k}\n" for k, t in fields)
    (reference / "fused.ply").write_bytes(header.encode() + b"end_header\n" + vertices.tobytes())
    (reference / "fused.ply.vis").write_bytes(
        struct.pack("<Q", 3) + struct.pack("<IIII", 3, 0, 1, 2) * 3
    )
    monkeypatch.setitem(sys.modules, "pycolmap", SimpleNamespace(Reconstruction=lambda _: None))
    monkeypatch.setattr(reference_export, "scene_from_colmap", lambda *_: scene)
    previous = save_scene(tmp_path / "old/scene.json", scene)
    output.mkdir()
    (output / "index.html").write_text("old viewer")
    report = reference_export.export_reference_scene(
        reference, output, target_points=100, previous_scene=previous
    )
    assert report["points"] == 8 and not report["target_reached"]
    assert (output / "previous_20k.html").read_text() == "old viewer"
    html = (output / "index.html").read_text()
    payload = json.loads(html.split('id="scene-data">')[1].split("</script>")[0])
    assert payload["sparse_point_count"] == 5 and payload["point_count"] == 8
    assert payload["dense_cloud"]["support_histogram"] == {"3": 3}
    assert len(list((output / "cloud_100").glob("*.bson"))) == 1
    assert (output / "dense_worker.js").exists()
    for camera, overlay in zip(scene.cameras, payload["previous_cameras"], strict=True):
        np.testing.assert_allclose(
            np.asarray(overlay["center"]) + payload["origin"], camera.extrinsics.C_w, atol=1e-12
        )
    first = payload["cameras"][0]
    assert first["photo"].startswith("data:image/jpeg;base64,")
    assert first["time_s"] == 0 and first["exif_orientation"] == 6
    assert (
        reference_export.export_reference_scene(reference, output, target_points=100)["points"] == 8
    )
