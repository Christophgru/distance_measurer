"""Export the image-only reference with bounded BSON clouds and GPU inspection.

Lecture 002 pp. 65–76/81–83: previews use the exact undistorted K/R/C frame.
Dense support projections are identified separately from measured feature tracks.
"""

import base64
import hashlib
import json
import shutil
from pathlib import Path

import cv2
import numpy as np

from distance_measuring.camera import CameraExtrinsics
from distance_measuring.scene import Scene, SceneCamera, load_scene, save_scene
from distance_measuring.scene.model import intrinsics_from_dict
from distance_measuring.scene.point_lists import write_cloud_point_lists
from distance_measuring.vision.reference_model import compare_visual_cameras, scene_from_colmap
from distance_measuring.vision.visual_reference import verify_sources, write_json

from .web_export import browser_payload


def export_reference_scene(reference, output, *, target_points=2_000_000, previous_scene=None):
    reference, output = Path(reference).resolve(), Path(output).resolve()
    raw = Path(__file__).resolve().parents[4] / "data/raw"
    if output.is_relative_to(raw) or output.is_relative_to(reference):
        raise ValueError("Viewer output must be outside raw and the reference directory")
    manifest = json.loads((reference / "manifest.json").read_text())
    verify_sources(manifest)
    import pycolmap

    scene = scene_from_colmap(pycolmap.Reconstruction(reference / "mvs/sparse"), manifest)
    if target_points <= len(scene.points) or target_points > 0xFFFFFE:
        raise ValueError("Target must exceed sparse count and fit 24-bit GPU picking")
    source = save_scene(reference / "reference_cameras.json", scene)
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    output.mkdir(parents=True, exist_ok=True)
    order = json.loads((reference / "mvs_image_order.json").read_text())
    by_name = {r["name"]: r for r in manifest["images"]}
    image_ids = [by_name[r["name"]]["image_id"] for r in order]
    cloud_dir = output / f"cloud_{target_points}"
    cloud_manifest = cloud_dir / "manifest.json"
    ply, visibility = reference / "fused.ply", reference / "fused.ply.vis"
    dense_report = json.loads((reference / "dense_report.json").read_text())
    min_views = dense_report.get("minimum_distinct_support_views", 3)
    id_base = max(p.point_id for p in scene.points) + 1
    expected_hashes = {
        str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in [ply, visibility]
    }
    cloud = json.loads(cloud_manifest.read_text()) if cloud_manifest.exists() else None
    if (
        cloud is None
        or cloud["source_sha256"] != expected_hashes
        or cloud["minimum_distinct_support_views"] != min_views
        or cloud.get("id_base") != id_base
        or cloud["target_points"] != target_points - len(scene.points)
        or cloud["image_ids"] != image_ids
    ):
        cloud = write_cloud_point_lists(
            ply,
            visibility,
            image_ids,
            cloud_dir,
            target_points=target_points - len(scene.points),
            id_base=id_base,
            min_views=min_views,
        )
    photos = {}
    first_time = min(r["timestamp_ns"] for r in manifest["images"])
    for camera in scene.cameras:
        record = next(r for r in manifest["images"] if r["image_id"] == camera.image_id)
        image = cv2.imread(str(reference / "mvs/images" / record["name"]), cv2.IMREAD_COLOR)
        if image is None or image.shape[:2] != camera.intrinsics.image_size[::-1]:
            raise ValueError("Undistorted photo and saved camera K dimensions differ")
        thumbnail = cv2.resize(image, (900, round(900 * image.shape[0] / image.shape[1])))
        ok, encoded = cv2.imencode(".jpg", thumbnail, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if not ok:
            raise ValueError("Cannot encode reference preview")
        photos[camera.image_id] = {
            "working_image": image,
            "data_url": "data:image/jpeg;base64," + base64.b64encode(encoded).decode(),
            "exif_orientation": record["exif_orientation"],
            "time_s": (record["timestamp_ns"] - first_time) / 1e9,
        }
    payload = browser_payload(scene, source_hash, photos=photos)
    payload["sparse_point_count"] = len(scene.points)
    payload["point_count"] += cloud["point_count"]
    payload["dense_cloud"] = {
        "point_count": cloud["point_count"],
        "image_ids": cloud["image_ids"],
        "worker": "dense_worker.js",
        "support_histogram": cloud["support_histogram"],
        "minimum_distinct_support_views": min_views,
        "fusion_checks": dense_report.get("fusion", {}),
        "minimum_ncc": dense_report.get("patch_match", {}).get("filter_min_ncc"),
        "shards": [
            {
                "url": f"{cloud_dir.name}/{s['transport_file']}",
                "point_count": s["point_count"],
                "sha256": s["sha256"],
            }
            for s in cloud["shards"]
        ],
    }
    payload["densification"] = {
        "original_point_count": len(scene.points),
        "added_point_count": cloud["point_count"],
    }
    origin = np.asarray(payload["origin"])
    payload["point_cloud_bounds"] = {}
    sparse_xyz = np.asarray([p.position for p in scene.points])
    for key in ["all", "overview"]:
        sparse_bounds = (
            np.array([sparse_xyz.min(axis=0), sparse_xyz.max(axis=0)])
            if key == "all"
            else np.percentile(sparse_xyz, [1, 99], axis=0)
        )
        bounds = np.asarray(cloud["bounds"][key]).copy()
        bounds[0] = np.minimum(bounds[0], sparse_bounds[0])
        bounds[1] = np.maximum(bounds[1], sparse_bounds[1])
        bounds -= origin
        payload["point_cloud_bounds"][key] = bounds.tolist()
    payload["input_sha256"] = manifest["input_sha256"]
    payload["warnings"] += [
        "Image-only self-calibration is not independently certified camera accuracy.",
        "Metric scale is shelved; all coordinates have an unknown common scale.",
    ]
    comparison = None
    if previous_scene:
        comparison = compare_visual_cameras(load_scene(previous_scene), scene)
        payload["camera_comparison"] = comparison
        overlay = Scene(
            tuple(
                SceneCamera(
                    r["image_id"],
                    intrinsics_from_dict(r["intrinsics"]),
                    CameraExtrinsics(np.asarray(r["R_cw"]), np.asarray(r["center"])),
                )
                for r in comparison["previous_camera_overlay"]
            ),
            (),
            (),
            {},
        )
        overlay_payload = browser_payload(overlay, source_hash)
        # Lecture 002 pp. 36–45/81–83: align display gauges only, then draw all
        # centers relative to this viewer's common origin. No metric fit occurs.
        payload["previous_cameras"] = [
            {
                **c,
                "center": (np.asarray(c["world_center"]) - origin).tolist(),
                "vertices": (
                    np.asarray(c["vertices"])
                    * payload["frustum_depth"]
                    / overlay_payload["frustum_depth"]
                ).tolist(),
            }
            for c in overlay_payload["cameras"]
        ]
        write_json(output / "camera_comparison.json", comparison)
    assets = Path(__file__).parent / "web_assets"
    script = (assets / "viewer.js").read_text()
    script = (
        "/* Three.js, MIT license:\n"
        + (assets / "THREE-LICENSE.txt").read_text()
        + "\n*/\n"
        + script
    )
    data = json.dumps(payload, separators=(",", ":"), allow_nan=False).replace("<", "\\u003c")
    html = (
        (assets / "viewer.html")
        .read_text()
        .replace("__SCENE_DATA__", data)
        .replace("__VIEWER_SCRIPT__", script.replace("</script", "<\\/script"))
    )
    for name in ["dense_worker.js", "BSON-LICENSE.txt", "THREE-LICENSE.txt"]:
        shutil.copyfile(assets / name, output / name)
    verify_sources(manifest)
    index = output / "index.html"
    if index.exists() and not (output / "previous_20k.html").exists():
        shutil.copyfile(index, output / "previous_20k.html")
    temporary = output / "index.pending.html"
    temporary.write_text(html)
    temporary.replace(index)
    report = {
        "points": payload["point_count"],
        "sparse_points": len(scene.points),
        "fused_points": cloud["point_count"],
        "minimum_distinct_support_views": min_views,
        "fusion_checks": dense_report.get("fusion", {}),
        "cameras": len(scene.cameras),
        "target_reached": payload["point_count"] >= target_points,
        "output": str(index),
        "scale_status": "not_estimated_visual_only",
        "source_scene_sha256": source_hash,
        "cloud_manifest": str(cloud_manifest),
    }
    write_json(output / "reference_export.json", report)
    return report
