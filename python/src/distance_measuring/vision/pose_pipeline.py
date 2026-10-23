"""Load M4 ideal working pixels and estimate M5 poses without re-undistortion."""

import json
from pathlib import Path

import cv2
import numpy as np

from distance_measuring.camera import CameraExtrinsics, CameraIntrinsics
from distance_measuring.visualization.cameras import plot_camera_frames

from .overlap import require_connected_overlap
from .pose import chain_camera, estimate_essential, recover_relative_pose


def _artifact(root: Path, name: str) -> Path:
    if not isinstance(name, str) or not name or Path(name).is_absolute():
        raise ValueError("M4 artifact filename must be a relative path")
    path = (root / name).resolve()
    if not path.is_relative_to(root) or path == root:
        raise ValueError("M4 artifact must remain inside the matching directory")
    return path


def _load_matches(root: Path):
    try:
        report = json.loads((root / "matches.json").read_text())
        if not isinstance(report["session_id"], str) or not report["session_id"]:
            raise ValueError("M4 session_id must be a nonempty string")
        if (
            report["schema_version"] != 1
            or report["coordinate_frame"] != "undistorted_working_pixels_ignore_exif"
        ):
            raise ValueError("M5 requires M4 schema 1 in undistorted working pixels")
        selected = report["selected_image_indices"]
        if (
            len(selected) != 3
            or any(type(index) is not int for index in selected)
            or len(set(selected)) != 3
        ):
            raise ValueError("M4 must select exactly three distinct integer image IDs")
        images = {image["index"]: image for image in report["images"]}
        if len(report["images"]) != 3 or set(images) != set(selected):
            raise ValueError("M4 image records must match the selected image IDs")
        cameras, features = {}, {}
        for index, image in images.items():
            data = image["working_intrinsics"]
            camera = CameraIntrinsics.from_K(
                data["camera_matrix"],
                (data["image_width"], data["image_height"]),
                distortion=data["distortion"],
                source=data["source"],
                camera_id=data.get("camera_id"),
                assumptions=data.get("assumptions", ()),
            )
            if any(camera.distortion):
                raise ValueError("M4 working coordinates must already be undistorted")
            cameras[index] = camera
            with np.load(_artifact(root, image["features_file"]), allow_pickle=False) as archive:
                points = archive["points"]
            if points.ndim != 2 or points.shape[1] != 2 or not np.isfinite(points).all():
                raise ValueError("Feature points must be a finite Nx2 array")
            features[index] = camera.normalize_pixels(points)
        a, b, c = selected
        expected = {(a, b), (b, c), (a, c)}
        pairs = {}
        for pair in report["pairs"]:
            ids = tuple(pair["indices"])
            if ids not in expected or ids in pairs:
                raise ValueError("M4 must contain each of the three selected pairs exactly once")
            with np.load(_artifact(root, pair["matches_file"]), allow_pickle=False) as archive:
                indices, mask = archive["indices"], archive["inliers"]
            if (
                indices.ndim != 2
                or indices.shape[1] != 2
                or indices.dtype.kind not in "iu"
                or mask.shape != (len(indices),)
                or mask.dtype != bool
            ):
                raise ValueError("M4 match indices and boolean mask must have aligned rows")
            for column, index in enumerate(ids):
                if np.any(indices[:, column] < 0) or np.any(
                    indices[:, column] >= len(features[index])
                ):
                    raise ValueError("M4 match index is outside its feature array")
            if pair["retained_count"] != len(indices) or pair["inlier_count"] != int(mask.sum()):
                raise ValueError("M4 report counts disagree with match arrays")
            pairs[ids] = (indices, mask)
        if set(pairs) != expected:
            raise ValueError("M4 is missing a selected image pair")
        return report, cameras, features, pairs
    except (KeyError, TypeError, AttributeError) as error:
        raise ValueError(f"Malformed M4 matching data: {error}") from error


def estimate_poses(
    matches_directory: Path | str,
    output: Path | str,
    *,
    threshold_px: float = 1.5,
    min_parallax_degrees: float = 0.25,
    seed: int = 0,
) -> dict:
    root, output = Path(matches_directory).resolve(), Path(output).resolve()
    raw_root = Path(__file__).resolve().parents[4] / "data" / "raw"
    if output.is_relative_to(raw_root) or output.is_relative_to(root):
        raise ValueError("Pose outputs must be outside data/raw and the M4 matching directory")
    if not np.isfinite(threshold_px) or threshold_px <= 0:
        raise ValueError("threshold_px must be positive and finite")
    if not np.isfinite(min_parallax_degrees) or not 0 < min_parallax_degrees < 90:
        raise ValueError("min_parallax_degrees must be finite and between 0 and 90")
    # Validate seed even when no pair has enough correspondences.
    estimate_essential(np.empty((0, 2)), np.empty((0, 2)), seed=seed)
    source, intrinsics, features, pairs = _load_matches(root)
    output.mkdir(parents=True, exist_ok=True)
    (output / "camera_frames.png").unlink(missing_ok=True)
    (output / "poses.json").unlink(missing_ok=True)
    results, relative = [], {}
    for (i, j), (indices, m4_mask) in pairs.items():
        points1, points2 = features[i][indices[:, 0]], features[j][indices[:, 1]]
        # Lecture 002 pp. 70–76, 82–83: K^-1 turns ideal pixels into optical rays.
        # No rectification H or second distortion correction is applied to M4 data.
        # For unequal fx/fy/skew, use the smallest sampling singular value: a
        # conservative circular normalized threshold, not an exact pixel ellipse.
        sampling = min(np.linalg.svd(intrinsics[k].K[:2, :2], compute_uv=False)[-1] for k in (i, j))
        threshold = float(threshold_px / sampling)
        estimate = estimate_essential(
            points1[m4_mask], points2[m4_mask], threshold_normalized=threshold, seed=seed
        )
        pose = recover_relative_pose(
            points1[m4_mask],
            points2[m4_mask],
            estimate,
            min_parallax_degrees=min_parallax_degrees,
            max_reprojection_error_normalized=2 * threshold,
        )
        relative[i, j] = pose
        e_mask, pose_mask = np.zeros(len(indices), bool), np.zeros(len(indices), bool)
        errors = np.full(len(indices), np.inf)
        e_mask[m4_mask], pose_mask[m4_mask] = estimate.inliers, pose.inliers
        errors[m4_mask] = estimate.sampson_error_normalized
        name = f"pair_{i:03d}_{j:03d}_pose.npz"
        arrays = {
            "indices": indices,
            "m4_inliers": m4_mask,
            "essential_inliers": e_mask,
            "pose_inliers": pose_mask,
            "sampson_error_normalized": errors,
        }
        if estimate.E is not None:
            arrays["E"] = estimate.E
        if pose.R is not None:
            arrays.update(R_second_first=pose.R, t_second_first_direction=pose.t_direction)
        np.savez_compressed(output / name, **arrays)
        results.append(
            {
                "indices": [i, j],
                "m4_inlier_count": int(m4_mask.sum()),
                "essential_inlier_count": int(e_mask.sum()),
                "pose_inlier_count": int(pose_mask.sum()),
                "threshold_normalized": threshold,
                "E": estimate.E.tolist() if estimate.E is not None else None,
                "R_second_first": pose.R.tolist() if pose.R is not None else None,
                "t_second_first_direction": pose.t_direction.tolist()
                if pose.t_direction is not None
                else None,
                "cheirality_counts": list(pose.cheirality_counts),
                "median_parallax_degrees": pose.median_parallax_degrees,
                "failure": pose.failure,
                "arrays_file": name,
            }
        )
    overlap = require_connected_overlap(
        source["selected_image_indices"],
        [pair for pair, pose in relative.items() if pose.R is not None and pose.inliers.sum() >= 8],
        stage="verified visual poses",
    )
    a, b, c = source["selected_image_indices"]
    world = {a: CameraExtrinsics(np.eye(3), np.zeros(3))}
    for first, second in ((a, b), (b, c)):
        if first in world and relative[first, second].R is not None:
            world[second] = chain_camera(world[first], relative[first, second])
    closure = None
    if len(world) == 3 and relative[a, c].R is not None:
        direct = relative[a, c]
        delta = direct.R @ world[c].R_cw.T
        rotation_error = float(np.degrees(np.arccos(np.clip((np.trace(delta) - 1) / 2, -1, 1))))
        O_cw = world[c].O_cw
        angle = (
            None
            if np.linalg.norm(O_cw) < 1e-12
            else float(
                np.degrees(
                    np.arccos(
                        np.clip(np.dot(O_cw / np.linalg.norm(O_cw), direct.t_direction), -1, 1)
                    )
                )
            )
        )
        closure = {
            "rotation_error_degrees": rotation_error,
            "unit_chain_translation_direction_error_degrees": angle,
            "rotation_loop_consistent": rotation_error <= 5.0,
        }
    warnings = []
    if any(camera.approximate for camera in intrinsics.values()):
        warnings.append("Intrinsics are approximate; recovered poses depend on those assumptions")
    if len(world) != 3:
        quality = "incomplete_chain"
    elif closure is None:
        quality = "rotation_loop_unchecked"
        warnings.append("Direct first-to-third pose unavailable; rotation loop cannot be checked")
    elif not closure["rotation_loop_consistent"]:
        quality = "rotation_loop_inconsistent"
        warnings.append(
            f"Rotation loop disagrees by {closure['rotation_error_degrees']:.2f} degrees "
            "(warning threshold 5 degrees); frames are not a verified consistent reconstruction"
        )
    else:
        quality = "pairwise_pose_checks_passed"
    report = {
        "schema_version": 1,
        "overlap": overlap,
        "session_id": source["session_id"],
        "selected_image_indices": [a, b, c],
        "coordinate_convention": "world_to_camera_X_right_Y_down_Z_forward",
        "scale_status": "independent_unit_baselines_relative_scales_unresolved",
        "baseline_convention": {f"{a}-{b}": 1.0, f"{b}-{c}": 1.0},
        "settings": {
            "threshold_px": threshold_px,
            "min_parallax_degrees": min_parallax_degrees,
            "seed": seed,
            "rotation_loop_warning_degrees": 5.0,
        },
        "software": {"opencv": cv2.__version__, "numpy": np.__version__},
        "images": source["images"],
        "pairs": results,
        "closure": closure,
        "cameras": [
            {
                "index": index,
                "R_cw": camera.R_cw.tolist(),
                "C_w": camera.C_w.tolist(),
                "O_cw": camera.O_cw.tolist(),
                "T_scene": camera.T_scene.tolist(),
                "M": camera.projection_matrix(intrinsics[index].K).tolist(),
            }
            for index, camera in world.items()
        ],
        "success": len(world) == 3,
        "quality_status": quality,
        "warnings": warnings,
        "camera_frames_image": None,
    }
    if len(world) == 3:
        plot_camera_frames(
            world,
            output / "camera_frames.png",
            quality_note=(
                "WARNING: rotation loop disagrees by "
                f"{closure['rotation_error_degrees']:.2f} degrees"
                if quality == "rotation_loop_inconsistent"
                else None
            ),
        )
        report["camera_frames_image"] = "camera_frames.png"
    (output / "poses.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    return report
