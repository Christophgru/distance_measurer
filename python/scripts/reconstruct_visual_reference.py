"""Run the visual-only reference reconstruction; metric scale is deliberately absent."""

import argparse
import sys
from pathlib import Path

from distance_measuring.vision.visual_reference import (
    prepare_visual_images,
    recover_dense_reference,
    recover_visual_reference,
)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session", type=Path)
    parser.add_argument("--regions", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--gpu-index", default="0", help="CUDA ordinal, which can differ from nvidia-smi"
    )
    parser.add_argument("--stage", choices=["sparse", "dense", "all"], default="all")
    parser.add_argument("--max-image-size", type=int, default=4096)
    args = parser.parse_args(argv)
    try:
        raw = Path(__file__).resolve().parents[2] / "data/raw"
        if args.output.resolve().is_relative_to(raw):
            raise ValueError("Visual outputs must be outside raw")
        manifest = prepare_visual_images(args.session, args.output, args.regions)
        if args.stage in ["sparse", "all"]:
            recover_visual_reference(args.output, manifest, gpu_index=args.gpu_index)
        if args.stage in ["dense", "all"]:
            recover_dense_reference(
                args.output, manifest, gpu_index=args.gpu_index, max_image_size=args.max_image_size
            )
        return 0
    except (ValueError, OSError, RuntimeError, ImportError) as error:
        print(f"Cannot recover visual reference: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
