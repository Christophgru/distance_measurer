"""Estimate inertial scale AFTER visual camera validation; never refit cameras.

Lecture 002 pp. 36–45/81–83: changes of orthonormal reference frame. Gravity
removal and inertial integration extend the lecture; no ruler length is an input.
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

from distance_measuring.dataset import load_session
from distance_measuring.inertial.experimental import fit_visual_scale, integrate_interval
from distance_measuring.inertial.heading import prepare_visual_heading
from distance_measuring.scene import load_scene


def estimate_scale(session, scene):
    if scene.metadata.get("quality_status") != "visual_validation_passed":
        raise ValueError("Visual camera validation must pass before estimating scale")
    _, orientation, heading = prepare_visual_heading(session, scene)
    camera = {c.image_id: c for c in scene.cameras}
    images = sorted((e for e in session.images if e.index in camera), key=lambda e: e.timestamp_ns)
    times = np.array([(e.timestamp_ns - session.start_ns) / 1e9 for e in images])
    centers = np.array([camera[e.index].extrinsics.C_w for e in images])
    samples = session.sensor_streams["accelerometer"]
    at = np.array([(s.timestamp_ns - session.start_ns) / 1e9 for s in samples])
    raw = np.array([[s.x, s.y, s.z] for s in samples])
    lo, hi = heading["continuous_orientation_coverage_s"]
    inside = (at >= lo) & (at <= hi)
    at, raw = at[inside], raw[inside]
    if np.max(np.diff(at)) > 0.25:
        raise ValueError("Accelerometer gaps exceed 0.25 seconds")
    # Lecture 002 pp. 36–45/81–83: remove gravity in its reference frame, then
    # rotate the result BACK into the frozen visual world. No camera is moved.
    reference_acceleration = orientation(at).apply(raw) - [0, 0, 9.80665]
    reference_from_visual = np.array(heading["R_reference_visual"])
    visual_acceleration = reference_acceleration @ reference_from_visual
    integrals = np.array(
        [integrate_interval(at, visual_acceleration, times[0], t)[0] for t in times[1:]]
    )
    fit = fit_visual_scale(centers, times - times[0], integrals)
    return {
        "schema_version": 1,
        "session_id": session.metadata.session_id,
        "image_ids": [e.index for e in images],
        "photos": [
            {"image_id": e.index, "time_s": float(t), "timestamp_source": e.timestamp_source}
            for e, t in zip(images, times, strict=True)
        ],
        "visual_camera_centers_arbitrary_units": centers.tolist(),
        "integrated_displacements_visual_frame_m": integrals.tolist(),
        "gravity_m_s2": 9.80665,
        "orientation": heading,
        "fit": fit,
        "camera_geometry_changed": False,
        "ruler_used_for_scale_fit": False,
        "metric_scale_applied": False,
        "metric_scale_accepted": False,
        "ruler_10_percent_gate_verified": False,
        "limitations": [
            "A positive scale candidate is not accepted metric scale",
            "Acceleration bias, initial tilt, timing and camera/IMU mounting remain unverified",
            "No ruler comparison has been supplied to this scale-only experiment",
        ],
    }


def plot_candidate(report, output):
    figure = Figure(figsize=(12, 5))
    FigureCanvasAgg(figure)
    motion = figure.add_subplot(121, projection="3d")
    centers = np.array(report["visual_camera_centers_arbitrary_units"])
    motion.plot(*centers.T, "o-")
    for i, C in zip(report["image_ids"], centers, strict=True):
        motion.text(*C, str(i))
    motion.set(
        xlabel="X (visual units)",
        ylabel="Y (visual units)",
        zlabel="Z (visual units)",
        title="Frozen camera locations — images only",
    )
    comparison = figure.add_subplot(122)
    candidate = report["fit"]["positive_scale_candidate_m_per_unit"]
    if candidate is None:
        comparison.axis("off")
        comparison.text(
            0.5,
            0.5,
            "No usable positive scale candidate\n"
            + report["fit"]["rejection_reason"]
            + f"\nSigned solution: {report['fit']['raw_scale_solution_m_per_unit']:.4f} m/unit"
            + f"\nFit residual RMS: {report['fit']['residual_rms_m']:.3f} m",
            ha="center",
            va="center",
            transform=comparison.transAxes,
        )
    else:
        times = np.array([p["time_s"] for p in report["photos"]])
        dt = times - times[0]
        inertial = np.vstack([np.zeros(3), report["integrated_displacements_visual_frame_m"]])
        inertial += dt[:, None] * report["fit"]["initial_velocity_world_m_s"]
        visual = (centers - centers[0]) * candidate
        for k, label in enumerate("XYZ"):
            comparison.plot(times, visual[:, k], "o-", label=f"Visual {label} × trial scale")
            comparison.plot(times, inertial[:, k], "x--", label=f"Inertial {label}")
        comparison.legend(fontsize=7)
    comparison.set(
        xlabel="Seconds since recording start",
        ylabel="Trial displacement (m)",
        title="Acceleration scale candidate — unaccepted",
    )
    comparison.grid(alpha=0.2)
    figure.tight_layout()
    figure.savefig(output / "scale_candidate.png", dpi=160)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session", type=Path)
    parser.add_argument("--scene", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        output = args.output.resolve()
        raw = Path(__file__).resolve().parents[2] / "data/raw"
        if any(
            output.is_relative_to(p)
            for p in (raw, args.session.resolve(), args.scene.resolve().parent)
        ):
            raise ValueError("Scale output must be outside raw and source scene directories")
        session, scene = load_session(args.session), load_scene(args.scene)
        inputs = [
            args.scene,
            *(args.session / n for n in ["session.json", "events.jsonl", "sensors.csv"]),
        ]
        hashes = {str(p.resolve()): hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs}
        report = estimate_scale(session, scene)
        report["input_sha256"] = hashes
        if any(hashlib.sha256(Path(p).read_bytes()).hexdigest() != h for p, h in hashes.items()):
            raise ValueError("A scale input changed during estimation")
        output.mkdir(parents=True, exist_ok=True)
        plot_candidate(report, output)
        (output / "scale_candidate.json").write_text(
            json.dumps(report, indent=2, allow_nan=False) + "\n"
        )
        print("Retained photos:", report["image_ids"])
        print("Scale fit:", report["fit"])
        print("Metric scale remains unaccepted; camera geometry is unchanged")
        return 0
    except (ValueError, OSError) as error:
        print(f"Cannot estimate scene scale: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
