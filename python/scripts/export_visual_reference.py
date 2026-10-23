"""Export a visual-only reference using BSON point-list files."""

import argparse
import json
import sys
from pathlib import Path

from distance_measuring.visualization.reference_export import export_reference_scene


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reference", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--target-points", type=int, default=2_000_000)
    parser.add_argument("--previous-scene", type=Path)
    args = parser.parse_args()
    try:
        print(
            json.dumps(
                export_reference_scene(
                    args.reference,
                    args.output,
                    target_points=args.target_points,
                    previous_scene=args.previous_scene,
                ),
                indent=2,
            )
        )
        return 0
    except (ValueError, OSError, RuntimeError, ImportError) as error:
        print(f"Cannot export visual reference: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
