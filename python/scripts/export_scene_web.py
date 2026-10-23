"""Export a self-contained WebGL reconstruction viewer, with optional source photos."""

import argparse
import sys
from pathlib import Path

from distance_measuring.visualization.web_export import export_browser_scene


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scene", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--session", type=Path, help="Embed photos and sample point colors")
    parser.add_argument(
        "--calibration", type=Path, help="Same intrinsic calibration used for matching"
    )
    args = parser.parse_args(argv)
    try:
        report = export_browser_scene(
            args.scene, args.output, session_path=args.session, calibration_path=args.calibration
        )
    except (ValueError, OSError) as error:
        print(f"Cannot export browser viewer: {error}", file=sys.stderr)
        return 2
    print(f"Browser scene: {report['points']} points, {report['cameras']} cameras")
    print(f"Open {report['output']} in a browser; no server or internet is needed")
    print("Positions remain in reconstruction units; no metric scale is applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
