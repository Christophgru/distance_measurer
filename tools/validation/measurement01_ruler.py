"""Reproduce the failed M9 ruler gate; print diagnostics without changing a scene.

This is a recording-specific experiment, not the M9 measurement engine. Manual
ticks refer to the far edge of the top ruler face in images 0, 1 and 2. Lecture
002 page numbers below are one-based PPTX slide order.
"""

import argparse
import hashlib
import json
from itertools import combinations
from pathlib import Path

import numpy as np

from distance_measuring.scene import Scene
from distance_measuring.vision.triangulation import triangulate_point

# Manually inspected cm 5, 10, 15, 20 ticks, in each image's stored-orientation
# 1600x1200 working frame. Upright display (x,y) becomes (u,v)=(y,1199-x).
# Precision records the coordinate conversion, not subpixel annotation accuracy.
TICKS = np.array(
    [
        [
            [989.664243, 658.998295],
            [882.097864, 535.621752],
            [794.753580, 432.992566],
            [722.821127, 345.458695],
        ],
        [
            [787.092634, 832.548505],
            [819.764006, 713.092234],
            [858.112646, 583.895617],
            [901.958503, 437.177589],
        ],
        [
            [653.607949, 612.068552],
            [645.571903, 466.195462],
            [638.644795, 319.090772],
            [632.559198, 173.115464],
        ],
    ]
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "scene",
        type=Path,
        nargs="?",
        default=Path(__file__).resolve().parents[2] / "data/processed/measurement01/m7/scene.json",
    )
    args = parser.parse_args()
    content = args.scene.read_bytes()
    from distance_measuring.scene.storage import decode_document

    scene = Scene.from_dict(decode_document(content, args.scene.suffix))
    cameras = {camera.image_id: camera for camera in scene.cameras}
    if not all(i in cameras for i in range(3)):
        parser.error("This experiment requires measurement01 cameras 0, 1 and 2")
    if any(cameras[i].intrinsics.image_size != (1600, 1200) for i in range(3)):
        parser.error("Annotations require the original 1600x1200 working frame")

    groups = [*combinations(range(3), 2), (0, 1, 2)]
    results = []
    for ids in groups:
        intrinsics = [cameras[i].intrinsics for i in ids]
        poses = [cameras[i].extrinsics for i in ids]
        accepted = []
        candidates = []
        eigen_deltas = []
        for tick in range(4):
            pixels = TICKS[list(ids), tick]
            # Lecture 002 pp. 81–83: M=K[R|-RC], with homogeneous scene points.
            # pp. 133–136: triangulate calibrated rays and check their projection.
            normal = triangulate_point(pixels, intrinsics, poses)
            accepted.append(normal.rejection_reason)
            # Deliberately relaxed ONLY to expose failure; these candidates must
            # never enter a scene or be treated as accepted metric measurements.
            candidate = triangulate_point(pixels, intrinsics, poses, max_reprojection_error_px=1e6)
            if candidate.position is None:
                raise ValueError(f"Diagnostic candidate unavailable: {candidate.rejection_reason}")
            candidates.append(candidate)
            # Lecture 002 pp. 98/101: minimum eigenvector of A.T@A. Reused here
            # for the 4-coordinate triangulation DLT, rather than 12 camera terms.
            eigen = triangulate_point(
                pixels, intrinsics, poses, max_reprojection_error_px=1e6, solver="eigh"
            )
            if eigen.position is None:
                raise ValueError(f"Eigen diagnostic unavailable: {eigen.rejection_reason}")
            eigen_deltas.append(
                float(np.linalg.norm(np.subtract(candidate.position, eigen.position)))
            )

        # Lecture 002 p. 36's orthonormal Euclidean basis supplies this norm.
        lengths = np.linalg.norm(np.diff([p.position for p in candidates], axis=0), axis=1)
        # p. 93 motivates known spacing. This one-interval scale plus HELD-OUT
        # intervals is our validation extension; it does not calibrate K or poses.
        exploratory_cm = lengths * (5.0 / lengths[0])
        deviations = 100 * np.abs(exploratory_cm[1:] / 5.0 - 1)
        results.append(
            {
                "image_ids": ids,
                "tick_cm": [5, 10, 15, 20],
                "normal_rejections": accepted,
                "diagnostic_reprojection_errors_px": [p.reprojection_errors_px for p in candidates],
                "diagnostic_interval_lengths_reconstruction_units": lengths.tolist(),
                "reference_interval_cm": [5, 10],
                "diagnostic_held_out_lengths_cm": exploratory_cm[1:].tolist(),
                "diagnostic_held_out_deviations_percent": deviations.tolist(),
                "svd_eigh_max_position_difference": max(eigen_deltas),
                "exceeds_10_percent": bool(np.any(deviations > 10)),
            }
        )
    passed = all(
        not any(result["normal_rejections"]) and not result["exceeds_10_percent"]
        for result in results
    )
    print(
        json.dumps(
            {
                "scene_sha256": hashlib.sha256(content).hexdigest(),
                "annotation_method": "manual far-edge centimetre ticks; approximate working pixels",
                "working_pixels": TICKS.tolist(),
                "scale_applied_to_scene": False,
                "diagnostic_candidates_are_accepted_measurements": False,
                "passed": passed,
                "groups": results,
            },
            indent=2,
            allow_nan=False,
        )
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
