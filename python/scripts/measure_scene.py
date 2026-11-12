"""Measure annotated endpoints and optionally export an offline photo review page."""

import argparse
import hashlib
import json
import sys
from pathlib import Path

import cv2

from distance_measuring.measurement import evaluate_measurements
from distance_measuring.scene import Scene
from distance_measuring.scene.storage import decode_document
from distance_measuring.visualization.measurement_review import review_document


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scene", type=Path)
    parser.add_argument("annotations", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--review-html", type=Path)
    parser.add_argument("--session", type=Path)
    parser.add_argument("--calibration", type=Path)
    parser.add_argument("--sensitivity-trials", type=int, default=200)
    args = parser.parse_args(argv)
    try:
        source, annotation = args.scene.resolve(), args.annotations.resolve()
        inputs = [source, annotation]
        calibration = args.calibration.resolve(strict=True) if args.calibration else None
        if calibration:
            inputs.append(calibration)
        raw = Path(__file__).resolve().parents[2] / "data/raw"
        targets = [args.output.resolve()]
        if args.review_html:
            if not args.session or args.review_html.suffix.lower() != ".html":
                raise ValueError("--review-html needs --session and an .html filename")
            targets.append(args.review_html.resolve())
        for index, target in enumerate(targets):
            protected = inputs + targets[:index]
            if (
                target.is_relative_to(raw)
                or target.is_relative_to(source.parent)
                or (args.session and target.is_relative_to(args.session.resolve()))
                or any(
                    target == p or (target.exists() and p.exists() and target.samefile(p))
                    for p in protected
                )
            ):
                raise ValueError(
                    "Outputs must be outside raw/source scene directories and must not "
                    "overwrite input annotations, calibration or each other"
                )
        content = annotation.read_bytes()
        data = json.loads(content)
        if not isinstance(data, dict):
            raise ValueError("Annotations must be a JSON object")
        scene_content = source.read_bytes()
        source_hash = hashlib.sha256(scene_content).hexdigest()
        if data.get("scene_sha256") != source_hash:
            raise ValueError("Annotations are not bound to the supplied scene SHA-256")
        scene = Scene.from_dict(decode_document(scene_content, source.suffix))
        report = evaluate_measurements(scene, data, sensitivity_trials=args.sensitivity_trials)
        hashes = {str(source): source_hash, str(annotation): hashlib.sha256(content).hexdigest()}
        if calibration:
            hashes[str(calibration)] = hashlib.sha256(calibration.read_bytes()).hexdigest()
        html = None
        if args.review_html:
            html, photo_hashes = review_document(
                scene,
                data,
                args.session,
                source_hash,
                report=report,
                calibration_path=calibration,
            )
            hashes.update(photo_hashes)
        report["input_sha256"] = hashes
        if any(target.exists() and target.samefile(p) for target in targets for p in hashes):
            raise ValueError("Outputs must be outside input files, including file aliases")
        # Detect input replacement during evaluation/export before publishing.
        if any(hashlib.sha256(Path(p).read_bytes()).hexdigest() != h for p, h in hashes.items()):
            raise ValueError("Measurement input changed during evaluation")
        for p in targets:
            p.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8"
        )
        if html is not None:
            args.review_html.write_text(html, encoding="utf-8")
        print(f"Measurement report: {args.output}")
        print(
            "Prototype checks: "
            + (
                "pass"
                if report["prototype_checks_passed"]
                else ", ".join(report["blocking_reasons"])
            )
        )
        print("Exploratory lengths only; scene poses and scale are unchanged")
        return 0
    except (ValueError, OSError, KeyError, TypeError, cv2.error) as error:
        print(f"Cannot measure scene: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
