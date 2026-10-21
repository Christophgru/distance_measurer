"""Reproduce the lecture's 3D/eigenvector/RQ calibration on a known synthetic camera."""

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation

from distance_measuring.camera import CameraExtrinsics, CameraIntrinsics
from distance_measuring.camera.calibration import calibrate_3d


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("data/processed/m3_synthetic"))
    parser.add_argument("--noise-px", type=float, default=0.0)
    args = parser.parse_args(argv)
    try:
        raw_root = Path(__file__).resolve().parents[2] / "data" / "raw"
        if args.output_dir.resolve().is_relative_to(raw_root.resolve()):
            raise ValueError("Synthetic outputs must be outside data/raw")
        if not np.isfinite(args.noise_px) or args.noise_px < 0:
            raise ValueError("noise-px must be finite and nonnegative")
        rng = np.random.default_rng(7)
        true_K = CameraIntrinsics(900, 880, 319.5, 239.5, 640, 480, skew=1.0)
        true_pose = CameraExtrinsics(
            Rotation.from_euler("xyz", [12, -18, 7], degrees=True).as_matrix(),
            np.array([0.2, -0.1, -3.0]),
        )
        points = rng.uniform(-0.6, 0.6, (80, 3))
        pixels = true_K.project(points, true_pose) + rng.normal(0, args.noise_px, (len(points), 2))
        result = calibrate_3d(
            points,
            pixels,
            true_K.image_size,
            camera_id="synthetic",
            coordinate_units="synthetic_world_units",
        )
        args.output_dir.mkdir(parents=True, exist_ok=True)
        result.save(args.output_dir / "lecture_dlt.yaml")
        correspondences = {
            "world_points": points.tolist(),
            "image_points": pixels.tolist(),
            "image_width": 640,
            "image_height": 480,
            "coordinate_units": "synthetic_world_units",
            "ground_truth": {
                "K": true_K.K.tolist(),
                "R_cw": true_pose.R_cw.tolist(),
                "C_w": true_pose.C_w.tolist(),
            },
        }
        (args.output_dir / "known_correspondences.json").write_text(
            json.dumps(correspondences, indent=2) + "\n", encoding="utf-8"
        )
    except (ValueError, OSError) as error:
        print(f"Synthetic calibration failed: {error}", file=sys.stderr)
        return 1
    pose = result.extrinsics[0]
    rotation_error = Rotation.from_matrix(pose.R_cw @ true_pose.R_cw.T).magnitude() * 180 / np.pi
    print("Synthetic example; this is not a phone calibration profile.")
    print(f"K (internal) =\n{result.intrinsics.K}")
    print(f"T_scene (external) =\n{pose.T_scene}")
    print(f"M = K P0 T_scene (3x4) =\n{pose.projection_matrix(result.intrinsics.K)}")
    print(f"Smallest eigenvalue: {result.diagnostics['eigenvalues'][0]:.6g}")
    print(f"Maximum K error: {np.max(np.abs(result.intrinsics.K - true_K.K)):.8g} px")
    print(f"Rotation error: {rotation_error:.8g} degrees")
    print(f"Camera center error: {np.linalg.norm(pose.C_w - true_pose.C_w):.8g} world units")
    print(f"Reprojection RMSE: {result.rmse_px:.8g} px; outputs: {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
