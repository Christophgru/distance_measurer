"""Run the fixed P0.2 three-image baseline using the existing M4–M7 stages."""

import argparse
import hashlib
import json
import platform
import sys
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

import cv2
import numpy as np
import scipy

from distance_measuring.dataset import load_session, validate_session
from distance_measuring.optimization.pipeline import refine_scene
from distance_measuring.scene.reconstruction import reconstruct_scene
from distance_measuring.vision.pipeline import match_session
from distance_measuring.vision.pose_pipeline import estimate_poses

ROOT = Path(__file__).resolve().parents[2]
SESSION = ROOT / "data/raw/measurement01"
IMAGE_INDICES = (0, 1, 2)


def run_baseline(output: Path) -> dict:
    # Fresh outputs keep earlier measurements available and prevent stale reports.
    output = output.resolve()
    if output.is_relative_to(ROOT / "data/raw") or output.is_relative_to(SESSION.resolve()):
        raise ValueError("Baseline output must be outside data/raw")
    output.mkdir(parents=True, exist_ok=False)
    cv2.setNumThreads(1)
    timings = {}

    def timed(name, function, *args, **kwargs):
        start = perf_counter()
        result = function(*args, **kwargs)
        timings[name] = perf_counter() - start
        print(f"{name}: {timings[name]:.3f} s", file=sys.stderr, flush=True)
        return result

    start = perf_counter()
    session = timed("session_loading", load_session, SESSION, validate=False)
    validation = timed("validation", validate_session, session)
    validation.raise_for_errors()
    if not session.sensor_streams.get("gyroscope"):
        raise ValueError("Baseline requires recorded gyroscope samples")
    paths = [SESSION / name for name in ("session.json", "events.jsonl", "sensors.csv")]
    paths.extend(session.image_path(image) for image in session.images)
    hashes = {
        str(path.relative_to(SESSION)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in paths
    }
    matches = timed(
        "matching",
        match_session,
        SESSION,
        output / "m4",
        image_indices=IMAGE_INDICES,
        max_image_size=1600,
        max_features=6000,
        ratio=0.75,
        mutual=True,
        threshold_px=1.5,
        seed=0,
    )
    if not matches["success"]:
        raise ValueError(f"Matching failed; inspect {output / 'm4/matches.json'}")
    poses = timed(
        "pose",
        estimate_poses,
        output / "m4",
        output / "m5",
        threshold_px=1.5,
        min_parallax_degrees=0.25,
        seed=0,
    )
    if not poses["success"]:
        raise ValueError(f"Pose estimation failed; inspect {output / 'm5/poses.json'}")
    reconstruction = timed(
        "triangulation",
        reconstruct_scene,
        output / "m4",
        output / "m5",
        output / "m6",
        max_reprojection_error_px=3.0,
        min_angle_degrees=1.0,
        solver="svd",
    )
    if not reconstruction["summary"]["success"]:
        raise ValueError(f"No sparse points; inspect {output / 'm6/triangulation.json'}")
    refined = timed(
        "bundle_adjustment",
        refine_scene,
        reconstruction["output_scene"],
        output / "m7",
        loss="soft_l1",
        f_scale=1.0,
        max_nfev=300,
    )
    timings["total"] = perf_counter() - start
    if hashes != {
        str(path.relative_to(SESSION)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in paths
    }:
        raise ValueError("Raw baseline inputs changed during reconstruction")
    report = {
        "schema_version": 1,
        "recorded_at_utc": datetime.now(UTC).isoformat(),
        "success": bool(refined["accepted"] and refined["converged"]),
        "session_path": str(SESSION.relative_to(ROOT)),
        "session_id": session.metadata.session_id,
        "session_image_count": len(session.images),
        "selected_image_indices": list(IMAGE_INDICES),
        "image_count": len(IMAGE_INDICES),
        "sensor_sample_counts": {k: len(v) for k, v in session.sensor_streams.items()},
        "input_sha256": hashes,
        "software": {
            "python": platform.python_version(),
            "opencv": cv2.__version__,
            "numpy": np.__version__,
            "scipy": scipy.__version__,
            "platform": platform.platform(),
            "opencv_threads": cv2.getNumThreads(),
        },
        "runtime_s": timings,
        "pairs": [
            {
                "image_indices": m["indices"],
                "fundamental_inliers": m["inlier_count"],
                "essential_inliers": p["essential_inlier_count"],
                "pose_inliers": p["pose_inlier_count"],
            }
            for m, p in zip(matches["pairs"], poses["pairs"], strict=True)
        ],
        "verified_match_count": sum(p["inlier_count"] for p in matches["pairs"]),
        "pose_inlier_match_count": sum(p["pose_inlier_count"] for p in poses["pairs"]),
        "reconstruction": reconstruction["summary"],
        "bundle_adjustment": refined,
        "validation_warnings": [w.message for w in validation.warnings],
    }
    (output / "baseline.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="New processed output directory")
    args = parser.parse_args(argv)
    try:
        report = run_baseline(args.output)
    except (ValueError, OSError, cv2.error) as error:
        print(f"Cannot run baseline: {error}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, allow_nan=False))
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
