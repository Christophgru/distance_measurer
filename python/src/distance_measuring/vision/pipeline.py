"""Three-view M4 diagnostics, retaining lecture 002's K and pixel conventions."""

import json
from dataclasses import asdict
from pathlib import Path

import cv2
import numpy as np

from distance_measuring.camera import CameraIntrinsics, resolve_intrinsics
from distance_measuring.dataset import load_session
from distance_measuring.visualization.matches import draw_match_diagnostic

from .epipolar import estimate_fundamental, rectify_matches
from .features import SiftBackend
from .matching import match_descriptors


def working_camera(
    camera: CameraIntrinsics, size: tuple[int, int]
) -> tuple[CameraIntrinsics, np.ndarray]:
    """Resize with OpenCV's pixel-center convention: u' = sx*(u+0.5)-0.5."""
    sx, sy = size[0] / camera.image_width, size[1] / camera.image_height
    A = np.array([[sx, 0, (sx - 1) / 2], [0, sy, (sy - 1) / 2], [0, 0, 1]])
    # Lecture 002 pp. 70–76, 102: K contains pixel sampling; changing sampling
    # changes K, while the external [R|O] and 3x4 M = K[R|O] keep their meaning.
    resized = CameraIntrinsics.from_K(
        A @ camera.K,
        size,
        distortion=camera.distortion,
        source=camera.source,
        camera_id=camera.camera_id,
        assumptions=camera.assumptions,
    )
    return resized, A


def _write_image(path: Path, image: np.ndarray) -> None:
    if not cv2.imwrite(str(path), image, [cv2.IMWRITE_JPEG_QUALITY, 92]):
        raise OSError(f"Could not write {path}")


def match_session(
    session_path: Path | str,
    output: Path | str,
    *,
    image_indices: tuple[int, int, int] = (0, 1, 2),
    calibration_path: Path | None = None,
    max_image_size: int = 1600,
    max_features: int = 6000,
    ratio: float = 0.75,
    mutual: bool = True,
    threshold_px: float = 1.5,
    seed: int = 0,
) -> dict:
    output, session_path = Path(output).resolve(), Path(session_path).resolve()
    raw_root = Path(__file__).resolve().parents[4] / "data" / "raw"
    if output.is_relative_to(session_path) or output.is_relative_to(raw_root):
        raise ValueError("Matching outputs must be outside data/raw and the raw session directory")
    if len(image_indices) != 3 or len(set(image_indices)) != 3:
        raise ValueError("Select exactly three distinct recorded image indices")
    if type(max_image_size) is not int or max_image_size < 32:
        raise ValueError("max_image_size must be an integer of at least 32 pixels")
    backend = SiftBackend(max_features=max_features)
    # Validate options even when selected images are featureless.
    match_descriptors(np.empty((0, 128)), np.empty((0, 128)), ratio=ratio, mutual=mutual)
    estimate_fundamental(np.empty((0, 2)), np.empty((0, 2)), threshold_px=threshold_px, seed=seed)
    session = load_session(session_path)
    by_index = {image.index: image for image in session.images}
    if any(index not in by_index for index in image_indices):
        raise ValueError(f"Requested image indices {image_indices}; available: {sorted(by_index)}")
    images, features, image_reports = {}, {}, []
    for index in image_indices:
        event = by_index[index]
        # Preserve lecture/M3's stored-pixel convention, ignoring JPEG EXIF rotation.
        image = cv2.imread(
            str(session.image_path(event)), cv2.IMREAD_COLOR | cv2.IMREAD_IGNORE_ORIENTATION
        )
        if image is None:
            raise ValueError(f"Cannot decode image {index}")
        original = resolve_intrinsics(event.metadata, calibration_path=calibration_path)
        scale = min(1.0, max_image_size / max(original.image_size))
        size = tuple(max(1, round(value * scale)) for value in original.image_size)
        camera, A = working_camera(original, size)
        image = cv2.resize(image, size, interpolation=cv2.INTER_AREA)
        # Lecture 002 pp. 82–83 and 138 assume ideal pinhole pixels. Remove lens
        # distortion before SIFT/F/rectification so all three use the same frame.
        image = camera.undistort_image(image) if any(camera.distortion) else image
        images[index] = image
        features[index] = backend.extract(image)
        image_reports.append(
            {
                "index": index,
                "filename": event.filename,
                "timestamp_ns": event.timestamp_ns,
                "stored_intrinsics": original.to_dict(),
                "working_intrinsics": camera.without_distortion().to_dict(),
                "stored_ideal_to_working_ideal_affine": A.tolist(),
                "feature_count": len(features[index]),
                "features_file": f"image_{index:03d}_features.npz",
            }
        )
    if len({image.shape[:2] for image in images.values()}) != 1:
        raise ValueError(
            "M4 rectification requires the three images to have equal working dimensions"
        )
    output.mkdir(parents=True, exist_ok=True)
    for index in image_indices:
        np.savez_compressed(output / f"image_{index:03d}_features.npz", **asdict(features[index]))
    report = {
        "schema_version": 1,
        "session_id": session.metadata.session_id,
        "selected_image_indices": list(image_indices),
        "coordinate_frame": "undistorted_working_pixels_ignore_exif",
        "settings": {
            "backend": "SIFT",
            "max_image_size": max_image_size,
            "max_features": max_features,
            "ratio": ratio,
            "mutual": mutual,
            "threshold_px": threshold_px,
            "seed": seed,
        },
        "software": {"opencv": cv2.__version__, "numpy": np.__version__},
        "images": image_reports,
        "pairs": [],
    }
    # Required pairs follow TODO order: 0↔1, 1↔2, 0↔2 (recorded IDs can differ).
    a, b, c = image_indices
    for i, j in ((a, b), (b, c), (a, c)):
        first, second = features[i], features[j]
        matches = match_descriptors(
            first.descriptors, second.descriptors, ratio=ratio, mutual=mutual
        )
        p1, p2 = first.points[matches.indices[:, 0]], second.points[matches.indices[:, 1]]
        geometry = estimate_fundamental(p1, p2, threshold_px=threshold_px, seed=seed)
        stem = f"pair_{i:03d}_{j:03d}"
        # Remove only this generated preview so a failed rerun cannot leave a
        # previous successful rectification looking like the current result.
        (output / f"{stem}_rectified.jpg").unlink(missing_ok=True)
        pair = {
            "indices": [i, j],
            "raw_count": matches.raw_count,
            "two_neighbor_count": matches.two_neighbor_count,
            "ratio_count": matches.ratio_count,
            "retained_count": len(matches),
            "inlier_count": int(geometry.inliers.sum()),
            "outlier_count": int((~geometry.inliers).sum()),
            "F": geometry.F.tolist() if geometry.F is not None else None,
            "failure": geometry.failure,
            "rectification_failure": None,
            "matches_file": f"{stem}_matches.npz",
            "debug_image": f"{stem}_matches.jpg",
            "rectified_image": None,
        }
        arrays = {
            "indices": matches.indices,
            "distances": matches.distances,
            "ratios": matches.ratios,
            "inliers": geometry.inliers,
            "sampson_error_px": geometry.sampson_error_px,
        }
        if geometry.F is not None:
            arrays["F"] = geometry.F
        title = f"Images {i} - {j}: ideal working pixels"
        if geometry.failure:
            title = f"Images {i} - {j}: VERIFICATION FAILED"
        _write_image(
            output / pair["debug_image"],
            draw_match_diagnostic(images[i], images[j], p1, p2, geometry.inliers, title=title),
        )
        if geometry.F is not None:
            try:
                rect = rectify_matches(p1, p2, geometry, (images[i].shape[1], images[i].shape[0]))
                size = (images[i].shape[1], images[i].shape[0])
                warped1 = cv2.warpPerspective(images[i], rect.H_first, size)
                warped2 = cv2.warpPerspective(images[j], rect.H_second, size)
                rectified_name = f"{stem}_rectified.jpg"
                _write_image(
                    output / rectified_name,
                    draw_match_diagnostic(
                        warped1,
                        warped2,
                        rect.first_points,
                        rect.second_points,
                        geometry.inliers,
                        title=f"Images {i} - {j}: RECTIFIED | horizontal scanline guides",
                        rectified=True,
                    ),
                )
                vertical = np.abs(rect.first_points[:, 1] - rect.second_points[:, 1])[
                    geometry.inliers
                ]
                pair["rectified_vertical_error_px"] = {
                    "median": float(np.median(vertical)),
                    "p95": float(np.percentile(vertical, 95)),
                }
                arrays.update(H_first=rect.H_first, H_second=rect.H_second)
                pair["rectified_image"] = rectified_name
            except (ValueError, cv2.error) as error:
                pair["rectification_failure"] = str(error)
        np.savez_compressed(output / pair["matches_file"], **arrays)
        report["pairs"].append(pair)
    report["success"] = all(pair["rectified_image"] is not None for pair in report["pairs"])
    (output / "matches.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    return report
