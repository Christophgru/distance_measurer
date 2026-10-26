"""Estimate relative gyro rotation, inspect pixel parallax and rank P2 tracks."""

import argparse
import sys
from pathlib import Path
from zipfile import BadZipFile

import cv2

from distance_measuring.vision.parallax_pipeline import analyze_track_parallax
from distance_measuring.vision.track_ranking import RankingConfig


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session", type=Path)
    parser.add_argument("tracking_directory", type=Path)
    parser.add_argument(
        "--output", type=Path, required=True, help="New directory outside raw/P2 data"
    )
    parser.add_argument("--best-n", type=int, default=50)
    parser.add_argument("--grid-rows", type=int, default=6)
    parser.add_argument("--grid-cols", type=int, default=8)
    parser.add_argument("--min-parallax-px", type=float, default=2)
    parser.add_argument("--max-fb-error-px", type=float, default=1.5)
    parser.add_argument("--max-gyro-gap-s", type=float, default=0.25)
    args = parser.parse_args(argv)
    try:
        cv2.setNumThreads(1)
        report = analyze_track_parallax(
            args.session,
            args.tracking_directory,
            args.output,
            best_n=args.best_n,
            config=RankingConfig(
                grid_rows=args.grid_rows,
                grid_cols=args.grid_cols,
                min_parallax_px=args.min_parallax_px,
                max_fb_error_px=args.max_fb_error_px,
            ),
            max_gyro_gap_s=args.max_gyro_gap_s,
        )
    except (ValueError, OSError, KeyError, TypeError, BadZipFile, cv2.error) as error:
        print(f"Cannot analyze track parallax: {error}", file=sys.stderr)
        return 2
    print(f"Diagnostics: {args.output / 'parallax.json'}")
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
