"""Bootstrap two cameras and compact landmarks from immutable P3 correspondences."""

import hashlib
import json
from dataclasses import asdict, replace
from pathlib import Path
from time import perf_counter

import numpy as np
from scipy.spatial.transform import Rotation

from distance_measuring.scene import save_scene
from distance_measuring.scene.model import COORDINATE_FRAME, intrinsics_from_dict
from distance_measuring.visualization.scene_view import build_scene_view
from distance_measuring.visualization.viewer import save_preview

from .bootstrap import (
    DEFAULT_BOOTSTRAP,
    BootstrapConfig,
    BootstrapPair,
    estimate_bootstrap_pose,
    evaluate_bootstrap_pair,
    triangulate_bootstrap,
)
from .pose_pipeline import _artifact


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_candidate(root, record, cameras):
    path = _artifact(root, record["arrays_file"])
    with np.load(path, allow_pickle=False) as arrays:
        ids, first, second, residual, rows = [
            arrays[name]
            for name in (
                "track_ids",
                "source_pixels",
                "target_pixels",
                "parallax_px",
                "selected_indices",
            )
        ]
    if ids.ndim != 1:
        raise ValueError("P3 track IDs must be a vector")
    n = len(ids)
    if (
        ids.dtype.kind not in "iu"
        or np.any(ids < 0)
        or len(set(ids)) != n
        or first.shape != (n, 2)
        or second.shape != (n, 2)
        or residual.shape != (n,)
        or rows.ndim != 1
        or rows.dtype.kind not in "iu"
        or len(set(rows)) != len(rows)
        or np.any(rows < 0)
        or np.any(rows >= n)
        or record["selected_count"] != len(rows)
        or len(record["selected_tracks"]) != len(rows)
    ):
        raise ValueError("P3 selected IDs, pixel rows, residuals and counts must align")
    for row, saved in zip(rows, record["selected_tracks"], strict=True):
        if (
            saved["track_id"] != int(ids[row])
            or not np.array_equal(saved["source_pixel"], first[row])
            or not np.array_equal(saved["target_pixel"], second[row])
            or saved["parallax_px"] != float(residual[row])
        ):
            raise ValueError("P3 selected correspondence arrays disagree with the JSON report")
    i, j = record["image_indices"]
    return BootstrapPair(
        (i, j), ids[rows], first[rows], second[rows], cameras[i], cameras[j], residual[rows]
    )


def bootstrap_session(parallax_directory, output, *, config: BootstrapConfig = DEFAULT_BOOTSTRAP):
    start = perf_counter()
    root, output = Path(parallax_directory).resolve(), Path(output).resolve()
    raw_root = Path(__file__).resolve().parents[4] / "data/raw"
    if output.is_relative_to(root) or output.is_relative_to(raw_root.resolve()):
        raise ValueError("Bootstrap outputs must be outside data/raw and the P3 input directory")
    manifest_path = root / "parallax.json"
    manifest = json.loads(manifest_path.read_text())
    if manifest["schema_version"] != 1 or manifest["coordinate_frame"] != COORDINATE_FRAME:
        raise ValueError("Bootstrap requires P3 schema 1 in ideal working pixels")
    if not isinstance(manifest["session_id"], str) or not manifest["session_id"]:
        raise ValueError("P3 session ID must be a nonempty string")
    if "images" not in manifest:
        raise ValueError("P3 working intrinsics are missing; rerun analyze_track_parallax.py")
    selected = manifest["selected_image_indices"]
    images = {image["index"]: image for image in manifest["images"]}
    if (
        len(selected) < 2
        or any(type(i) is not int or i < 0 for i in selected)
        or selected != sorted(set(selected))
        or set(images) != set(selected)
        or len(images) != len(manifest["images"])
    ):
        raise ValueError("P3 selected image records must be distinct and ordered")
    timestamps = [images[i]["timestamp_ns"] for i in selected]
    if any(type(t) is not int or t < 0 for t in timestamps) or any(
        b <= a for a, b in zip(timestamps[:-1], timestamps[1:], strict=True)
    ):
        raise ValueError("P3 image timestamps must be strictly increasing integer nanoseconds")
    expected_pairs = [list(p) for p in zip(selected[:-1], selected[1:], strict=True)]
    if [p["image_indices"] for p in manifest["pairs"]] != expected_pairs:
        raise ValueError("P3 pairs must follow consecutive selected capture order")
    cameras = {i: intrinsics_from_dict(images[i]["working_intrinsics"]) for i in selected}
    fingerprints = {manifest_path: _sha(manifest_path)}
    output.mkdir(parents=True, exist_ok=False)
    report = {
        "schema_version": 1,
        "session_id": manifest["session_id"],
        "coordinate_frame": COORDINATE_FRAME,
        "selected_image_indices": selected,
        "settings": asdict(config),
        "source_directory": str(root),
        "input_manifest_sha256": fingerprints[manifest_path],
        "input_quality_status": manifest.get("quality_status"),
        "raw_input_sha256": manifest.get("raw_input_sha256", {}),
        "warnings": list(manifest.get("warnings", [])),
        "candidates": [],
        "success": False,
        "bootstrap_pair": None,
        "output_scene": None,
        "summary": {"camera_count": 0, "point_count": 0, "reprojection_rmse_px": None},
    }
    print("Bootstrap thresholds: " + json.dumps(report["settings"], sort_keys=True), flush=True)
    for record in manifest["pairs"]:
        i, j = record["image_indices"]
        if type(record["available"]) is not bool:
            raise ValueError("P3 pair availability must be boolean")
        if not record["available"]:
            reason = "p3_rotation_unavailable: " + record.get("reason", "unspecified")
            report["candidates"].append(
                {
                    "image_indices": [i, j],
                    "stage": "preselection",
                    "accepted": False,
                    "reasons": [reason],
                }
            )
            print(f"Pair {i}-{j}: rejected: {reason}", flush=True)
            continue
        path = _artifact(root, record["arrays_file"])
        fingerprints[path] = _sha(path)
        pair = _load_candidate(root, record, cameras)
        selection = evaluate_bootstrap_pair(pair, config)
        candidate = {
            "image_indices": [i, j],
            "selection": selection,
            "stage": "preselection",
            "accepted": False,
            "reasons": list(selection["reasons"]),
            "input_gyro_compensation_increases_motion": record.get(
                "compensation_increases_median_motion"
            ),
        }
        report["candidates"].append(candidate)
        print(
            f"Pair {i}-{j}: tracks={len(pair.track_ids)}, cells={selection['occupied_cells']}, "
            f"score={selection['score']:.3f}, "
            f"visual rotation residual={selection['rotation_only_fit_median_px']}",
            flush=True,
        )
        if not selection["preselection_passed"]:
            print("  Rejected: " + "; ".join(candidate["reasons"]), flush=True)
            continue
        pose = estimate_bootstrap_pose(pair, config)
        candidate.update(stage="pose", pose=pose.report, reasons=list(pose.report["reasons"]))
        stem = f"pair_{i:03d}_{j:03d}"
        np.savez_compressed(
            output / f"{stem}_bootstrap.npz",
            track_ids=pair.track_ids,
            source_pixels=pair.first_pixels,
            target_pixels=pair.second_pixels,
            essential_inliers=pose.essential.inliers,
            pose_inliers=pose.relative.inliers,
            sampson_error_normalized=pose.essential.sampson_error_normalized,
        )
        candidate["arrays_file"] = f"{stem}_bootstrap.npz"
        print(
            f"  Pose: input={pose.report['input_track_count']}, "
            f"RANSAC={pose.report['ransac_inlier_count']}, "
            f"pose={pose.report['pose_inlier_count']}, runtime={pose.report['runtime_s']:.4f} s",
            flush=True,
        )
        if not pose.report["success"]:
            print("  Rejected: " + "; ".join(candidate["reasons"]), flush=True)
            continue
        print(f"  R_second_first={pose.report['R_second_first']}", flush=True)
        print(f"  Translation direction={pose.report['t_second_first_direction']}", flush=True)
        landmarks = triangulate_bootstrap(pair, pose, config)
        candidate.update(
            stage="landmarks", landmarks=landmarks.report, reasons=list(landmarks.report["reasons"])
        )
        print(
            f"  Landmarks={landmarks.report['point_count']}, "
            f"RMSE={landmarks.report['reprojection_rmse_px']}, "
            f"runtime={landmarks.report['runtime_s']:.4f} s",
            flush=True,
        )
        if not landmarks.report["success"]:
            print("  Rejected: " + "; ".join(candidate["reasons"]), flush=True)
            continue
        if "R_second_first" in record:
            candidate["gyro_visual_rotation_disagreement_degrees"] = float(
                np.degrees(
                    Rotation.from_matrix(
                        pose.relative.R @ np.asarray(record["R_second_first"]).T
                    ).magnitude()
                )
            )
        candidate.update(stage="complete", accepted=True)
        scene = replace(
            landmarks.scene,
            metadata={
                **landmarks.scene.metadata,
                "session_id": manifest["session_id"],
                "input_manifest_sha256": fingerprints[manifest_path],
                "input_quality_status": manifest.get("quality_status"),
                "warnings": report["warnings"],
                "selection_score": selection["score"],
            },
        )
        scene_path = save_scene(output / "scene.json", scene)
        preview = save_preview(scene, build_scene_view(scene), output / "bootstrap_preview.png")
        report.update(
            success=True,
            bootstrap_pair=[i, j],
            output_scene=str(scene_path),
            preview_file=preview.name,
            summary={
                "camera_count": 2,
                "point_count": len(scene.points),
                "reprojection_rmse_px": landmarks.report["reprojection_rmse_px"],
            },
        )
        break
    report["unregistered_image_indices"] = [
        i for i in selected if i not in (report["bootstrap_pair"] or [])
    ]
    report["remaining_pair_count"] = len(manifest["pairs"]) - len(report["candidates"])
    report["failure_reason"] = (
        None
        if report["success"]
        else "No pair passed the bootstrap selection, pose and landmark gates"
    )
    if any(_sha(path) != expected for path, expected in fingerprints.items()):
        raise ValueError("P3 bootstrap inputs changed during the run")
    report["input_files_sha256"] = {
        str(p.relative_to(root)): sha for p, sha in fingerprints.items()
    }
    report["total_runtime_s"] = perf_counter() - start
    (output / "bootstrap.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    return report
