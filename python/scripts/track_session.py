"""Select distributed points, track ordered capture images, and export P2 diagnostics."""

import argparse
import json
import sys
from pathlib import Path

import cv2

from distance_measuring.vision.tracking import TrackingConfig
from distance_measuring.vision.tracking_pipeline import track_session


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session", type=Path)
    parser.add_argument("--output", type=Path, required=True, help="New directory outside raw data")
    parser.add_argument("--image-indices", type=int, nargs="+", help="Default: all ordered images")
    parser.add_argument("--calibration", type=Path)
    parser.add_argument("--max-image-size", type=int, default=1600)
    parser.add_argument("--max-points", type=int, default=800)
    parser.add_argument("--grid-rows", type=int, default=6)
    parser.add_argument("--grid-cols", type=int, default=8)
    parser.add_argument("--min-distance-px", type=float, default=12)
    parser.add_argument("--quality-level", type=float, default=0.01)
    parser.add_argument("--lk-window-px", type=int, default=31)
    parser.add_argument("--lk-max-level", type=int, default=4)
    parser.add_argument("--fb-threshold-px", type=float, default=1.5)
    parser.add_argument("--replenish-ratio", type=float, default=0.75)
    parser.add_argument(
        "--initialize-motion",
        action="store_true",
        help="Retry poor LK with bounded SIFT image-warp initialization",
    )
    parser.add_argument("--motion-max-features", type=int, default=600)
    parser.add_argument("--motion-max-image-size", type=int, default=640)
    parser.add_argument("--motion-retry-ratio", type=float, default=0.25)
    args = parser.parse_args(argv)
    try:
        cv2.setNumThreads(1)
        config = TrackingConfig(
            **{
                name: getattr(args, name)
                for name in (
                    "max_points",
                    "grid_rows",
                    "grid_cols",
                    "min_distance_px",
                    "quality_level",
                    "lk_window_px",
                    "lk_max_level",
                    "fb_threshold_px",
                    "replenish_ratio",
                    "initialize_motion",
                    "motion_max_features",
                    "motion_max_image_size",
                    "motion_retry_ratio",
                )
            }
        )
        report = track_session(
            args.session,
            args.output,
            image_indices=args.image_indices,
            calibration_path=args.calibration,
            max_image_size=args.max_image_size,
            config=config,
        )
    except (ValueError, OSError, cv2.error) as error:
        print(f"Cannot track session: {error}", file=sys.stderr)
        return 2
    print(json.dumps(report["summary"], indent=2))
    print(f"Diagnostics: {args.output / 'tracking.json'}")
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
