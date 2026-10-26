"""Select the earliest usable P3 pair, recover visual pose and triangulate landmarks."""

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from zipfile import BadZipFile

import cv2

from distance_measuring.vision.bootstrap import DEFAULT_BOOTSTRAP, BootstrapConfig
from distance_measuring.vision.bootstrap_pipeline import bootstrap_session


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("parallax_directory", type=Path)
    parser.add_argument(
        "--output", type=Path, required=True, help="New directory outside raw/P3 data"
    )
    # Expose every scalar threshold without a separate competing set of defaults.
    for name, default in asdict(DEFAULT_BOOTSTRAP).items():
        parser.add_argument("--" + name.replace("_", "-"), type=type(default), default=default)
    args = parser.parse_args(argv)
    try:
        cv2.setNumThreads(1)
        config = BootstrapConfig(
            **{name: getattr(args, name) for name in asdict(DEFAULT_BOOTSTRAP)}
        )
        report = bootstrap_session(args.parallax_directory, args.output, config=config)
    except (ValueError, OSError, KeyError, TypeError, BadZipFile, cv2.error) as error:
        print(f"Cannot bootstrap tracks: {error}", file=sys.stderr)
        return 2
    print(json.dumps(report["summary"], indent=2))
    print(
        f"Selected pair: {report['bootstrap_pair']}; diagnostics: {args.output / 'bootstrap.json'}"
    )
    if not report["success"]:
        print(report["failure_reason"])
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
