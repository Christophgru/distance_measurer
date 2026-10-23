"""Offline GPU viewer export; lecture 002 pp. 70–76, 81–83, 133–136.

Persisted K/R/C and accepted points are visualization inputs, never refitted.
Packed buffers, photo colors and the browser interface extend the lecture.
"""

import base64
import hashlib
import json
from pathlib import Path

import cv2
import numpy as np

from distance_measuring.camera import resolve_intrinsics
from distance_measuring.dataset import load_session
from distance_measuring.scene import Scene
from distance_measuring.vision.pipeline import working_camera

from .scene_view import build_scene_view


def _buffer(values, dtype):
    return base64.b64encode(np.asarray(values, dtype=dtype).tobytes()).decode("ascii")


def _ids(values):
    values = list(values)
    if any(type(v) is not int or not 0 <= v <= 0xFFFFFFFF for v in values):
        raise ValueError("Browser IDs must be unsigned 32-bit integers; IDs are never truncated")
    return _buffer(values, "<u4")


def browser_payload(scene, source_sha256, *, photos=None, excluded=None):
    """Pack the entire accepted cloud once; no decimation or per-point DOM."""
    if len(scene.points) > 0xFFFFFE:
        raise ValueError("This browser viewer supports at most 16,777,214 points")
    if not scene.cameras and not scene.points:
        raise ValueError("Scene has no points or cameras")
    photos = photos or {}
    xyz = np.array([p.position for p in scene.points], float).reshape(-1, 3)
    centers = np.array([c.extrinsics.C_w for c in scene.cameras], float).reshape(-1, 3)
    all_xyz = np.vstack([xyz, centers])
    origin = np.median(all_xyz, axis=0)
    # Keep float32 GPU coordinates near the origin without changing world units.
    # Lecture 002 pp. 38–45/81–83: this is a display translation, not a pose fit.
    low, high = np.percentile(all_xyz, [1, 99], axis=0)
    span = max(
        float(np.max(high - low)), float(np.ptp(centers, axis=0).max()) if len(centers) else 0, 1e-6
    )
    view = build_scene_view(scene, frustum_depth=0.08 * span)
    tracks = {t.track_id: t for t in scene.tracks}
    errors, counts, offsets, image_ids, pixels, obs_errors = [], [], [0], [], [], []
    colors = np.tile([139, 184, 211], (len(xyz), 1)).astype(np.uint8)
    for row, point in enumerate(scene.points):
        obs = tracks[point.track_id].observations
        known = [o.reprojection_error_px for o in obs if o.reprojection_error_px is not None]
        errors.append(max(known) if known else -1)
        counts.append(len(obs))
        best = np.inf
        for o in obs:
            image_ids.append(o.image_id)
            pixels.append(o.pixel)
            obs_errors.append(
                o.reprojection_error_px if o.reprojection_error_px is not None else -1
            )
            if o.image_id in photos:
                image = photos[o.image_id]["working_image"]
                u, v = np.rint(o.pixel).astype(int)
                error = o.reprojection_error_px if o.reprojection_error_px is not None else np.inf
                if (
                    0 <= u < image.shape[1]
                    and 0 <= v < image.shape[0]
                    and (error < best or best == np.inf)
                ):
                    colors[row] = image[v, u, ::-1]
                    best = error
        offsets.append(len(image_ids))
    cameras = []
    drawings = {c.image_id: c for c in view.cameras}
    for camera in sorted(scene.cameras, key=lambda c: c.image_id):
        drawing = drawings[camera.image_id]
        photo = photos.get(camera.image_id, {})
        cameras.append(
            {
                "image_id": camera.image_id,
                "center": (camera.extrinsics.C_w - origin).tolist(),
                "world_center": camera.extrinsics.C_w.tolist(),
                "R_cw": camera.extrinsics.R_cw.tolist(),
                "K": camera.intrinsics.K.tolist(),
                "image_size": list(camera.intrinsics.image_size),
                "color": drawing.color,
                # Lecture 002 pp. 70–76/133–134: calibrated corner rays; 81–83:
                # P_world=R.T P_camera+C. Shared with the existing desktop viewer.
                "vertices": (drawing.vertices - camera.extrinsics.C_w).tolist(),
                "lines": drawing.lines.tolist(),
                "photo": photo.get("data_url"),
                "exif_orientation": photo.get("exif_orientation", 1),
                "time_s": photo.get("time_s"),
            }
        )
    return {
        "schema_version": 1,
        "session_id": scene.metadata.get("session_id", "Sparse reconstruction"),
        "source_scene_sha256": source_sha256,
        "origin": origin.tolist(),
        "span": span,
        "frustum_depth": view.frustum_depth,
        "point_count": len(xyz),
        "camera_count": len(cameras),
        "positions": _buffer(xyz - origin, "<f4"),
        "colors": _buffer(colors, "u1"),
        "point_ids": _ids(p.point_id for p in scene.points),
        "track_ids": _ids(p.track_id for p in scene.points),
        "errors": _buffer(errors, "<f4"),
        "view_counts": _buffer(counts, "<u4"),
        "observation_offsets": _buffer(offsets, "<u4"),
        "observation_images": _ids(image_ids),
        "observation_pixels": _buffer(pixels, "<f4"),
        "observation_errors": _buffer(obs_errors, "<f4"),
        "cameras": cameras,
        "excluded_images": excluded
        if excluded is not None
        else scene.metadata.get("excluded_images", []),
        "scale_status": scene.metadata.get("scale_status", "unspecified"),
        "quality_status": scene.metadata.get("quality_status", "unspecified"),
        "warnings": scene.metadata.get("warnings", []),
        "units": "reconstruction_units",
        "metric_scale_applied": False,
        "has_photo_colors": bool(photos),
        "geometry_modified": False,
        "densification": scene.metadata.get("densification"),
    }


def export_browser_scene(source, output, *, session_path=None, calibration_path=None):
    source, output = Path(source).resolve(), Path(output).resolve()
    raw = Path(__file__).resolve().parents[4] / "data/raw"
    if output.is_relative_to(raw) or output.is_relative_to(source.parent):
        raise ValueError("Browser export must be outside raw and the source scene directory")
    if session_path is not None and output.is_relative_to(Path(session_path).resolve()):
        raise ValueError("Browser export must be outside the raw photo session")
    if output.suffix.lower() != ".html":
        raise ValueError("Browser output must be an .html file")
    content = source.read_bytes()
    scene = Scene.from_dict(json.loads(content))
    hashes = {str(source): hashlib.sha256(content).hexdigest()}
    photos = {}
    if session_path is not None:
        session = load_session(session_path)
        if scene.metadata.get("session_id") != session.metadata.session_id:
            raise ValueError("Photos and scene must describe the same recording")
        events = {e.index: e for e in session.images}
        for camera in scene.cameras:
            if camera.intrinsics.source == "calibrated" and calibration_path is None:
                raise ValueError(
                    "Embedding calibrated photos requires the original --calibration file"
                )
            event = events.get(camera.image_id)
            if event is None:
                raise ValueError(f"Missing recorded photo for camera {camera.image_id}")
            path = session.image_path(event)
            hashes[str(path.resolve())] = hashlib.sha256(path.read_bytes()).hexdigest()
            image = cv2.imread(str(path), cv2.IMREAD_COLOR | cv2.IMREAD_IGNORE_ORIENTATION)
            original = resolve_intrinsics(event.metadata, calibration_path=calibration_path)
            working, _ = working_camera(original, camera.intrinsics.image_size)
            if not np.allclose(working.K, camera.intrinsics.K, atol=1e-6, rtol=1e-7):
                raise ValueError(
                    "Photo calibration differs from scene K; supply its --calibration file"
                )
            if image is None:
                raise ValueError(f"Cannot decode camera photo {camera.image_id}")
            image = cv2.resize(image, working.image_size, interpolation=cv2.INTER_AREA)
            if any(working.distortion):
                image = working.undistort_image(image)
            thumbnail = cv2.resize(image, (900, round(900 * image.shape[0] / image.shape[1])))
            ok, encoded = cv2.imencode(".jpg", thumbnail, [cv2.IMWRITE_JPEG_QUALITY, 82])
            if not ok:
                raise ValueError("Cannot encode source photo preview")
            photos[camera.image_id] = {
                "working_image": image,
                "data_url": "data:image/jpeg;base64," + base64.b64encode(encoded).decode("ascii"),
                "exif_orientation": event.metadata.exif_orientation,
                "time_s": (event.timestamp_ns - session.start_ns) / 1e9,
            }
        for name in ["session.json", "events.jsonl"]:
            path = Path(session_path).resolve() / name
            hashes[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    if calibration_path:
        path = Path(calibration_path).resolve()
        hashes[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    payload = browser_payload(scene, hashes[str(source)], photos=photos)
    payload["input_sha256"] = hashes
    assets = Path(__file__).parent / "web_assets"
    template = (assets / "viewer.html").read_text()
    script = (assets / "viewer.js").read_text()
    license_text = (assets / "THREE-LICENSE.txt").read_text()
    script = "/* Three.js, MIT license:\n" + license_text + "\n*/\n" + script
    data = json.dumps(payload, separators=(",", ":"), allow_nan=False).replace("<", "\\u003c")
    document = template.replace("__SCENE_DATA__", data).replace(
        "__VIEWER_SCRIPT__", script.replace("</script", "<\\/script")
    )
    if any(hashlib.sha256(Path(p).read_bytes()).hexdigest() != h for p, h in hashes.items()):
        raise ValueError("A viewer input changed during export")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(document)
    return {
        "output": str(output),
        "points": len(scene.points),
        "cameras": len(scene.cameras),
        "source_sha256": hashes[str(source)],
        "input_sha256": hashes,
    }
