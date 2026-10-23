"""Build conflict-checked tracks, triangulate, and persist a sparse scene from M4/M5."""

import argparse
import sys
from pathlib import Path
from zipfile import BadZipFile

from distance_measuring.scene.reconstruction import reconstruct_scene


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("matches_directory", type=Path)
    parser.add_argument("poses_directory", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-reprojection-error-px", type=float, default=3.0)
    parser.add_argument("--min-angle-degrees", type=float, default=1.0)
    parser.add_argument("--solver", choices=("svd", "eigh"), default="svd")
    args = parser.parse_args(argv)
    try:
        report = reconstruct_scene(
            args.matches_directory,
            args.poses_directory,
            args.output,
            max_reprojection_error_px=args.max_reprojection_error_px,
            min_angle_degrees=args.min_angle_degrees,
            solver=args.solver,
        )
    except (ValueError, OSError, BadZipFile) as error:
        print(f"Cannot reconstruct scene: {error}", file=sys.stderr)
        return 2
    summary = report["summary"]
    print(
        f"Cameras: {summary['camera_count']}; tracks: {summary['track_count']}; "
        f"points: {summary['point_count']}"
    )
    print(
        f"Track conflicts rejected: {summary['conflict_count']}; "
        f"rejected tracks: {summary['rejected_tracks']}"
    )
    print(f"Reprojection RMSE: {summary['reprojection_rmse_px']} working pixels")
    for warning in summary["warnings"]:
        print(f"Warning: {warning}")
    print(f"Scene: {report['output_scene']}")
    return 0 if summary["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
