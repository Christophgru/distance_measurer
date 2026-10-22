"""Explain the ruler/acceleration scale gap without changing reconstruction.

The ruler supplies a diagnostic reference, never an inertial scale constraint.
Lecture references use one-based PPTX slide order; see the companion math note.
"""

import argparse
import hashlib
import json
from itertools import combinations
from pathlib import Path

import numpy as np
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.ticker import MaxNLocator
from retry_ruler import imu_experiment, ruler_check
from scipy.spatial.transform import Rotation

from distance_measuring.dataset import load_session
from distance_measuring.inertial.experimental import (
    back_camera_from_device,
    fit_visual_scale,
    gyro_orientation_interpolator,
    integrate_interval,
    orientation_interpolator,
    three_pose_scale_diagnostics,
)
from distance_measuring.scene import load_scene


def orientation_jumps(session):
    """Compare short fused-orientation increments against gyro increments.

    Near-vertical world-axis jumps suggest heading corrections. They must not
    be interpreted as measured physical camera motion or gravity tilt.
    """
    base = session.start_ns
    rotation = session.sensor_streams["rotation_vector"]
    gyro = session.sensor_streams["gyroscope"]
    rt = np.array([(s.timestamp_ns - base) / 1e9 for s in rotation])
    R = Rotation.from_quat([[s.x, s.y, s.z, s.w] for s in rotation])
    gt = np.array([(s.timestamp_ns - base) / 1e9 for s in gyro])
    gw = np.array([[s.x, s.y, s.z] for s in gyro])
    sl = gyro_orientation_interpolator(gt, gw, Rotation.identity())
    images = [next(e for e in session.images if e.index == i) for i in range(3)]
    begin = (images[0].timestamp_ns - base) / 1e9
    end = (images[-1].callback_timestamp_ns - base) / 1e9
    inside = (rt >= max(begin, gt[0])) & (rt <= min(end, gt[-1]))
    rt, R = rt[inside], R[inside]
    G = sl(rt)
    observed = R[:-1].inv() * R[1:]
    predicted = G[:-1].inv() * G[1:]
    errors = np.degrees((observed * predicted.inv()).magnitude())
    events = []
    for i in np.argsort(errors)[-10:][::-1]:
        increment = (R[i + 1] * R[i].inv()).as_rotvec()
        norm = np.linalg.norm(increment)
        events.append(
            {
                "start_s": float(rt[i]),
                "end_s": float(rt[i + 1]),
                "rotation_vector_increment_degrees": float(np.degrees(norm)),
                "gyro_increment_degrees": float(np.degrees(predicted[i].magnitude())),
                "increment_difference_degrees": float(errors[i]),
                "rotation_vector_axis_world": (increment / norm).tolist() if norm > 0 else None,
                "axis_angle_to_vertical_degrees": float(
                    np.degrees(np.arccos(np.clip(abs(increment[2]) / norm, 0, 1)))
                )
                if norm > 0
                else None,
            }
        )
    return {
        "interval_s": [begin, end],
        "increment_difference_p99_degrees": float(np.percentile(errors, 99)),
        "largest_increment_disagreements": events,
        "interpretation": "Near-vertical jumps unsupported by gyro suggest heading corrections",
    }


def analyze(session, scene, retry):
    cameras = {c.image_id: c for c in scene.cameras}
    centers = np.array([cameras[i].extrinsics.C_w for i in range(3)])
    seed = retry["reconstruction"]["seed_image_ids"]
    ruler = ruler_check(scene, seed)
    if not ruler["passes_10_percent_and_reprojection"]:
        raise ValueError("Current scene fails the reference ruler geometry gate")
    imu, at, world = imu_experiment(session, scene, ruler)
    old_scales = [t["fit"]["raw_scale_solution_m_per_unit"] for t in retry["imu"]["scale_trials"]]
    new_scales = [t["fit"]["raw_scale_solution_m_per_unit"] for t in imu["scale_trials"]]
    if not np.allclose(old_scales, new_scales, atol=1e-10, rtol=1e-8):
        raise ValueError("Scene/session no longer reproduce the selected retry report")
    reference = ruler["reference_scale_m_per_unit"]
    gyro_imu, _, _ = imu_experiment(session, scene, ruler, orientation_source="gyroscope")
    trials = []
    for orientation, t in [
        (orientation, t)
        for orientation, result in [("rotation_vector", imu), ("gyroscope", gyro_imu)]
        for t in result["scale_trials"]
    ]:
        diagnostic = three_pose_scale_diagnostics(
            centers, t["elapsed_s"], t["integrated_displacements_visual_world_m"], reference
        )
        error = diagnostic["constant_world_error_family"][-1]["acceleration_error_norm_m_s2"]
        trials.append(
            {
                "orientation_source": orientation,
                "timing": t["timing"],
                "gravity_mode": t["gravity_mode"],
                "fixed_bias_fit": t["fit"],
                **diagnostic,
                "reference_equivalent_error_percent_g": 100 * error / 9.80665,
                # Equivalent gravity leakage magnitude only, NOT measured tilt.
                "reference_equivalent_gravity_tilt_degrees": float(
                    np.degrees(np.arctan(error / 9.80665))
                ),
            }
        )
    rotation = session.sensor_streams["rotation_vector"]
    sl = orientation_interpolator(
        [(s.timestamp_ns - session.start_ns) / 1e9 for s in rotation],
        [[s.x, s.y, s.z, s.w] for s in rotation],
    )
    images = [next(e for e in session.images if e.index == i) for i in range(3)]
    R_cd = back_camera_from_device(images[0].metadata.sensor_orientation_degrees)
    sweep = []
    for fraction in np.linspace(0, 1, 41):
        times = np.array(
            [
                (
                    (e.timestamp_ns - session.start_ns)
                    + fraction * (e.callback_timestamp_ns - e.timestamp_ns)
                )
                / 1e9
                for e in images
            ]
        )
        # Lecture 002 pp. 38–45, 81–83: compose rigid coordinate changes.
        # All trial vectors must share the visual reconstruction's world basis.
        visual_from_enu = cameras[0].extrinsics.R_cw.T @ R_cd @ sl(times[0]).as_matrix().T
        for mode, gravity in [
            ("nominal_vertical_gravity", [0, 0, 9.80665]),
            ("early_median_gravity_plus_bias_proxy", imu["early_gravity_plus_bias_proxy_m_s2"]),
        ]:
            displacements = np.array(
                [
                    visual_from_enu @ integrate_interval(at, world - gravity, times[0], t)[0]
                    for t in times[1:]
                ]
            )
            fit = fit_visual_scale(centers, times - times[0], displacements)
            sweep.append(
                {
                    "request_callback_fraction": float(fraction),
                    "gravity_mode": mode,
                    "raw_scale_m_per_unit": fit["raw_scale_solution_m_per_unit"],
                }
            )
    primary = trials[0]
    scale = primary["fixed_bias_fit"]["positive_scale_candidate_m_per_unit"]
    gyro_scale = gyro_imu["scale_trials"][0]["fit"]["positive_scale_candidate_m_per_unit"]
    baselines = []
    for i, j in combinations(range(3), 2):
        # Lecture 002 p. 36: norm in an orthonormal world frame; p. 93: spacing
        # supplies a reference scale. Neither diagnostic length enters IMU fitting.
        distance = np.linalg.norm(centers[j] - centers[i])
        baselines.append(
            {
                "image_ids": [i, j],
                "visual_length_units": float(distance),
                "ruler_reference_length_m": float(distance * reference),
                "imu_candidate_length_m": None if scale is None else float(distance * scale),
                "gyro_candidate_length_m": None
                if gyro_scale is None
                else float(distance * gyro_scale),
            }
        )
    return {
        "schema_version": 1,
        "session_id": session.metadata.session_id,
        "reference_image_ids": seed,
        "reference_interval_cm": [5, 10],
        "reference_scale_m_per_unit_comparison_only": reference,
        "primary_reference_to_imu_scale_ratio": None if scale is None else reference / scale,
        "baseline_comparison": baselines,
        "trials": trials,
        "gyro_orientation_sensitivity": gyro_imu,
        "orientation_increment_comparison": orientation_jumps(session),
        "timing_sensitivity_sweep": sweep,
        "rotation_comparison": imu["rotation_comparison"],
        "callback_latency_s": imu["image_callback_latency_s"],
        "early_world_acceleration_error_proxy_m_s2": (
            np.array(imu["early_gravity_plus_bias_proxy_m_s2"]) - [0, 0, 9.80665]
        ).tolist(),
        "early_window_stationarity_validated": False,
        "bias_calibrated": False,
        "scale_applied_to_scene": False,
        "metric_scale_accepted": False,
    }


def plot(report, output):
    figure = Figure(figsize=(14, 10))
    FigureCanvasAgg(figure)
    motion = figure.add_subplot(221, projection="3d")
    timing = figure.add_subplot(222)
    bias = figure.add_subplot(223)
    baseline = figure.add_subplot(224)
    primary = report["trials"][0]
    reference = report["reference_scale_m_per_unit_comparison_only"]
    visual = np.array(primary["visual_velocity_difference_units_s"])
    inertial = np.array(primary["inertial_velocity_difference_m_s"])
    for v, label, color in [
        (visual * reference, "Visual × ruler reference", "#278f53"),
        (inertial, "Integrated IMU", "#bb7037"),
        (visual * primary["projection_scale_m_per_unit"], "IMU projection onto visual", "#267eac"),
    ]:
        motion.quiver(0, 0, 0, *v, color=color, arrow_length_ratio=0.12, label=label)
    gyro_primary = next(t for t in report["trials"] if t["orientation_source"] == "gyroscope")
    gyro_vector = np.array(gyro_primary["inertial_velocity_difference_m_s"])
    motion.quiver(
        0,
        0,
        0,
        *gyro_vector,
        color="#8457a6",
        arrow_length_ratio=0.12,
        label="Gyro orientation trial",
    )
    limit = (
        max(
            np.max(np.abs(visual * reference)),
            np.max(np.abs(inertial)),
            np.max(np.abs(gyro_vector)),
        )
        * 1.15
    )
    motion.set(xlim=(-limit, limit), ylim=(-limit, limit), zlim=(-limit, limit))
    motion.set_box_aspect([1, 1, 1])
    for axis in [motion.xaxis, motion.yaxis, motion.zaxis]:
        axis.set_major_locator(MaxNLocator(4))
    motion.set(xlabel="X (m/s)", ylabel="Y (m/s)", zlabel="Z (m/s)")
    motion.set_title(
        "Velocity-eliminated motion vectors\n"
        f"Direction disagreement: {primary['direction_difference_degrees']:.2f}°"
    )
    motion.legend(fontsize=8, loc="upper left")
    for mode, label in [
        ("nominal_vertical_gravity", "Fused + nominal gravity"),
        ("early_median_gravity_plus_bias_proxy", "Fused + early proxy"),
    ]:
        rows = [r for r in report["timing_sensitivity_sweep"] if r["gravity_mode"] == mode]
        timing.plot(
            [r["request_callback_fraction"] for r in rows],
            [r["raw_scale_m_per_unit"] for r in rows],
            label=label,
        )
    timing.axhline(reference, color="#278f53", ls="--", label="Ruler reference")
    for mode, marker, label in [
        ("nominal_vertical_gravity", "o", "Gyro + nominal gravity"),
        ("early_median_gravity_plus_bias_proxy", "x", "Gyro + early proxy"),
    ]:
        rows = [
            r
            for r in report["gyro_orientation_sensitivity"]["scale_trials"]
            if r["gravity_mode"] == mode
        ]
        timing.plot(
            [r["timing_fraction"] for r in rows],
            [r["fit"]["raw_scale_solution_m_per_unit"] for r in rows],
            marker=marker,
            ls="--",
            label=label,
        )
    timing.axhline(0, color="gray", lw=0.7)
    timing.set(
        xlabel="Assumed fraction from button request to JPEG callback",
        ylabel="Scale (m / reconstruction unit)",
        title="Timing sensitivity; negative candidates remain rejected",
    )
    timing.legend(fontsize=8)
    # The bias family is affine in assumed scale. Its endpoints suffice to plot
    # all intermediate hypotheses; every one fits these three positions exactly.
    first, last = primary["constant_world_error_family"][0::2]
    scales = np.linspace(0, reference * 1.3, 100)
    b0 = np.array(first["world_acceleration_error_to_subtract_m_s2"])
    b1 = np.array(last["world_acceleration_error_to_subtract_m_s2"])
    errors = b0 + (scales / reference)[:, None] * (b1 - b0)
    for i, axis in enumerate("XYZ"):
        bias.plot(scales, errors[:, i], label=axis)
    bias.plot(scales, np.linalg.norm(errors, axis=1), color="black", ls="--", label="Norm")
    bias.axvline(reference, color="#278f53", ls=":", label="Ruler reference")
    bias.set(
        xlabel="Assumed scale (m / reconstruction unit)",
        ylabel="World acceleration error to subtract (m/s²)",
        title=(
            "Any scale fits with a free constant world error\n"
            "Sensitivity family; not a bias calibration"
        ),
    )
    bias.legend(fontsize=8, ncol=3)
    rows = report["baseline_comparison"]
    x = np.arange(len(rows))
    baseline.bar(
        x - 0.25, [r["ruler_reference_length_m"] * 100 for r in rows], 0.25, label="Ruler reference"
    )
    baseline.bar(
        x,
        [
            np.nan if r["imu_candidate_length_m"] is None else r["imu_candidate_length_m"] * 100
            for r in rows
        ],
        0.25,
        label="Rotation-vector candidate",
    )
    baseline.bar(
        x + 0.25,
        [
            np.nan if r["gyro_candidate_length_m"] is None else r["gyro_candidate_length_m"] * 100
            for r in rows
        ],
        0.25,
        label="Gyro candidate",
    )
    baseline.set(
        xticks=x,
        xticklabels=["–".join(map(str, r["image_ids"])) for r in rows],
        xlabel="Camera pair",
        ylabel="Camera-center distance (cm)",
        title="Same visual scene, different global scales",
    )
    baseline.legend(fontsize=8)
    for ax in [timing, bias, baseline]:
        ax.grid(alpha=0.2)
    figure.suptitle("Why the acceleration-scaled ruler is too small — no metric scale applied")
    figure.tight_layout()
    figure.savefig(output / "scale_discrepancy.png", dpi=170)


def main():
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", type=Path, default=root / "data/raw/measurement01")
    parser.add_argument(
        "--retry-report",
        type=Path,
        default=root / "data/processed/measurement01/ruler_retry/ruler_retry.json",
    )
    parser.add_argument(
        "--output", type=Path, default=root / "data/processed/measurement01/scale_discrepancy"
    )
    args = parser.parse_args()
    output = args.output.resolve()
    protected = [root / "data/raw", args.session.resolve(), args.retry_report.resolve().parent]
    if any(output.is_relative_to(p) for p in protected):
        parser.error("Outputs must be outside raw and retry input directories")
    retry = json.loads(args.retry_report.read_text())
    sources = {Path(p): h for p, h in retry["input_sha256"].items()}
    if any(hashlib.sha256(p.read_bytes()).hexdigest() != h for p, h in sources.items()):
        parser.error("A retry input changed; rerun the ruler retry first")
    scene_path = args.retry_report.parent / "scene.json"
    for p in [
        args.retry_report,
        scene_path,
        args.session / "sensors.csv",
        args.session / "events.jsonl",
        args.session / "session.json",
    ]:
        sources[p.resolve()] = hashlib.sha256(p.read_bytes()).hexdigest()
    session = load_session(args.session)
    scene = load_scene(scene_path)
    if (
        session.metadata.session_id != retry["session_id"]
        or scene.metadata.get("session_id") != retry["session_id"]
    ):
        parser.error("Session, scene and retry report must describe the same recording")
    report = analyze(session, scene, retry)
    report["input_sha256"] = {str(p): h for p, h in sources.items()}
    output.mkdir(parents=True, exist_ok=True)
    plot(report, output)
    if any(hashlib.sha256(p.read_bytes()).hexdigest() != h for p, h in sources.items()):
        raise RuntimeError("An input changed during diagnosis")
    (output / "scale_discrepancy.json").write_text(
        json.dumps(report, indent=2, allow_nan=False) + "\n"
    )
    print("Ruler-reference / IMU scale:", report["primary_reference_to_imu_scale_ratio"])
    print(
        "Motion direction difference (degrees):",
        report["trials"][0]["direction_difference_degrees"],
    )
    print(
        "Gyro-orientation trial scale:",
        report["gyro_orientation_sensitivity"]["scale_trials"][0]["fit"][
            "raw_scale_solution_m_per_unit"
        ],
    )
    print("Report:", output / "scale_discrepancy.json")


if __name__ == "__main__":
    main()
