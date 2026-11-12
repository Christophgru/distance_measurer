"""Audit a calibration recording and optional measurement recording before reconstruction."""

import argparse
import hashlib
import json
import sys
from pathlib import Path

import cv2

from distance_measuring.camera.calibration_audit import (
    audit_board_observations,
    compare_capture_settings,
    detect_capture_boards,
)
from distance_measuring.dataset import load_session, validate_session

ROOT = Path(__file__).resolve().parents[2]


def inventory(session):
    hashes = {}
    for path in sorted(session.path.rglob("*")):
        if path.is_file():
            with path.open("rb") as stream:
                hashes[str(path.resolve())] = hashlib.file_digest(stream, "sha256").hexdigest()
    return hashes


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("calibration_session", type=Path)
    parser.add_argument("--measurement-session", type=Path)
    parser.add_argument("--output", type=Path, required=True, help="Fresh directory outside raw")
    parser.add_argument("--columns", type=int, default=9)
    parser.add_argument("--rows", type=int, default=6)
    parser.add_argument("--square-size-m", type=float, default=0.025)
    args = parser.parse_args(argv)
    try:
        cv2.setNumThreads(1)
        output = args.output.resolve()
        roots = [args.calibration_session.resolve(), (ROOT / "data/raw").resolve()]
        if args.measurement_session:
            roots.append(args.measurement_session.resolve())
        if any(output.is_relative_to(p) for p in roots):
            raise ValueError("Intake output must be outside raw recording directories")
        if output.exists():
            raise ValueError("Intake output must be a fresh directory")
        calibration = load_session(args.calibration_session, validate=False)
        validation = validate_session(calibration)
        inputs = inventory(calibration)
        measurement, measurement_validation = None, None
        if args.measurement_session:
            measurement = load_session(args.measurement_session, validate=False)
            measurement_validation = validate_session(measurement)
            inputs.update(inventory(measurement))
        geometry = compare_capture_settings(calibration, calibration)
        incompatible = [
            name for name, value in geometry["fields"].items() if value["status"] == "mismatch"
        ]
        detections, observations = [], {}
        if validation.ok and not incompatible:
            observations, detections = detect_capture_boards(calibration, args.columns, args.rows)
        size = (
            (calibration.images[0].metadata.width, calibration.images[0].metadata.height)
            if calibration.images
            else (1, 1)
        )
        report, candidate = audit_board_observations(
            observations,
            size,
            columns=args.columns,
            rows=args.rows,
            square_size_m=args.square_size_m,
            camera_id=calibration.images[0].metadata.camera_id if calibration.images else None,
        )
        if not validation.ok:
            report["blocking_reasons"].append("calibration_capture_invalid")
        if incompatible:
            report["blocking_reasons"].append("calibration_geometry_changes")
        report.update(
            calibration_session_id=calibration.metadata.session_id,
            calibration_capture_valid=validation.ok,
            calibration_capture_errors=[
                {"code": i.code, "message": i.message} for i in validation.errors
            ],
            calibration_capture_warnings=[
                {"code": i.code, "message": i.message} for i in validation.warnings
            ],
            calibration_geometry=geometry,
            board_detection_performed=validation.ok and not incompatible,
            detections=detections,
            native_pixel_frame="stored_pixels_ignore_exif",
            checkerboard={
                "columns": args.columns,
                "rows": args.rows,
                "square_size_m": args.square_size_m,
            },
            profile_fitted_on_reserved_views=False,
            physical_square_size_verified_by_tool=False,
            thresholds={
                "reserved_corner_rmse_native_px": 2.0,
                "reserved_corner_p95_native_px": 3.0,
            },
            source_images_modified=False,
            reconstruction_started=False,
            metric_measurement_accepted=False,
        )
        profile = output / "candidate_camera.yaml"
        report["candidate_profile_saved"] = bool(candidate and report["calibration_audit_passed"])
        report["candidate_profile"] = str(profile) if report["candidate_profile_saved"] else None
        compatibility = compare_capture_settings(calibration, measurement) if measurement else None
        ready = bool(
            report["calibration_audit_passed"]
            and measurement_validation
            and measurement_validation.ok
            and compatibility["status"] == "matched"
        )
        report["measurement_session_id"] = measurement.metadata.session_id if measurement else None
        report["measurement_capture_valid"] = (
            measurement_validation.ok if measurement_validation else None
        )
        report["measurement_capture_errors"] = (
            [{"code": i.code, "message": i.message} for i in measurement_validation.errors]
            if measurement_validation
            else []
        )
        report["capture_settings_comparison"] = compatibility
        report["ready_for_calibrated_reconstruction"] = ready
        report["next_commands"] = (
            [
                [
                    sys.executable,
                    str(ROOT / "python/scripts/reconstruct_incremental.py"),
                    str(measurement.path),
                    "--calibration",
                    str(profile),
                    "--output",
                    str(
                        ROOT
                        / "data/processed"
                        / measurement.path.name
                        / "calibrated_reconstruction"
                    ),
                ]
            ]
            if ready
            else []
        )
        report["input_sha256"] = inputs
        combined = inventory(calibration)
        if measurement:
            combined.update(inventory(measurement))
        if combined != inputs:
            raise ValueError("Raw recording files changed during calibration intake")
        output.mkdir(parents=True)
        if report["candidate_profile_saved"]:
            candidate.save(profile)
        (output / "intake.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
        blockers = ", ".join(report["blocking_reasons"]) or "none"
        comparison = compatibility["status"] if compatibility else "awaiting measurement session"
        (output / "REPORT.md").write_text(
            "# Calibration capture intake\n\n"
            f"Recording: `{calibration.metadata.session_id}`\n\n"
            f"Detected full boards: **{len(observations)}/{len(calibration.images)}**. "
            f"Training/reserved views: {len(report['training_image_ids'])}/"
            f"{len(report['reserved_image_ids'])}.\n\n"
            f"Calibration audit passed: **{report['calibration_audit_passed']}**. "
            f"Blockers: {blockers}.\n\n"
            f"Capture settings: **{comparison}**. "
            f"Ready for calibrated reconstruction: **{ready}**.\n\n"
            "Every third detected view is reserved before fitting K/distortion. "
            "Each reserved pose uses alternating corners; the other corners test the frozen model. "
            "A candidate profile, when saved, uses training views only. "
            "Missing settings remain unknown. Reconstruction is not started automatically; "
            "physical measurement accuracy remains unaccepted.\n\n"
            "See [complete diagnostics and input hashes](intake.json).\n"
        )
        print(f"Intake report: {output / 'REPORT.md'}")
        print(
            f"Full boards: {len(observations)}/{len(calibration.images)}; "
            f"audit: {report['calibration_audit_passed']}"
        )
        print(f"Capture settings: {comparison}; reconstruction ready: {ready}")
        return 0 if report["calibration_audit_passed"] and (not measurement or ready) else 1
    except (OSError, ValueError, KeyError, TypeError, cv2.error) as error:
        print(f"Cannot audit calibration capture: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
