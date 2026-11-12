"""Compose P2–P6 without changing their geometric gates or raw captures."""

import hashlib
import json
import platform
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

import cv2
import numpy as np
import scipy

from distance_measuring.dataset import load_session, validate_session
from distance_measuring.dataset.inspection import summarize_session
from distance_measuring.optimization.local_bundle import DEFAULT_LOCAL_BUNDLE
from distance_measuring.scene import load_scene, save_scene

from .bootstrap import DEFAULT_BOOTSTRAP
from .bootstrap_pipeline import bootstrap_session
from .guided import DEFAULT_GUIDED
from .hybrid import DEFAULT_HYBRID
from .hybrid_tracking import hybrid_input_paths, track_hybrid_session
from .incremental_pipeline import extend_tracked_scene
from .landmark_confidence import DEFAULT_CONFIDENCE
from .parallax_pipeline import analyze_track_parallax
from .pnp import DEFAULT_PNP
from .prototype_diagnostics import collect_diagnostics, render_report
from .refinement import DEFAULT_REFINEMENT
from .tracking import DEFAULT_CONFIG
from .tracking_pipeline import track_session

ROOT = Path(__file__).resolve().parents[4]


def _write_json(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def reconstruct_incremental(
    session_path,
    output=None,
    *,
    image_indices=None,
    calibration_path=None,
    max_image_size=1600,
    best_n=200,
    max_gyro_gap_s=0.25,
    tracking_config=DEFAULT_CONFIG,
    bootstrap_config=DEFAULT_BOOTSTRAP,
    guided_config=DEFAULT_GUIDED,
    pnp_config=DEFAULT_PNP,
    refinement_config=DEFAULT_REFINEMENT,
    local_bundle_config=DEFAULT_LOCAL_BUNDLE,
    confidence_config=DEFAULT_CONFIDENCE,
    use_gyro_prior=False,
    tracking_source="auto",
    hybrid_config=DEFAULT_HYBRID,
):
    """Return a report; success requires every explicitly selected camera.

    Geometric failure preserves diagnostics and any partial scene. Invalid input,
    I/O and stage errors raise, with a diagnostic report if output already exists.
    """
    start = perf_counter()
    session_path = Path(session_path).resolve()
    if tracking_source not in ("auto", "stills", "hybrid"):
        raise ValueError("tracking_source must be auto, stills or hybrid")
    has_sidecar = (session_path / "tracking.json").exists()
    hybrid_mode = tracking_source == "hybrid" or (tracking_source == "auto" and has_sidecar)
    default_name = "prototype_hybrid_p9" if hybrid_mode else "prototype_incremental_p8"
    output = (
        Path(output).resolve()
        if output is not None
        else (ROOT / "data/processed" / session_path.name / default_name).resolve()
    )
    if output.is_relative_to((ROOT / "data/raw").resolve()) or output.is_relative_to(session_path):
        raise ValueError("Reconstruction output must be outside data/raw and the raw session")
    if output.exists():
        raise ValueError(f"Output already exists: {output}; choose a fresh directory")
    if type(max_image_size) is not int or max_image_size < 32:
        raise ValueError("max_image_size must be an integer of at least 32")
    if type(best_n) is not int or best_n < 1:
        raise ValueError("best_n must be a positive integer")
    if not np.isfinite(max_gyro_gap_s) or max_gyro_gap_s <= 0:
        raise ValueError("max_gyro_gap_s must be positive and finite")
    if calibration_path is not None:
        calibration_path = Path(calibration_path).resolve(strict=True)
        if calibration_path.is_relative_to(output):
            raise ValueError("Output must not contain the input calibration")
    session = load_session(session_path, validate=False)
    validation = validate_session(session)
    validation.raise_for_errors()
    available = {e.index for e in session.images}
    selected = sorted(available) if image_indices is None else list(image_indices)
    if (
        len(selected) < 2
        or any(type(i) is not int or i not in available for i in selected)
        or selected != sorted(set(selected))
    ):
        raise ValueError("Select at least two distinct recorded image IDs in capture order")
    paths = [session_path / name for name in ("session.json", "events.jsonl", "sensors.csv")]
    paths += [session.image_path(e) for e in session.images]
    if hybrid_mode:
        if session.tracking is None or not session.tracking.frames:
            raise ValueError("Hybrid replay requires saved preview frames")
        paths += hybrid_input_paths(session)

    def fingerprints():
        return {
            str(p.relative_to(session_path)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in paths
        }

    hashes = fingerprints()
    calibration_hash = (
        hashlib.sha256(calibration_path.read_bytes()).hexdigest() if calibration_path else None
    )
    report = {
        "schema_version": 1,
        "recorded_at_utc": datetime.now(UTC).isoformat(),
        "session_path": str(session_path),
        "session_id": session.metadata.session_id,
        "selected_image_indices": selected,
        "input_sha256": hashes,
        "calibration_sha256": calibration_hash,
        "software": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "scipy": scipy.__version__,
            "opencv": cv2.__version__,
            "opencv_threads": cv2.getNumThreads(),
        },
        "settings": {
            "tracking_source": "hybrid" if hybrid_mode else "stills",
            "hybrid": asdict(hybrid_config) if hybrid_mode else None,
            "max_image_size": max_image_size,
            "best_n": best_n,
            "max_gyro_gap_s": max_gyro_gap_s,
            "use_gyro_prior": use_gyro_prior,
            "calibration_path": str(calibration_path) if calibration_path else None,
            **{
                name: asdict(config)
                for name, config in (
                    ("tracking", tracking_config),
                    ("bootstrap", bootstrap_config),
                    ("guided", guided_config),
                    ("pnp", pnp_config),
                    ("refinement", refinement_config),
                    ("local_bundle", local_bundle_config),
                    ("confidence", confidence_config),
                )
            },
        },
        "status": "running",
        "success": False,
        "failure_reason": None,
        "raw_inputs_unchanged": None,
        "stage_runtime_s": {"load_validate_fingerprint": perf_counter() - start},
        "warnings": [w.message for w in validation.warnings],
    }
    output.mkdir(parents=True, exist_ok=False)
    _write_json(output / "session_inspection.json", summarize_session(session, validation))
    active_stage = "tracking"

    def run_stage(name, operation):
        nonlocal active_stage
        active_stage = name
        print(f"Reconstruction stage: {name}", flush=True)
        stage_start = perf_counter()
        result = operation()
        report["stage_runtime_s"][name] = perf_counter() - stage_start
        return result

    try:
        tracking = run_stage(
            "tracking",
            lambda: (track_hybrid_session if hybrid_mode else track_session)(
                session_path,
                output / "tracking",
                image_indices=selected,
                calibration_path=calibration_path,
                max_image_size=max_image_size,
                config=tracking_config,
                **(
                    {"hybrid_config": hybrid_config, "max_gyro_gap_s": max_gyro_gap_s}
                    if hybrid_mode
                    else {}
                ),
            ),
        )
        parallax = run_stage(
            "gyro_parallax",
            lambda: analyze_track_parallax(
                session_path,
                output / "tracking",
                output / "parallax",
                best_n=best_n,
                max_gyro_gap_s=max_gyro_gap_s,
            ),
        )
        bootstrap = run_stage(
            "bootstrap",
            lambda: bootstrap_session(
                output / "parallax", output / "bootstrap", config=bootstrap_config
            ),
        )
        incremental, scene = None, None
        if bootstrap["success"]:
            incremental = run_stage(
                "incremental_refinement",
                lambda: extend_tracked_scene(
                    session_path,
                    output / "tracking",
                    output / "bootstrap",
                    output / "incremental",
                    config=guided_config,
                    pnp_config=pnp_config,
                    parallax_directory=output / "parallax" if use_gyro_prior else None,
                    refinement_config=refinement_config,
                    local_bundle_config=local_bundle_config,
                    confidence_config=confidence_config,
                ),
            )
            scene = load_scene(output / "incremental/scene.json")
        active_stage = "diagnostics_export_verify"
        export_start = perf_counter()
        report["diagnostics"] = collect_diagnostics(
            tracking, parallax, bootstrap, incremental, scene
        )
        if hybrid_mode:
            report["hybrid"] = tracking["hybrid"]
            report["hybrid"]["recording"] = summarize_session(session, validation)["tracking"]
            report["hybrid"]["reconstruction_uses"] = (
                "JPEG poses and landmarks from preview-carried, "
                "dual-image-verified still observations"
            )
        report["warnings"] = list(
            dict.fromkeys(
                [
                    *report["warnings"],
                    *tracking["warnings"],
                    *parallax["warnings"],
                    *bootstrap["warnings"],
                    *((incremental or {}).get("warnings", [])),
                ]
            )
        )
        report["raw_inputs_unchanged"] = fingerprints() == hashes
        if not report["raw_inputs_unchanged"]:
            raise ValueError("Raw session changed during reconstruction")
        if (
            calibration_path
            and hashlib.sha256(calibration_path.read_bytes()).hexdigest() != calibration_hash
        ):
            raise ValueError("Input calibration changed during reconstruction")
        missing = report["diagnostics"]["quality"]["unregistered_image_indices"]
        report["success"] = scene is not None and not missing
        report["status"] = "complete" if report["success"] else "partial" if scene else "failed"
        report["failure_reason"] = (
            None
            if report["success"]
            else (
                f"Insufficient geometric support for selected cameras {missing}; "
                "forward-only registration does not revisit cameras before the bootstrap pair."
                if scene
                else bootstrap["failure_reason"]
            )
        )
        if scene is not None:
            save_scene(output / "scene.json", scene)
        report["stage_runtime_s"][active_stage] = perf_counter() - export_start
        report["stage_runtime_s"]["total"] = perf_counter() - start
        report["diagnostics"]["unattributed_runtime_s"] = report["stage_runtime_s"]["total"] - sum(
            report["diagnostics"]["component_runtime_s"].values()
        )
        _write_json(output / "reconstruction.json", report)
        (output / "REPORT.md").write_text(render_report(report), encoding="utf-8")
    except (OSError, ValueError, KeyError, TypeError, cv2.error) as error:
        report.update(
            status="error", success=False, failure_reason=str(error), failed_stage=active_stage
        )
        report["stage_runtime_s"]["total"] = perf_counter() - start
        _write_json(output / "reconstruction.json", report)
        raise
    return report
