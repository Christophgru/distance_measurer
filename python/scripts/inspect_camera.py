"""Resolve and report stored-pixel K for an image from a captured session."""

import argparse
import json
import sys
from pathlib import Path

from distance_measuring.camera.intrinsics import resolve_intrinsics
from distance_measuring.dataset import load_session


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session", type=Path)
    parser.add_argument("--image-index", type=int, default=0, help="Recorded image event ID")
    parser.add_argument("--calibration", type=Path)
    parser.add_argument("--output", type=Path, help="Optional JSON report, outside raw data")
    args = parser.parse_args(argv)
    try:
        session = load_session(args.session)
        image = next((image for image in session.images if image.index == args.image_index), None)
        if image is None:
            raise ValueError(f"No successful image with index {args.image_index}")
        intrinsics = resolve_intrinsics(image.metadata, calibration_path=args.calibration)
        if args.output is not None:
            raw_root = Path(__file__).resolve().parents[2] / "data" / "raw"
            if args.output.resolve().is_relative_to(raw_root.resolve()):
                raise ValueError("Camera report must be written outside data/raw")
            if args.output.resolve().is_relative_to(session.path):
                raise ValueError("Camera report must be written outside the raw session directory")
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(intrinsics.to_dict(), indent=2) + "\n", encoding="utf-8"
            )
    except (ValueError, OSError) as error:
        print(f"Cannot determine camera intrinsics: {error}", file=sys.stderr)
        return 1
    print(
        f"Intrinsic source: {intrinsics.source}"
        + (" (approximate)" if intrinsics.approximate else "")
    )
    print(f"Stored image size: {intrinsics.image_size}; camera ID: {intrinsics.camera_id}")
    print(f"K =\n{intrinsics.K}\nDistortion (k1,k2,p1,p2,k3): {intrinsics.distortion}")
    for assumption in intrinsics.assumptions:
        print(f"Assumption: {assumption}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
