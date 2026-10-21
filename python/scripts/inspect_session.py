"""Inspect an exported Android capture without changing its files."""

import argparse
import json
import sys

from distance_measuring.dataset import DatasetFormatError, load_session, validate_session
from distance_measuring.dataset.inspection import (
    format_summary,
    plot_acceleration,
    summarize_session,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "session", help="Directory containing session.json, sensors.csv, and images/"
    )
    parser.add_argument("--json", action="store_true", help="Print a machine-readable JSON report")
    parser.add_argument("--plot", metavar="PATH", help="Save an acceleration plot outside raw data")
    parser.add_argument(
        "--max-sensor-gap-s",
        type=float,
        default=0.25,
        help="Maximum interval bracketing each image (default: 0.25 seconds)",
    )
    args = parser.parse_args(argv)
    try:
        session = load_session(args.session, validate=False)
        report = validate_session(session, max_sensor_gap_s=args.max_sensor_gap_s)
        summary = summarize_session(session, report)
        if args.plot:
            summary["plot"] = str(plot_acceleration(session, args.plot))
    except (DatasetFormatError, ValueError, OSError) as error:
        if args.json:
            print(json.dumps({"valid": False, "format_error": str(error)}, indent=2))
        else:
            print(f"Cannot inspect session: {error}", file=sys.stderr)
        return 2
    print(json.dumps(summary, indent=2, allow_nan=False) if args.json else format_summary(summary))
    if args.plot and not args.json:
        print(f"Plot: {summary['plot']}")
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
