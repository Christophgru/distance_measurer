"""Lecture 3D DLT: estimate M, K, R_cw, C_w from known undistorted correspondences."""

import argparse
import json
import sys
from pathlib import Path

from distance_measuring.camera.calibration import calibrate_3d


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "correspondences",
        type=Path,
        help="JSON with world_points, image_points, image_width, image_height",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--solver", choices=("eigh", "svd"), default="eigh")
    parser.add_argument("--camera-id")
    args = parser.parse_args(argv)
    try:
        raw_root = Path(__file__).resolve().parents[2] / "data" / "raw"
        if args.output.resolve().is_relative_to(raw_root.resolve()):
            raise ValueError("Calibration output must be outside data/raw")
        if args.output.resolve() == args.correspondences.resolve():
            raise ValueError("Calibration output cannot replace the correspondence input")
        data = json.loads(args.correspondences.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Correspondence JSON must be an object")
        result = calibrate_3d(
            data["world_points"],
            data["image_points"],
            (data["image_width"], data["image_height"]),
            solver=args.solver,
            camera_id=args.camera_id,
            coordinate_units=data.get("coordinate_units", "world_units"),
        )
        result.save(args.output)
    except (ValueError, OSError, KeyError, TypeError) as error:
        print(f"Calibration failed: {error}", file=sys.stderr)
        return 1
    pose = result.extrinsics[0]
    print(f"Intrinsic source: {result.intrinsics.source}; solver: {args.solver}")
    print(f"K =\n{result.intrinsics.K}\nR_cw =\n{pose.R_cw}")
    print(f"C_w = {pose.C_w}; O_cw = {pose.O_cw}")
    print(f"M =\n{pose.projection_matrix(result.intrinsics.K)}")
    print(f"Reprojection RMSE: {result.rmse_px:.8f} px; calibration: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
