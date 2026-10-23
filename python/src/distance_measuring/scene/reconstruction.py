"""M6 track/point reconstruction using M4 observations and M5 camera poses."""

import json
from collections import Counter
from dataclasses import asdict
from pathlib import Path

import numpy as np

from distance_measuring.camera import CameraExtrinsics
from distance_measuring.vision.pose_pipeline import _artifact, _load_matches
from distance_measuring.vision.tracks import MatchEdge, build_tracks
from distance_measuring.vision.triangulation import triangulate_point

from .model import Observation, Scene, SceneCamera, ScenePoint, SceneTrack, save_scene


def _load_inputs(matches_root: Path, poses_root: Path):
    source, intrinsics, normalized, matches = _load_matches(matches_root)
    try:
        poses = json.loads((poses_root / "poses.json").read_text())
        if (
            poses["schema_version"] != 1
            or poses["coordinate_convention"] != "world_to_camera_X_right_Y_down_Z_forward"
        ):
            raise ValueError("M6 requires M5 schema 1 in the world-to-camera convention")
        if any(
            poses[key] != source[key] for key in ("session_id", "selected_image_indices", "images")
        ):
            raise ValueError("M4 and M5 session, selected images or calibration records disagree")
        selected = source["selected_image_indices"]
        if poses["success"] is not True or len(poses["cameras"]) != 3:
            raise ValueError("M6 requires a complete three-camera M5 chain")
        cameras = {}
        for record in poses["cameras"]:
            index = record["index"]
            if index not in selected or index in cameras:
                raise ValueError("M5 camera IDs must match the selected images exactly once")
            camera = SceneCamera(
                index, intrinsics[index], CameraExtrinsics(record["R_cw"], record["C_w"])
            )
            for key, derived in (
                ("O_cw", camera.extrinsics.O_cw),
                ("T_scene", camera.extrinsics.T_scene),
                ("M", camera.M),
            ):
                stored = np.asarray(record[key], dtype=float)
                if (
                    stored.shape != derived.shape
                    or not np.isfinite(stored).all()
                    or not np.allclose(stored, derived, atol=1e-8, rtol=1e-8)
                ):
                    raise ValueError(f"M5 {key} disagrees with its K/R/C camera parameters")
            cameras[index] = camera
        edges, seen = [], set()
        for record in poses["pairs"]:
            pair = tuple(record["indices"])
            if pair not in matches or pair in seen:
                raise ValueError("M5 must contain each M4 pair exactly once")
            seen.add(pair)
            indices, m4_mask = matches[pair]
            with np.load(
                _artifact(poses_root, record["arrays_file"]), allow_pickle=False
            ) as arrays:
                if not np.array_equal(arrays["indices"], indices) or not np.array_equal(
                    arrays["m4_inliers"], m4_mask
                ):
                    raise ValueError(
                        "M5 match indices/M4 masks disagree with the current M4 artifacts"
                    )
                essential, mask, errors = (
                    arrays["essential_inliers"],
                    arrays["pose_inliers"],
                    arrays["sampson_error_normalized"],
                )
            if (
                any(value.shape != m4_mask.shape for value in (essential, mask, errors))
                or essential.dtype != bool
                or mask.dtype != bool
            ):
                raise ValueError(
                    "M5 essential/pose masks and errors must align with all M4 match rows"
                )
            if np.any(essential & ~m4_mask) or np.any(mask & ~essential):
                raise ValueError("M5 pose masks must be subsets of essential and M4 masks")
            if record["pose_inlier_count"] != int(mask.sum()):
                raise ValueError("M5 pose count disagrees with its mask")
            threshold = record["threshold_normalized"]
            if (
                not np.isfinite(threshold)
                or threshold <= 0
                or not np.isfinite(errors[mask]).all()
                or np.any(errors[mask] < 0)
            ):
                raise ValueError(
                    "Accepted M5 matches need finite nonnegative errors and a positive threshold"
                )
            i, j = pair
            for row in np.flatnonzero(mask):
                edges.append(
                    MatchEdge(
                        (i, int(indices[row, 0])),
                        (j, int(indices[row, 1])),
                        float(errors[row] / threshold),
                    )
                )
        if seen != matches.keys():
            raise ValueError("M5 is missing an M4 pair")
        if not isinstance(poses["scale_status"], str) or not isinstance(
            poses["quality_status"], str
        ):
            raise ValueError("M5 must declare its scale and quality status")
        if not isinstance(poses["warnings"], list) or not all(
            isinstance(value, str) for value in poses["warnings"]
        ):
            raise ValueError("M5 warnings must be a list of strings")
        pixels = {
            index: intrinsics[index].pixels_from_normalized(points)
            for index, points in normalized.items()
        }
        return source, poses, cameras, pixels, edges
    except (KeyError, TypeError, AttributeError) as error:
        raise ValueError(f"Malformed M5 reconstruction inputs: {error}") from error


def reconstruct_scene(
    matches_directory: Path | str,
    poses_directory: Path | str,
    output: Path | str,
    *,
    max_reprojection_error_px: float = 3.0,
    min_angle_degrees: float = 1.0,
    solver: str = "svd",
) -> dict:
    roots = [Path(value).resolve() for value in (matches_directory, poses_directory)]
    output = Path(output).resolve()
    raw_root = Path(__file__).resolve().parents[4] / "data" / "raw"
    if output.is_relative_to(raw_root) or any(output.is_relative_to(root) for root in roots):
        raise ValueError("Scene outputs must be outside data/raw and the M4/M5 input directories")
    if not np.isfinite(max_reprojection_error_px) or max_reprojection_error_px <= 0:
        raise ValueError("max_reprojection_error_px must be positive and finite")
    if not np.isfinite(min_angle_degrees) or not 0 < min_angle_degrees < 90:
        raise ValueError("min_angle_degrees must be finite and in (0, 90)")
    if solver not in ("svd", "eigh"):
        raise ValueError("solver must be svd or eigh")
    source, poses, cameras, pixels, edges = _load_inputs(*roots)
    built = build_tracks(edges)
    tracks, points, diagnostics = [], [], []
    for track in built.tracks:
        keys = track.features
        observations = [pixels[image_id][feature_id] for image_id, feature_id in keys]
        result = triangulate_point(
            observations,
            [cameras[index].intrinsics for index, _ in keys],
            [cameras[index].extrinsics for index, _ in keys],
            max_reprojection_error_px=max_reprojection_error_px,
            min_angle_degrees=min_angle_degrees,
            solver=solver,
        )
        # Lecture 002 pp. 136–137: triangulate one shared point from ALL its
        # corresponding views. Do not silently discard a contradictory third view.
        point_id = track.track_id if result.position is not None else None
        records = tuple(
            Observation(
                image_id,
                feature_id,
                tuple(pixel),
                result.reprojection_errors_px[k] if result.reprojection_errors_px else None,
            )
            for k, ((image_id, feature_id), pixel) in enumerate(
                zip(keys, observations, strict=True)
            )
        )
        tracks.append(SceneTrack(track.track_id, records, point_id, result.rejection_reason))
        if point_id is not None:
            points.append(
                ScenePoint(point_id, track.track_id, result.position, result.angle_degrees)
            )
        diagnostics.append(
            {
                "track_id": track.track_id,
                "view_count": len(keys),
                "rejection_reason": result.rejection_reason,
                "max_reprojection_error_px": max(result.reprojection_errors_px)
                if result.reprojection_errors_px
                else None,
                "triangulation_angle_degrees": result.angle_degrees,
            }
        )
    warnings = list(poses["warnings"])
    if poses["scale_status"] == "independent_unit_baselines_relative_scales_unresolved":
        warnings.append(
            "M6 uses M5's independent unit baselines; the relative baseline ratio "
            "and physical scale remain unresolved"
        )
    metadata = {
        "session_id": source["session_id"],
        "selected_image_indices": source["selected_image_indices"],
        "coordinate_convention": poses["coordinate_convention"],
        "scale_status": poses["scale_status"],
        "quality_status": poses["quality_status"],
        "pose_closure": poses["closure"],
        "warnings": warnings,
        "input_images": source["images"],
        "baseline_convention": poses["baseline_convention"],
        "settings": {
            "max_reprojection_error_px": max_reprojection_error_px,
            "min_angle_degrees": min_angle_degrees,
            "solver": solver,
        },
        "lecture_reference": {
            "deck": "002 - Image Formation and Acquisition.pptx",
            "page_numbering": "1-based PPTX slide order",
            "pages": [
                65,
                66,
                67,
                68,
                70,
                71,
                72,
                73,
                74,
                75,
                76,
                82,
                83,
                98,
                101,
                132,
                133,
                134,
                136,
                137,
                138,
            ],
        },
    }
    scene = Scene(
        tuple(cameras[index] for index in source["selected_image_indices"]),
        tuple(tracks),
        tuple(points),
        metadata,
    )
    errors = [
        observation.reprojection_error_px
        for track in tracks
        if track.point_id is not None
        for observation in track.observations
    ]
    summary = {
        "success": bool(points),
        "camera_count": len(cameras),
        "track_count": len(tracks),
        "point_count": len(points),
        "track_view_counts": dict(Counter(str(len(track.observations)) for track in tracks)),
        "accepted_observation_count": len(errors),
        "pose_match_edges": len(edges),
        "conflict_count": len(built.conflicts),
        "redundant_edges": built.redundant_edges,
        "rejected_tracks": dict(
            Counter(track.rejection_reason for track in tracks if track.rejection_reason)
        ),
        "reprojection_rmse_px": float(np.sqrt(np.mean(np.square(errors)))) if errors else None,
        "scale_status": poses["scale_status"],
        "quality_status": poses["quality_status"],
        "warnings": warnings,
    }
    output.mkdir(parents=True, exist_ok=True)
    scene_path = save_scene(output / "scene.json", scene)
    report = {
        "schema_version": 1,
        "output_scene": str(scene_path),
        "summary": summary,
        "tracks": diagnostics,
        "conflicts": [asdict(conflict) for conflict in built.conflicts],
    }
    (output / "triangulation.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    return report
