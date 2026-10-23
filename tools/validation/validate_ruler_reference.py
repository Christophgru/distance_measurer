"""Check a held-out ruler against frozen visual cameras; never fit their poses.

Lecture 002 pp. 81–83, 98/101, 133–137: homogeneous projection, least squares
and triangulation of the same physical graduations. Page numbers are PPTX order.
"""

import argparse
import hashlib
import json
import sys
from itertools import combinations
from pathlib import Path

import numpy as np

from distance_measuring.scene import load_scene
from distance_measuring.vision.triangulation import triangulate_point


def check_ruler(scene, annotation, *, allow_unusable_observations=False):
    if scene.metadata.get("quality_status") != "visual_validation_passed":
        raise ValueError("Ruler check requires validated visual cameras")
    if annotation["session_id"] != scene.metadata["session_id"]:
        raise ValueError("Ruler annotation belongs to a different recording")
    if annotation["coordinate_frame"] != "undistorted_working_pixels_ignore_exif":
        raise ValueError("Ruler coordinates must use undistorted stored working pixels")
    cameras = {c.image_id: c for c in scene.cameras}
    ids = sorted(int(i) for i in annotation["working_pixels"])
    if len(ids) < 2 or not set(ids) <= cameras.keys():
        raise ValueError("Ruler requires at least two retained camera observations")
    if any(list(cameras[i].intrinsics.image_size) != annotation["working_image_size"] for i in ids):
        raise ValueError("Ruler working image size differs from the camera calibration")
    cm = np.array(annotation["tick_cm"], float)
    xy = np.array([annotation["working_pixels"][str(i)] for i in ids], float)
    if (
        cm.ndim != 1
        or len(cm) < 3
        or np.any(np.diff(cm) <= 0)
        or xy.shape != (len(ids), len(cm), 2)
    ):
        raise ValueError("Need increasing cm labels and aligned per-view tick coordinates")
    original_ids = ids[:]
    observation_attempts = []
    if allow_unusable_observations:
        selected = None
        for count in range(len(ids), 1, -1):
            viable = []
            for group in combinations(range(len(ids)), count):
                group_ids = [ids[n] for n in group]
                geometry = [
                    triangulate_point(
                        xy[list(group), k],
                        [cameras[i].intrinsics for i in group_ids],
                        [cameras[i].extrinsics for i in group_ids],
                    )
                    for k in range(len(cm))
                ]
                passed = all(p.position is not None for p in geometry)
                observation_attempts.append(
                    {
                        "image_ids": group_ids,
                        "geometry_passed": passed,
                        "normal_rejections": [p.rejection_reason for p in geometry],
                    }
                )
                if passed:
                    viable.append((min(p.angle_degrees for p in geometry), group))
            if viable:
                # Choose by ray conditioning BEFORE evaluating any known length.
                selected = max(viable)[1]
                break
        if selected is not None:
            xy = xy[list(selected)]
            ids = [ids[n] for n in selected]
    normal, diagnostic = [], []
    for k in range(len(cm)):
        # Lecture 002 pp. 81–83: M=K[R|-RC], P_h=(X,Y,Z,1). Cameras stay fixed.
        args = (
            xy[:, k],
            [cameras[i].intrinsics for i in ids],
            [cameras[i].extrinsics for i in ids],
        )
        normal.append(triangulate_point(*args))
        # Rejected candidates expose failure only, never accepted measurements.
        diagnostic.append(triangulate_point(*args, max_reprojection_error_px=1e6))
    a, b = annotation["reference_interval_indices"]
    held_indices = annotation["held_out_interval_indices"]
    if (
        not (0 <= a < b < len(cm))
        or any(not (0 <= i < j < len(cm)) or {i, j} == {a, b} for i, j in held_indices)
        or not held_indices
    ):
        raise ValueError("Reference and held-out intervals must be distinct valid tick pairs")
    scale, held = None, []
    if all(p.position is not None for p in diagnostic):
        points = np.array([p.position for p in diagnostic])
        # Lecture 002 pp. 36, 93: one known spacing supplies a temporary metric
        # reference. Other intervals validate it; they cannot change K/R/C.
        reference = np.linalg.norm(points[b] - points[a])
        if reference <= 0:
            raise ValueError("Ruler reference interval triangulates to zero length")
        scale = (cm[b] - cm[a]) / (100 * reference)
        for i, j in held_indices:
            measured = float(np.linalg.norm(points[j] - points[i]) * scale * 100)
            held.append(
                {
                    "tick_cm": [float(cm[i]), float(cm[j])],
                    "reference_scaled_length_cm": measured,
                    "expected_length_cm": float(cm[j] - cm[i]),
                    "deviation_percent": float(100 * abs(measured / (cm[j] - cm[i]) - 1)),
                }
            )
    accepted = all(p.position is not None for p in normal)
    return {
        "image_ids": ids,
        "excluded_observation_image_ids": sorted(set(original_ids) - set(ids)),
        "observation_selection_attempts": observation_attempts,
        "observation_selection_uses_known_lengths": False,
        "annotation_method": annotation.get("annotation_method"),
        "normal_rejections": [p.rejection_reason for p in normal],
        "reprojection_errors_px": [list(p.reprojection_errors_px) for p in normal],
        "triangulation_angles_degrees": [p.angle_degrees for p in normal],
        "all_ticks_accepted": accepted,
        "diagnostic_reference_scale_m_per_unit": scale,
        "diagnostic_held_out_intervals": held,
        "passes_10_percent_and_reprojection": bool(
            accepted and held and all(r["deviation_percent"] <= 10 for r in held)
        ),
        "ruler_used_for_camera_fit": False,
        "scale_applied_to_scene": False,
        "diagnostic_values_are_accepted_measurements": False,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scene", type=Path)
    parser.add_argument("annotations", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--allow-unusable-observations", action="store_true")
    args = parser.parse_args(argv)
    try:
        output = args.output.resolve()
        raw = Path(__file__).resolve().parents[2] / "data/raw"
        if any(output.is_relative_to(p) for p in (raw, args.scene.resolve().parent)):
            raise ValueError("Ruler output must be outside raw and source scene directories")
        scene = load_scene(args.scene)
        annotation = json.loads(args.annotations.read_text())
        hashes = annotation.get("input_sha256", {}).copy()
        for p in (args.scene, args.annotations):
            hashes[str(p.resolve())] = hashlib.sha256(p.read_bytes()).hexdigest()
        if any(hashlib.sha256(Path(p).read_bytes()).hexdigest() != h for p, h in hashes.items()):
            raise ValueError("Ruler annotation source changed")
        report = check_ruler(
            scene, annotation, allow_unusable_observations=args.allow_unusable_observations
        )
        report["input_sha256"] = hashes
        output.mkdir(parents=True, exist_ok=True)
        (output / "ruler_reference.json").write_text(
            json.dumps(report, indent=2, allow_nan=False) + "\n"
        )
        print(json.dumps(report, indent=2, allow_nan=False))
        return 0 if report["passes_10_percent_and_reprojection"] else 1
    except (ValueError, OSError) as error:
        print(f"Cannot validate ruler: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
