"""Fit and validate cameras using image matches only, before any metric scaling."""

import argparse
import hashlib
import json
import sys
from pathlib import Path

from distance_measuring.optimization.bundle import bundle_adjust
from distance_measuring.scene import save_scene
from distance_measuring.vision.pairwise_scene import rebuild_pairwise_scene
from distance_measuring.vision.validation import (
    camera_fit_quality,
    evaluate_camera_holdout,
    prune_visual_outliers,
    split_camera_matches,
)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("matches", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    output = args.output.resolve()
    failure_context = None
    try:
        raw = Path(__file__).resolve().parents[2] / "data/raw"
        if output.is_relative_to(raw) or output.is_relative_to(args.matches.resolve().parent):
            raise ValueError("Camera outputs must be outside raw and input matching directories")
        audit = json.loads(args.matches.read_text())
        hashes = audit["input_sha256"].copy()
        hashes[str(args.matches.resolve())] = hashlib.sha256(args.matches.read_bytes()).hexdigest()
        if any(hashlib.sha256(Path(p).read_bytes()).hexdigest() != h for p, h in hashes.items()):
            raise ValueError("Matching input changed; recompute image matches")
        failure_context = {
            "schema_version": 1,
            "session_id": audit["session_id"],
            "image_ids": audit["image_ids"],
            "input_sha256": hashes,
            "camera_fit_sources": "images_only_no_ruler_no_imu",
            "metric_scale_applied": False,
            "metric_scale_accepted": False,
        }
        training, records = split_camera_matches(audit)
        print("Reserved independent feature tracks:", len(records), flush=True)
        result = rebuild_pairwise_scene(
            training, refine_incremental=True, seed_strategy="conditioned"
        )
        print("All visual cameras registered:", result.report["registrations"], flush=True)
        scene = result.scene
        rounds, removed = [], []
        original_count = sum(len(t.observations) for t in scene.tracks if t.point_id is not None)
        for _ in range(3):
            refined = bundle_adjust(scene, max_nfev=500)
            scene = refined.scene
            rounds.append(refined.report)
            pruned, discarded = prune_visual_outliers(scene)
            removed.extend(discarded)
            if not discarded:
                break
            scene = pruned
        holdout = evaluate_camera_holdout(scene, records)
        quality = camera_fit_quality(
            scene,
            holdout,
            converged=rounds[-1]["converged"],
            removed_fraction=len(removed) / original_count,
        )
        scene.metadata["quality_status"] = (
            "visual_validation_passed" if quality["passed"] else "visual_validation_failed"
        )
        scene.metadata["camera_fit_sources"] = "images_only_no_ruler_no_imu"
        report = {
            "schema_version": 1,
            "session_id": audit["session_id"],
            "image_ids": audit["image_ids"],
            "input_sha256": hashes,
            "camera_initialization": result.report,
            "bundle_adjustments": rounds,
            "removed_training_observations": removed,
            "held_out_validation": holdout,
            "camera_quality": quality,
            "metric_scale_applied": False,
            "metric_scale_accepted": False,
        }
        if any(hashlib.sha256(Path(p).read_bytes()).hexdigest() != h for p, h in hashes.items()):
            raise ValueError("A camera input changed during reconstruction")
        output.mkdir(parents=True, exist_ok=True)
        save_scene(output / "scene.json", scene)
        (output / "camera_validation.json").write_text(
            json.dumps(report, indent=2, allow_nan=False) + "\n"
        )
        print("Visual camera quality:", quality, flush=True)
        return 0 if quality["passed"] else 1
    except (ValueError, OSError) as error:
        if failure_context is not None:
            failure_context.update(
                camera_quality={"passed": False},
                failure_stage="visual_camera_reconstruction",
                failure_reason=str(error),
                ruler_reference_status="aborted_before_ruler_triangulation",
                acceleration_scale_status="not_run_camera_prerequisite_failed",
                scene_accepted=False,
            )
            output.mkdir(parents=True, exist_ok=True)
            (output / "camera_validation.json").write_text(
                json.dumps(failure_context, indent=2, allow_nan=False) + "\n"
            )
        print(f"Cannot establish visual cameras: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
