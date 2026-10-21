"""Estimate essential matrices and relative camera frames from M4 exports."""

import argparse
import sys
from pathlib import Path
from zipfile import BadZipFile

import cv2

from distance_measuring.vision.pose_pipeline import estimate_poses


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("matches_directory", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--threshold-px", type=float, default=1.5)
    parser.add_argument("--min-parallax-degrees", type=float, default=0.25)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args(argv)
    try:
        report = estimate_poses(
            args.matches_directory,
            args.output,
            threshold_px=args.threshold_px,
            min_parallax_degrees=args.min_parallax_degrees,
            seed=args.seed,
        )
    except (ValueError, OSError, BadZipFile, cv2.error) as error:
        print(f"Cannot estimate poses: {error}", file=sys.stderr)
        return 2
    for image in report["images"]:
        camera = image["working_intrinsics"]
        print(
            f"Image {image['index']}: intrinsic source: {camera['source']}"
            + (" (approximate)" if camera["approximate"] else "")
        )
    for pair in report["pairs"]:
        print(
            f"Pair {pair['indices']}: M4={pair['m4_inlier_count']}, "
            f"E={pair['essential_inlier_count']}, pose={pair['pose_inlier_count']}; "
            f"median parallax={pair['median_parallax_degrees']} degrees"
        )
        if pair["failure"]:
            print(f"  {pair['failure']}")
    print("Scale: independent unit baselines; relative baseline scales unresolved")
    for warning in report["warnings"]:
        print(f"Warning: {warning}")
    print(f"Camera frames: {len(report['cameras'])}/3; report: {args.output / 'poses.json'}")
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
