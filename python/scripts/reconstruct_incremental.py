"""Reconstruct an ordered capture through P2–P6 and save one P8 report."""

import argparse
import json
import sys
from pathlib import Path

import cv2

from distance_measuring.vision.hybrid import HybridConfig
from distance_measuring.vision.prototype_pipeline import reconstruct_incremental
from distance_measuring.vision.tracking import TrackingConfig


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        help="Fresh directory; default data/processed/<session>/prototype_hybrid_p9 "
        "for hybrid capture, prototype_incremental_p8 for still-only capture",
    )
    parser.add_argument("--image-indices", type=int, nargs="+")
    parser.add_argument("--calibration", type=Path)
    parser.add_argument("--max-image-size", type=int, default=1600)
    parser.add_argument("--max-points", type=int, default=800)
    parser.add_argument("--best-n", type=int, default=200)
    parser.add_argument("--max-gyro-gap-s", type=float, default=0.25)
    parser.add_argument(
        "--tracking-source",
        choices=("auto", "stills", "hybrid"),
        default="auto",
        help="Default: hybrid when a tracking sidecar exists, otherwise original still tracking",
    )
    parser.add_argument("--max-preview-gap-s", type=float, default=0.25)
    parser.add_argument(
        "--cross-stream-agreement-px",
        type=float,
        default=1.5,
        help="Maximum disagreement of both JPEG measurements in working JPEG pixels",
    )
    parser.add_argument(
        "--initialize-motion",
        action="store_true",
        help="Retry poor LK with the bounded SIFT warp initializer for sparse stills",
    )
    parser.add_argument(
        "--use-gyro-prior",
        action="store_true",
        help="Also gate later pose predictions with P3 rotation",
    )
    args = parser.parse_args(argv)
    try:
        cv2.setNumThreads(1)
        report = reconstruct_incremental(
            args.session,
            args.output,
            image_indices=args.image_indices,
            calibration_path=args.calibration,
            max_image_size=args.max_image_size,
            best_n=args.best_n,
            max_gyro_gap_s=args.max_gyro_gap_s,
            tracking_config=TrackingConfig(
                max_points=args.max_points, initialize_motion=args.initialize_motion
            ),
            use_gyro_prior=args.use_gyro_prior,
            tracking_source=args.tracking_source,
            hybrid_config=HybridConfig(
                max_preview_gap_s=args.max_preview_gap_s,
                cross_stream_agreement_px=args.cross_stream_agreement_px,
            ),
        )
    except (OSError, ValueError, KeyError, TypeError, cv2.error) as error:
        print(f"Cannot reconstruct capture: {error}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "status": report["status"],
                **report["diagnostics"]["quality"],
                "stage_runtime_s": report["stage_runtime_s"],
                "component_runtime_s": report["diagnostics"]["component_runtime_s"],
            },
            indent=2,
        )
    )
    if report["failure_reason"]:
        print(report["failure_reason"])
    name = (
        "prototype_hybrid_p9"
        if report["settings"]["tracking_source"] == "hybrid"
        else "prototype_incremental_p8"
    )
    output = args.output or Path("data/processed") / args.session.name / name
    print(f"Report: {output / 'REPORT.md'}")
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
