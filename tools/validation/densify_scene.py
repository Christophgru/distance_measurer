"""Densify a saved scene from real stereo matches while freezing K, R and C."""

import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import cv2
import numpy as np

from distance_measuring.camera import resolve_intrinsics
from distance_measuring.dataset import load_session
from distance_measuring.scene import load_scene, save_scene
from distance_measuring.vision.densification import densify_scene
from distance_measuring.vision.pipeline import working_camera


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session", type=Path)
    parser.add_argument("--scene", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--regions", type=Path, required=True)
    parser.add_argument("--calibration", type=Path)
    parser.add_argument("--target-points", type=int, default=20000)
    parser.add_argument("--max-image-size", type=int, default=1600)
    args = parser.parse_args(argv)
    try:
        output = args.output.resolve()
        raw = Path(__file__).resolve().parents[2] / "data/raw"
        if any(
            output.is_relative_to(p)
            for p in [raw, args.session.resolve(), args.scene.resolve().parent]
        ):
            raise ValueError("Densification output must be outside raw and input scene directories")
        if args.max_image_size < 128:
            raise ValueError("Stereo image size must be at least 128")
        session, scene = load_session(args.session), load_scene(args.scene)
        if session.metadata.session_id != scene.metadata.get("session_id"):
            raise ValueError("Scene and images describe different recordings")
        regions = json.loads(args.regions.read_text())
        spec = importlib.util.spec_from_file_location(
            "audit_masks", Path(__file__).with_name("audit_correspondences.py")
        )
        audit = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(audit)
        paths = [
            args.scene,
            args.regions,
            args.session / "session.json",
            args.session / "events.jsonl",
        ]
        if args.calibration:
            paths.append(args.calibration)
        events = {e.index: e for e in session.images}
        paths.extend(session.image_path(events[c.image_id]) for c in scene.cameras)
        hashes = {str(p.resolve()): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
        images, masks = {}, {}
        for c in scene.cameras:
            e = events[c.image_id]
            if c.intrinsics.source == "calibrated" and args.calibration is None:
                raise ValueError("Calibrated scenes require their original --calibration file")
            original = resolve_intrinsics(e.metadata, calibration_path=args.calibration)
            work, _ = working_camera(original, c.intrinsics.image_size)
            if not np.allclose(work.K, c.intrinsics.K, atol=1e-6, rtol=1e-7):
                raise ValueError("Camera K differs from photo calibration")
            image = cv2.imread(
                str(session.image_path(e)), cv2.IMREAD_GRAYSCALE | cv2.IMREAD_IGNORE_ORIENTATION
            )
            if image is None:
                raise ValueError(f"Cannot read photo {c.image_id}")
            image = cv2.resize(image, work.image_size, interpolation=cv2.INTER_AREA)
            images[c.image_id] = work.undistort_image(image) if any(work.distortion) else image
            mask = audit.search_mask(regions, c.image_id, work.image_size)
            masks[c.image_id] = work.undistort_image(mask) if any(work.distortion) else mask
        dense, report = densify_scene(
            scene,
            images,
            masks,
            target_points=args.target_points,
            max_image_size=args.max_image_size,
        )
        report["input_sha256"] = hashes
        if any(hashlib.sha256(Path(p).read_bytes()).hexdigest() != h for p, h in hashes.items()):
            raise ValueError("A densification input changed")
        output.mkdir(parents=True, exist_ok=True)
        save_scene(output / "scene.json", dense)
        (output / "densification.json").write_text(
            json.dumps(report, indent=2, allow_nan=False) + "\n"
        )
        print("Total real reconstructed points:", len(dense.points), flush=True)
        return 0
    except (ValueError, OSError) as error:
        print(f"Cannot densify scene: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
