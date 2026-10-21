"""Calibrate K, distortion, and target-view extrinsics from checkerboard images."""

import argparse
import sys
from pathlib import Path

from distance_measuring.camera.calibration import calibrate_checkerboard_folder


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("images", type=Path)
    parser.add_argument("--columns", type=int, required=True, help="Inner corners across the board")
    parser.add_argument("--rows", type=int, required=True, help="Inner corners down the board")
    parser.add_argument("--square-size-m", type=float, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--camera-id", help="Logical camera ID, when known (e.g. 0)")
    parser.add_argument("--solver", choices=("eigh", "svd"), default="eigh")
    parser.add_argument(
        "--fit-skew", action="store_true", help="Estimate skew instead of fixing it at 0"
    )
    args = parser.parse_args(argv)
    try:
        raw_root = Path(__file__).resolve().parents[2] / "data" / "raw"
        if args.output.resolve().is_relative_to(raw_root.resolve()):
            raise ValueError("Calibration output must be outside data/raw")
        if args.output.resolve().is_relative_to(args.images.resolve()):
            raise ValueError("Write calibration output outside the raw calibration image directory")
        result = calibrate_checkerboard_folder(
            args.images,
            args.columns,
            args.rows,
            args.square_size_m,
            solver=args.solver,
            camera_id=args.camera_id,
            fit_skew=args.fit_skew,
        )
        result.save(args.output)
    except (ValueError, OSError) as error:
        print(f"Calibration failed: {error}", file=sys.stderr)
        return 1
    print(f"Intrinsic source: {result.intrinsics.source}")
    print(f"Solver: {args.solver}; views: {len(result.extrinsics)}")
    print(f"Reprojection RMSE: {result.initial_rmse_px:.6f} -> {result.rmse_px:.6f} px")
    print(f"Skipped images: {result.diagnostics['skipped_images']}")
    print(f"K =\n{result.intrinsics.K}")
    print(f"Calibration: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
