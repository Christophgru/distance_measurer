"""Create blank scene-bound endpoint annotations and an editable photo review page."""

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

from distance_measuring.measurement import create_annotations
from distance_measuring.scene import Scene
from distance_measuring.scene.storage import decode_document


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scene", type=Path)
    parser.add_argument("--session", type=Path, required=True)
    parser.add_argument(
        "--output", type=Path, required=True, help="Fresh annotation/report directory"
    )
    parser.add_argument("--review-html", type=Path, help="Default: OUTPUT/index.html")
    parser.add_argument("--reference-length-m", type=float, required=True)
    parser.add_argument("--view-group", type=int, nargs="+", action="append")
    parser.add_argument("--localization-radius-px", type=float, default=1.0)
    parser.add_argument("--calibration", type=Path)
    args = parser.parse_args(argv)
    try:
        source, output, session = (
            args.scene.resolve(),
            args.output.resolve(),
            args.session.resolve(),
        )
        raw = (Path(__file__).resolve().parents[2] / "data/raw").resolve()
        if any(output.is_relative_to(p) for p in (raw, source.parent, session)):
            raise ValueError("Measurement setup must be outside raw and source scene directories")
        if output.exists():
            raise ValueError(f"Output already exists: {output}; choose a fresh directory")
        html = args.review_html.resolve() if args.review_html else output / "index.html"
        if html.exists():
            raise ValueError("Review HTML already exists; choose a fresh path")
        if html.suffix.lower() != ".html" or any(
            html.is_relative_to(p) for p in (raw, source.parent, session)
        ):
            raise ValueError(
                "Review HTML must be an .html file outside raw/source scene directories"
            )
        content = source.read_bytes()
        scene = Scene.from_dict(decode_document(content, source.suffix))
        annotations = create_annotations(
            scene,
            hashlib.sha256(content).hexdigest(),
            args.reference_length_m,
            view_groups=args.view_group,
            localization_radius_px=args.localization_radius_px,
        )
        output.mkdir(parents=True, exist_ok=False)
        annotation = output / "annotations.json"
        annotation.write_text(
            json.dumps(annotations, indent=2, allow_nan=False) + "\n", encoding="utf-8"
        )
        command = [
            sys.executable,
            str(Path(__file__).with_name("measure_scene.py")),
            str(source),
            str(annotation),
            "--session",
            str(session),
            "--output",
            str(output / "measurements.json"),
            "--review-html",
            str(html),
        ]
        if args.calibration:
            command += ["--calibration", str(args.calibration.resolve())]
        result = subprocess.run(command, check=False)
        print(f"Annotations: {annotation}")
        if result.returncode:
            print(
                "Photo review export failed; the annotation template is retained", file=sys.stderr
            )
            return 2
        print(f"Open {html}; select each endpoint and mark the same physical location in each view")
        print("Download edited annotations, then rerun measure_scene.py to evaluate them")
        return 0
    except (ValueError, OSError, KeyError, TypeError) as error:
        print(f"Cannot initialize measurements: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
