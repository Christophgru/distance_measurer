"""Register later images with temporal PnP, local patches and epipolar search."""

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

import cv2

from distance_measuring.optimization.local_bundle import DEFAULT_LOCAL_BUNDLE, LocalBundleConfig
from distance_measuring.vision.guided import DEFAULT_GUIDED, GuidedConfig
from distance_measuring.vision.incremental_pipeline import extend_tracked_scene
from distance_measuring.vision.landmark_confidence import DEFAULT_CONFIDENCE, ConfidenceConfig
from distance_measuring.vision.pnp import DEFAULT_PNP, PnPConfig
from distance_measuring.vision.refinement import DEFAULT_REFINEMENT, RefinementConfig


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session", type=Path)
    parser.add_argument("tracking_directory", type=Path)
    parser.add_argument("bootstrap_directory", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--parallax-directory", type=Path, help="Optional consistent gyro rotation prior"
    )
    parser.add_argument("--seed-growth", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument(
        "--refine",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="P6: local BA, bounded retries and confidence filtering",
    )
    for name, default in asdict(DEFAULT_GUIDED).items():
        options = (
            {"action": argparse.BooleanOptionalAction}
            if type(default) is bool
            else {"type": type(default)}
        )
        parser.add_argument("--" + name.replace("_", "-"), default=default, **options)
    for name, default in asdict(DEFAULT_PNP).items():
        parser.add_argument("--pnp-" + name.replace("_", "-"), type=type(default), default=default)
    for prefix, defaults in (
        ("ba", DEFAULT_LOCAL_BUNDLE),
        ("confidence", DEFAULT_CONFIDENCE),
        ("refinement", DEFAULT_REFINEMENT),
    ):
        for name, default in asdict(defaults).items():
            parser.add_argument(
                f"--{prefix}-" + name.replace("_", "-"), type=type(default), default=default
            )
    args = parser.parse_args(argv)
    try:
        cv2.setNumThreads(1)
        config = GuidedConfig(**{name: getattr(args, name) for name in asdict(DEFAULT_GUIDED)})
        pnp_config = PnPConfig(
            **{name: getattr(args, "pnp_" + name) for name in asdict(DEFAULT_PNP)}
        )
        bundle = LocalBundleConfig(
            **{name: getattr(args, "ba_" + name) for name in asdict(DEFAULT_LOCAL_BUNDLE)}
        )
        confidence = ConfidenceConfig(
            **{name: getattr(args, "confidence_" + name) for name in asdict(DEFAULT_CONFIDENCE)}
        )
        refinement = RefinementConfig(
            **{name: getattr(args, "refinement_" + name) for name in asdict(DEFAULT_REFINEMENT)}
        )
        report = extend_tracked_scene(
            args.session,
            args.tracking_directory,
            args.bootstrap_directory,
            args.output,
            config=config,
            pnp_config=pnp_config,
            parallax_directory=args.parallax_directory,
            seed_growth=args.seed_growth,
            refinement_config=refinement if args.refine else None,
            local_bundle_config=bundle,
            confidence_config=confidence,
        )
    except (OSError, ValueError, KeyError, TypeError, cv2.error) as error:
        print(f"Cannot extend tracked scene: {error}", file=sys.stderr)
        return 2
    print(json.dumps(report["summary"], indent=2))
    if report["failure_reason"]:
        print(report["failure_reason"])
    print(f"Diagnostics: {args.output / 'incremental.json'}")
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
