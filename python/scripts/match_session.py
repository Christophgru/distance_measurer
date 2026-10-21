"""Extract SIFT, verify three image pairs, and save rectified match diagnostics."""

import argparse
import sys
from pathlib import Path

import cv2

from distance_measuring.vision.pipeline import match_session


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--image-indices", type=int, nargs=3, default=(0, 1, 2), metavar=("A", "B", "C")
    )
    parser.add_argument("--calibration", type=Path)
    parser.add_argument(
        "--max-image-size", type=int, default=1600, help="Working image longest edge"
    )
    parser.add_argument("--max-features", type=int, default=6000)
    parser.add_argument("--ratio", type=float, default=0.75)
    parser.add_argument("--no-mutual", action="store_true")
    parser.add_argument(
        "--threshold-px", type=float, default=1.5, help="Working-pixel Sampson threshold"
    )
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args(argv)
    try:
        report = match_session(
            args.session,
            args.output,
            image_indices=tuple(args.image_indices),
            calibration_path=args.calibration,
            max_image_size=args.max_image_size,
            max_features=args.max_features,
            ratio=args.ratio,
            mutual=not args.no_mutual,
            threshold_px=args.threshold_px,
            seed=args.seed,
        )
    except (ValueError, OSError, cv2.error) as error:
        print(f"Cannot match session: {error}", file=sys.stderr)
        return 2
    for image in report["images"]:
        camera = image["stored_intrinsics"]
        print(
            f"Image {image['index']}: {image['feature_count']} features; "
            f"intrinsic source: {camera['source']}"
            + (" (approximate)" if camera["approximate"] else "")
        )
    for pair in report["pairs"]:
        i, j = pair["indices"]
        print(
            f"Pair {i}-{j}: raw={pair['raw_count']}, ratio={pair['ratio_count']}, "
            f"retained={pair['retained_count']}, inliers={pair['inlier_count']}, "
            f"outliers={pair['outlier_count']}; rectified={pair['rectified_image'] is not None}"
        )
        for key in ("failure", "rectification_failure"):
            if pair[key]:
                print(f"  {pair[key]}")
    print(f"Diagnostics: {args.output / 'matches.json'}")
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
