"""Label P3 frames as skip/candidate keyframe using measured track support."""

import argparse
import sys
from dataclasses import asdict
from pathlib import Path
from zipfile import BadZipFile

from distance_measuring.vision.keyframe_pipeline import score_keyframes
from distance_measuring.vision.keyframes import DEFAULT_KEYFRAME, KeyframeConfig


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("parallax_directory", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    for name, default in asdict(DEFAULT_KEYFRAME).items():
        parser.add_argument("--" + name.replace("_", "-"), type=type(default), default=default)
    args = parser.parse_args(argv)
    try:
        config = KeyframeConfig(**{name: getattr(args, name) for name in asdict(DEFAULT_KEYFRAME)})
        report = score_keyframes(args.parallax_directory, args.output, config=config)
    except (ValueError, OSError, KeyError, TypeError, BadZipFile) as error:
        print(f"Cannot score keyframes: {error}", file=sys.stderr)
        return 2
    print(
        f"Candidate frames: {report['candidate_count']}; "
        f"diagnostics: {args.output / 'keyframes.json'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
