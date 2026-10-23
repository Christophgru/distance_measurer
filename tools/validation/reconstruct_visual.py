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
    subset_camera_holdout,
    subset_camera_matches,
    supported_camera_subsets,
)


def fit_candidate(training, records, *, allow_unregistered=False):
    result = rebuild_pairwise_scene(
        training,
        refine_incremental=True,
        seed_strategy="conditioned",
        allow_unregistered=allow_unregistered,
    )
    print("Visual cameras registered:", result.report["retained_image_ids"], flush=True)
    print("Excluded views:", result.report["excluded_images"], flush=True)
    scene = result.scene
    records = subset_camera_holdout(records, [c.image_id for c in scene.cameras])
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
    return result, scene, rounds, removed, holdout, quality


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("matches", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--allow-unusable-images",
        action="store_true",
        help="Exclude unconstrained views and retain at least two visual cameras",
    )
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
        fitted = fit_candidate(training, records, allow_unregistered=args.allow_unusable_images)
        initial = fitted[0]
        candidates = (
            supported_camera_subsets(
                initial.report["retained_image_ids"], initial.report["pair_poses"]
            )
            if args.allow_unusable_images
            else []
        )
        attempts = []
        for group in [None, *candidates]:
            if group is not None:
                print("Trying visually supported subset:", group, flush=True)
                try:
                    fitted = fit_candidate(subset_camera_matches(training, group), records)
                except ValueError as error:
                    attempts.append(
                        {"image_ids": list(group), "passed": False, "failure_reason": str(error)}
                    )
                    continue
            result, scene, rounds, removed, holdout, quality = fitted
            attempts.append(
                {
                    "image_ids": [c.image_id for c in scene.cameras],
                    "passed": quality["passed"],
                    "camera_quality": quality,
                    "held_out_acceptance_fraction": holdout["accepted_fraction"],
                }
            )
            print(
                "Candidate quality:",
                quality["passed"],
                "held out:",
                holdout["accepted_fraction"],
                flush=True,
            )
            if quality["passed"]:
                break
        retained = [c.image_id for c in scene.cameras]
        exclusion_reasons = {r["image_id"]: r for r in initial.report["excluded_images"]}
        excluded = [
            exclusion_reasons.get(
                i,
                {
                    "image_id": i,
                    "reason": "outside_first_validated_visual_subset",
                },
            )
            for i in audit["image_ids"]
            if i not in retained
        ]
        scene.metadata["quality_status"] = (
            "visual_validation_passed" if quality["passed"] else "visual_validation_failed"
        )
        scene.metadata["camera_fit_sources"] = "images_only_no_ruler_no_imu"
        scene.metadata["excluded_images"] = excluded
        report = {
            "schema_version": 1,
            "session_id": audit["session_id"],
            "image_ids": audit["image_ids"],
            "retained_image_ids": [c.image_id for c in scene.cameras],
            "excluded_images": excluded,
            "candidate_attempts": attempts,
            "selection_uses_visual_validation": len(attempts) > 1,
            "ruler_or_imu_used_for_selection": False,
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
