"""Self-contained full-resolution endpoint annotation page; no browser dependency."""

import base64
import hashlib
import json
from pathlib import Path

import cv2
import numpy as np

from distance_measuring.camera import resolve_intrinsics
from distance_measuring.dataset import load_session
from distance_measuring.measurement import validate_annotations
from distance_measuring.vision.pipeline import working_camera


def display_rotation(width, height, orientation):
    """Stored-pixel to upright-pixel affine, including pixel-center offsets."""
    transforms = {
        1: [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
        3: [[-1, 0, width - 1], [0, -1, height - 1], [0, 0, 1]],
        6: [[0, -1, height - 1], [1, 0, 0], [0, 0, 1]],
        8: [[0, 1, 0], [-1, 0, width - 1], [0, 0, 1]],
    }
    if orientation not in transforms:
        raise ValueError("Endpoint review supports EXIF orientations 1, 3, 6 and 8")
    return np.array(transforms[orientation], float)


def review_document(
    scene, annotations, session_path, scene_sha256, *, report=None, calibration_path=None
):
    """Return HTML and input hashes, preserving raw-resolution ruler detail."""
    validate_annotations(scene, annotations)
    if annotations.get("scene_sha256") != scene_sha256:
        raise ValueError("Annotations must be bound to this exact scene SHA-256")
    session = load_session(session_path)
    if session.metadata.session_id != annotations["session_id"]:
        raise ValueError("Review photos and annotations describe different recordings")
    events = {e.index: e for e in session.images}
    photos, hashes = [], {}
    ids = sorted(set(i for group in annotations["view_groups"] for i in group))
    cameras = {c.image_id: c for c in scene.cameras}
    for i in ids:
        camera, event = cameras[i], events[i]
        if camera.intrinsics.source == "calibrated" and calibration_path is None:
            raise ValueError("Calibrated review photos require the original calibration file")
        original = resolve_intrinsics(event.metadata, calibration_path=calibration_path)
        working, resize = working_camera(original, camera.intrinsics.image_size)
        if not np.allclose(working.K, camera.intrinsics.K, atol=1e-6, rtol=1e-7):
            raise ValueError("Review photo calibration differs from scene K")
        path = session.image_path(event)
        content = path.read_bytes()
        hashes[str(path.resolve())] = hashlib.sha256(content).hexdigest()
        image = cv2.imdecode(
            np.frombuffer(content, np.uint8), cv2.IMREAD_COLOR | cv2.IMREAD_IGNORE_ORIENTATION
        )
        if image is None or image.shape[:2] != (original.image_height, original.image_width):
            raise ValueError("Review photo dimensions differ from calibration")
        if any(original.distortion):
            image = original.undistort_image(image)
        orientation = event.metadata.exif_orientation or 1
        rotate = display_rotation(original.image_width, original.image_height, orientation)
        if orientation != 1:
            image = cv2.rotate(
                image,
                {3: cv2.ROTATE_180, 6: cv2.ROTATE_90_CLOCKWISE, 8: cv2.ROTATE_90_COUNTERCLOCKWISE}[
                    orientation
                ],
            )
        ok, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 97])
        if not ok:
            raise ValueError("Cannot encode review photo")
        # Canvas raster centers are at (u+.5, v+.5), whereas camera coordinates
        # use integer pixel centers. Preserve this half-pixel when zooming in.
        canvas_centers = np.array([[1, 0, 0.5], [0, 1, 0.5], [0, 0, 1.0]])
        transform = canvas_centers @ rotate @ np.linalg.inv(resize)
        photos.append(
            {
                "image_id": i,
                "width": image.shape[1],
                "height": image.shape[0],
                "working_to_display": transform.tolist(),
                "display_to_working": np.linalg.inv(transform).tolist(),
                "photo": "data:image/jpeg;base64," + base64.b64encode(encoded).decode(),
            }
        )
    for name in ("session.json", "events.jsonl"):
        path = Path(session_path).resolve() / name
        hashes[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    if calibration_path:
        path = Path(calibration_path).resolve()
        hashes[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    payload = {
        "annotations": annotations,
        "photos": photos,
        "report": report,
        "input_sha256": hashes,
    }
    assets = Path(__file__).parent / "web_assets"
    data = json.dumps(payload, allow_nan=False, separators=(",", ":")).replace("<", "\\u003c")
    html = (assets / "measurement.html").read_text().replace("__MEASUREMENT_DATA__", data)
    return html.replace("__MEASUREMENT_SCRIPT__", (assets / "measurement.js").read_text()), hashes
