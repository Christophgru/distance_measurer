"""Retry measurement01's held-out ruler check with pairwise matches and IMU data.

Existing scenes/captures are immutable. Ruler ticks never enter pose fitting or
the acceleration-only scale estimate. Lecture pages use PPTX slide order.
"""

import argparse
import hashlib
import json
from itertools import combinations
from pathlib import Path

import numpy as np
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from measurement01_ruler import TICKS
from scipy.spatial.transform import Rotation

from distance_measuring.dataset import load_session
from distance_measuring.inertial.experimental import (
    back_camera_from_device,
    fit_visual_scale,
    gyro_orientation_interpolator,
    integrate_interval,
    orientation_interpolator,
)
from distance_measuring.inertial.heading import prepare_visual_heading
from distance_measuring.optimization.bundle import bundle_adjust, reprojection_residuals
from distance_measuring.scene import load_scene, save_scene
from distance_measuring.vision.pairwise_scene import rebuild_pairwise_scene
from distance_measuring.vision.triangulation import triangulate_point


def ruler_check(scene, ids):
    cameras = {c.image_id: c for c in scene.cameras}
    normal = []
    diagnostic = []
    for tick in range(4):
        args = (
            TICKS[list(ids), tick],
            [cameras[i].intrinsics for i in ids],
            [cameras[i].extrinsics for i in ids],
        )
        normal.append(triangulate_point(*args))
        diagnostic.append(triangulate_point(*args, max_reprojection_error_px=1e6))
    accepted = all(p.position is not None for p in normal)
    lengths = None
    if all(p.position is not None for p in diagnostic):
        # Lecture 002 p. 36: Euclidean length in the orthonormal world basis.
        lengths = np.linalg.norm(np.diff([p.position for p in diagnostic], axis=0), axis=1)
    reference = None if lengths is None else 0.05 / lengths[0]
    held = None if lengths is None else lengths[1:] * reference * 100
    deviations = None if held is None else np.abs(held / 5 - 1) * 100
    errors = [e for p in normal for e in p.reprojection_errors_px]
    return {
        "image_ids": list(ids),
        "tick_cm": [5, 10, 15, 20],
        "normal_rejections": [p.rejection_reason for p in normal],
        "normal_reprojection_errors_px": [list(p.reprojection_errors_px) for p in normal],
        "all_ticks_accepted": accepted,
        "max_reprojection_error_px": max(errors) if errors else None,
        "interval_lengths_reconstruction_units": lengths.tolist() if accepted else None,
        "diagnostic_interval_lengths_reconstruction_units": None
        if lengths is None
        else lengths.tolist(),
        # Lecture 002 p. 93 motivates known spacing. Use ONLY 5–10 cm for this
        # reference-ratio check; reserve 10–15 and 15–20 cm as held-out validation.
        "reference_interval_cm": [5, 10],
        "reference_scale_m_per_unit": reference,
        "held_out_reference_scaled_lengths_cm": held.tolist() if accepted else None,
        "diagnostic_held_out_lengths_cm": None if held is None else held.tolist(),
        "diagnostic_held_out_deviations_percent": None
        if deviations is None
        else deviations.tolist(),
        "passes_10_percent_and_reprojection": bool(accepted and np.all(deviations <= 10)),
    }


def checks(scene):
    return [ruler_check(scene, ids) for ids in [*combinations(range(3), 2), (0, 1, 2)]]


def imu_experiment(
    session,
    scene,
    seed_measurement,
    *,
    orientation_source="rotation_vector",
    stationary_window=(0.1, 1.6),
):
    streams = session.sensor_streams
    base = session.start_ns
    rotation = streams["rotation_vector"]
    acceleration = streams["accelerometer"]
    gyro = streams["gyroscope"]
    if min(len(acceleration), len(gyro)) < 2:
        raise ValueError("Retry needs accelerometer and gyroscope streams")
    rt, sl = None, None
    if orientation_source != "visual_anchored_gyroscope":
        if len(rotation) < 2:
            raise ValueError("The legacy orientation comparison needs rotation-vector samples")
        rt = np.array([(s.timestamp_ns - base) / 1e9 for s in rotation])
        sl = orientation_interpolator(rt, [[s.x, s.y, s.z, s.w] for s in rotation])
    gt = np.array([(s.timestamp_ns - base) / 1e9 for s in gyro])
    gw = np.array([[s.x, s.y, s.z] for s in gyro])
    seed_time = None
    visual_heading = None
    if orientation_source == "visual_anchored_gyroscope":
        _, sl, visual_heading = prepare_visual_heading(
            session, scene, stationary_window=stationary_window
        )
        rt = np.array(visual_heading["continuous_orientation_coverage_s"])
        seed_time = float(rt[0])
    elif orientation_source == "gyroscope":
        covered = (gt >= rt[0]) & (gt <= rt[-1])
        if covered.sum() < 2:
            raise ValueError("Need gyro samples inside rotation-vector coverage")
        seed_time = float(gt[covered][0])
        sl = gyro_orientation_interpolator(gt[covered], gw[covered], sl(seed_time))
        rt = gt[covered]
    elif orientation_source != "rotation_vector":
        raise ValueError("Unknown orientation source")
    at = np.array([(s.timestamp_ns - base) / 1e9 for s in acceleration])
    raw = np.array([[s.x, s.y, s.z] for s in acceleration])
    inside = (at >= rt[0]) & (at <= rt[-1])
    at = at[inside]
    raw = raw[inside]
    # Lecture 002 pp. 38–45/81: change reference frame BEFORE removing gravity.
    # Android rotation vectors map device coordinates into the ENU world frame;
    # the gyro trial seeds that frame once and then propagates it without correction.
    world = sl(at).apply(raw)
    early = at < 0.7
    if early.sum() < 10:
        raise ValueError("Insufficient early samples for gravity/bias sensitivity check")
    proxy = np.median(world[early], axis=0)
    gravity_modes = {
        "nominal_vertical_gravity": np.array([0.0, 0.0, 9.80665]),
        "early_median_gravity_plus_bias_proxy": proxy,
    }
    images = [next(e for e in session.images if e.index == i) for i in range(3)]
    frame_times_available = all(
        e.timestamp_source == "camera_frame"
        and e.metadata.sensor_timestamp_clock_domain == "android_elapsed_realtime_ns"
        and e.metadata.sensor_timestamp_ns == e.timestamp_ns
        for e in images
    )
    for e in images:
        if e.metadata.lens_facing != 1 or e.metadata.target_rotation != 0 or e.metadata.is_flipped:
            raise ValueError(
                "Nominal mounting assumes an unmirrored back camera at native screen rotation"
            )
        if e.metadata.sensor_orientation_degrees != images[0].metadata.sensor_orientation_degrees:
            raise ValueError("Camera mounting changed between images")
    R_cd = back_camera_from_device(images[0].metadata.sensor_orientation_degrees)
    cameras = {c.image_id: c for c in scene.cameras}
    centers = np.array([cameras[i].extrinsics.C_w for i in range(3)])
    trials = []
    rotation_checks = []
    endpoint_checks = []
    naive_pairs = []
    hypotheses = (
        [("camera_frame", None)]
        if frame_times_available
        else [
            ("button_request", 0.0),
            ("request_callback_midpoint", 0.5),
            ("callback", 1.0),
        ]
    )
    for name, fraction in hypotheses:
        times = np.array(
            [
                (e.timestamp_ns - base) / 1e9
                if fraction is None
                else (
                    ((e.request_timestamp_ns or e.timestamp_ns) - base)
                    + fraction
                    * (e.callback_timestamp_ns - (e.request_timestamp_ns or e.timestamp_ns))
                )
                / 1e9
                for e in images
            ]
        )
        R_wd = sl(times).as_matrix()
        for i, j in combinations(range(3), 2):
            expected = R_cd @ R_wd[j].T @ R_wd[i] @ R_cd.T
            actual = cameras[j].extrinsics.R_cw @ cameras[i].extrinsics.R_cw.T
            angle = float(
                np.degrees(np.linalg.norm(Rotation.from_matrix(actual @ expected.T).as_rotvec()))
            )
            rotation_checks.append(
                {
                    "timing": name,
                    "image_ids": [i, j],
                    "visual_imu_rotation_difference_degrees": angle,
                }
            )
        for i, t in enumerate(times):
            gsel = np.abs(gt - t) < 0.15
            asel = np.abs(at - t) < 0.15
            endpoint_checks.append(
                {
                    "timing": name,
                    "image_id": i,
                    "gyro_rms_rad_s": float(np.sqrt(np.mean(np.sum(gw[gsel] ** 2, axis=1)))),
                    "nominal_gravity_removed_acceleration_rms_m_s2": float(
                        np.sqrt(
                            np.mean(
                                np.sum(
                                    (world[asel] - gravity_modes["nominal_vertical_gravity"]) ** 2,
                                    axis=1,
                                )
                            )
                        )
                    ),
                }
            )
        # P_c0=R_c0_w P_w. Align ENU into the visual world using photo zero's
        # orientation; do not assume Android device axes equal stored camera axes.
        visual_from_enu = cameras[0].extrinsics.R_cw.T @ R_cd @ R_wd[0].T
        for mode, g in gravity_modes.items():
            displacement = []
            for t in times[1:]:
                p, _ = integrate_interval(at, world - g, times[0], t)
                displacement.append(visual_from_enu @ p)
            fit = fit_visual_scale(centers, times - times[0], displacement)
            candidate = fit["positive_scale_candidate_m_per_unit"]
            lengths = (
                None
                if candidate is None
                else np.array(seed_measurement["interval_lengths_reconstruction_units"])
                * candidate
                * 100
            )
            trials.append(
                {
                    "timing": name,
                    "timing_fraction": fraction,
                    "gravity_mode": mode,
                    "gravity_removed_world_m_s2": g.tolist(),
                    "elapsed_s": (times - times[0]).tolist(),
                    "integrated_displacements_visual_world_m": np.array(displacement).tolist(),
                    "fit": fit,
                    "ruler_measurement_image_ids": seed_measurement["image_ids"],
                    "acceleration_only_scale_ruler_lengths_cm": None
                    if lengths is None
                    else lengths.tolist(),
                    "ruler_errors_percent": None
                    if lengths is None
                    else (100 * np.abs(lengths / 5 - 1)).tolist(),
                }
            )
        # Naive pair integration is diagnostic only: these endpoints are moving,
        # so zero initial velocity must not be silently promoted to a constraint.
        for i, j in combinations(range(3), 2):
            p, v = integrate_interval(
                at, world - gravity_modes["nominal_vertical_gravity"], times[i], times[j]
            )
            naive_pairs.append(
                {
                    "timing": name,
                    "image_ids": [i, j],
                    "assuming_zero_initial_velocity_displacement_enu_m": p.tolist(),
                    "velocity_increment_enu_m_s": v.tolist(),
                    "initial_velocity_known": False,
                }
            )
    return (
        {
            "orientation_source": orientation_source,
            "visual_heading": visual_heading,
            "rotation_comparison_independent": visual_heading is None,
            "gyro_seed_time_s": seed_time,
            "gyro_bias_assumed_rad_s": [0, 0, 0] if orientation_source == "gyroscope" else None,
            "camera_from_device_nominal": R_cd.tolist(),
            "camera_imu_mounting_calibrated": False,
            "camera_imu_lever_arm_assumed_m": [0, 0, 0],
            "early_gravity_plus_bias_proxy_m_s2": proxy.tolist(),
            "early_window_stationarity_validated": False,
            "image_callback_latency_s": [
                (e.callback_timestamp_ns - (e.request_timestamp_ns or e.timestamp_ns)) / 1e9
                for e in images
            ],
            "image_button_times_s": [
                ((e.request_timestamp_ns or e.timestamp_ns) - base) / 1e9 for e in images
            ],
            "exposure_timestamps_available": frame_times_available,
            "rotation_comparison": rotation_checks,
            "endpoint_diagnostics": endpoint_checks,
            "naive_pair_integrations": naive_pairs,
            "scale_trials": trials,
            "metric_scale_accepted": False,
            "limitations": [
                "Frame timestamps do not resolve rolling shutter/exposure-duration uncertainty"
                if frame_times_available
                else "Photo times are button requests, not measured exposure times",
                "Accelerometer bias, initial velocity and camera/IMU lever arm are not calibrated",
                "Three poses cannot jointly determine scale, initial velocity and constant bias",
            ]
            + (
                ["Gyro orientation assumes zero bias and has no drift correction"]
                if orientation_source == "gyroscope"
                else []
            ),
        },
        at,
        world,
    )


def plots(report, at, world, output):
    figure = Figure(figsize=(13, 6))
    FigureCanvasAgg(figure)
    left, right = figure.subplots(1, 2)
    x = np.arange(3)
    labels = ["0–1", "0–2", "1–2"]
    for row, color, offset, title in [
        (report["old_ruler_checks"], "#a4a9b3", -0.14, "Old M7 (rejected points; diagnostic)"),
        (report["new_ruler_checks"], "#267eac", 0.14, "Rebuilt + refined pairs"),
    ]:
        for k in range(2):
            values = [r["diagnostic_held_out_lengths_cm"][k] for r in row[:3]]
            left.scatter(
                x + offset,
                values,
                color=color,
                marker="o" if k == 0 else "s",
                label=title if k == 0 else None,
            )
    left.axhspan(4.5, 5.5, color="#46ad73", alpha=0.15)
    left.axhline(5, color="#278f53", lw=1)
    left.set(
        xticks=x,
        xticklabels=labels,
        xlabel="Image pair",
        ylabel="Held-out interval (cm; reference-scaled)",
        title="Use 5–10 cm for scale\nTest 10–15 cm (circles), 15–20 cm (squares)",
    )
    left.legend(fontsize=8)
    left.grid(alpha=0.2)
    colors = ["#267eac", "#bb7037", "#8457a6"]
    timing_labels = {
        "button_request": "Button press",
        "request_callback_midpoint": "Midpoint",
        "callback": "Callback",
        "camera_frame": "Camera frame",
    }
    for trial, color in zip(
        [
            t
            for t in report["imu"]["scale_trials"]
            if t["gravity_mode"] == "nominal_vertical_gravity"
        ],
        colors,
        strict=False,
    ):
        values = trial["acceleration_only_scale_ruler_lengths_cm"]
        if values is not None:
            right.plot([0, 1, 2], values, "o-", color=color, label=timing_labels[trial["timing"]])
    callback = next(
        (
            t
            for t in report["imu"]["scale_trials"]
            if t["timing"] == "callback" and t["gravity_mode"] == "nominal_vertical_gravity"
        ),
        None,
    )
    if callback is not None and callback["acceleration_only_scale_ruler_lengths_cm"] is None:
        right.text(
            0.03,
            0.5,
            "Callback rejected: " + callback["fit"]["rejection_reason"],
            transform=right.transAxes,
            fontsize=8,
        )
    right.axhspan(4.5, 5.5, color="#46ad73", alpha=0.15)
    right.axhline(5, color="#278f53", lw=1)
    right.set(
        xticks=[0, 1, 2],
        xticklabels=["5–10", "10–15", "15–20"],
        xlabel="Ruler markings (cm)",
        ylabel="Interval length (cm; unvalidated IMU scale)",
        title="Independent acceleration scale attempt\nNo ruler length enters this scale fit",
    )
    right.legend(fontsize=8)
    right.grid(alpha=0.2)
    figure.suptitle(
        "Ruler retry: improved visual geometry; inertial metric scale remains unverified"
    )
    figure.tight_layout()
    figure.savefig(output / "ruler_comparison.png", dpi=170)
    figure = Figure(figsize=(13, 7))
    FigureCanvasAgg(figure)
    a, b = figure.subplots(2, 1)
    for k, name in enumerate("XYZ"):
        a.plot(at, (world - [0, 0, 9.80665])[:, k], lw=0.8, label=name)
    a.set(
        xlabel="Seconds since timeline start",
        ylabel="Acceleration ENU (m/s²)",
        title="Rotation-compensated acceleration, nominal gravity removed",
    )
    a.legend()
    a.grid(alpha=0.2)
    for i, t in enumerate(report["imu"]["image_button_times_s"]):
        a.axvline(t, color="gray", ls="--", lw=0.7)
        a.text(t, 0.98, f"Photo {i}", transform=a.get_xaxis_transform(), va="top", fontsize=8)
    a.set_xlim(0, report["imu"]["image_button_times_s"][-1] + 1)
    for mode, marker in [
        ("nominal_vertical_gravity", "o"),
        ("early_median_gravity_plus_bias_proxy", "x"),
    ]:
        trials = [t for t in report["imu"]["scale_trials"] if t["gravity_mode"] == mode]
        b.plot(
            np.arange(len(trials)),
            [t["fit"]["raw_scale_solution_m_per_unit"] for t in trials],
            marker=marker,
            label="Nominal gravity"
            if mode == "nominal_vertical_gravity"
            else "Early gravity/bias proxy",
        )
    reference = report["new_ruler_checks"][2]["reference_scale_m_per_unit"]
    b.axhline(reference, color="#278f53", ls="--", label="5 cm ruler reference (comparison only)")
    b.axhline(0, color="gray", lw=0.7)
    b.set(
        xticks=np.arange(len(trials)),
        xticklabels=[timing_labels[t["timing"]] for t in trials],
        ylabel="Candidate scale (m / reconstruction unit)",
        title="Scale sensitivity to timing and gravity/bias assumptions",
    )
    b.legend(fontsize=8)
    b.grid(alpha=0.2)
    figure.tight_layout()
    figure.savefig(output / "acceleration_diagnostics.png", dpi=170)


def main():
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", type=Path, default=root / "data/raw/measurement01")
    parser.add_argument(
        "--matches",
        type=Path,
        default=root / "data/processed/measurement01/matching_review/correspondences.json",
    )
    parser.add_argument(
        "--old-scene", type=Path, default=root / "data/processed/measurement01/m7/scene.json"
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=root / "data/processed/measurement01/ruler_visual_heading_retry",
    )
    parser.add_argument(
        "--orientation-source",
        choices=["visual_anchored_gyroscope", "gyroscope", "rotation_vector"],
        default="visual_anchored_gyroscope",
    )
    parser.add_argument("--stationary-window", nargs=2, type=float, default=[0.1, 1.6])
    args = parser.parse_args()
    output = args.output.resolve()
    protected = [
        args.session.resolve(),
        root / "data/raw",
        args.matches.resolve().parent,
        args.old_scene.resolve().parent,
    ]
    if any(output.is_relative_to(p) for p in protected):
        parser.error("Outputs must be outside raw and input artifact directories")
    session = load_session(args.session)
    audit = json.loads(args.matches.read_text())
    old = load_scene(args.old_scene)
    if audit["image_ids"] != [0, 1, 2] or audit["session_id"] != session.metadata.session_id:
        parser.error(
            "Ruler annotations require measurement01 images 0,1,2 in the original working frame"
        )
    if old.metadata.get("session_id") != audit["session_id"]:
        parser.error("Old scene describes a different recording")
    sources = {Path(p): h for p, h in audit["input_sha256"].items()}
    for p in [
        args.matches,
        args.old_scene,
        args.session / "sensors.csv",
        Path(__file__).with_name("measurement01_ruler.py"),
    ]:
        sources[p.resolve()] = hashlib.sha256(p.read_bytes()).hexdigest()
    if any(hashlib.sha256(p.read_bytes()).hexdigest() != h for p, h in sources.items()):
        parser.error("A recorded matching input has changed; rerun matching first")
    output.mkdir(parents=True, exist_ok=True)
    build = rebuild_pairwise_scene(audit)
    print("Reconstruction:", build.report, flush=True)
    save_scene(output / "scene_before.json", build.scene)
    refined = bundle_adjust(build.scene)
    residual_errors = np.linalg.norm(reprojection_residuals(refined.scene).reshape(-1, 2), axis=1)
    save_scene(output / "scene.json", refined.scene)
    independent = []
    for ids, scene in build.pair_scenes.items():
        save_scene(output / f"pair_{ids[0]:03}_{ids[1]:03}_scene.json", scene)
        independent.append(ruler_check(scene, ids))
    new = checks(refined.scene)
    seed = build.report["seed_image_ids"]
    seed_check = next(r for r in new if r["image_ids"] == seed)
    if not seed_check["all_ticks_accepted"]:
        raise ValueError("Selected visual seed does not triangulate the independent ruler ticks")
    imu, at, world = imu_experiment(
        session,
        refined.scene,
        seed_check,
        orientation_source=args.orientation_source,
        stationary_window=args.stationary_window,
    )
    report = {
        "schema_version": 1,
        "session_id": audit["session_id"],
        "input_sha256": {str(p): h for p, h in sources.items()},
        "reconstruction": build.report,
        "bundle_adjustment": refined.report,
        "post_refinement_training_observations_over_3px": int((residual_errors > 3).sum()),
        "old_ruler_checks": checks(old),
        "independent_pair_ruler_checks": independent,
        "new_ruler_checks": new,
        "imu": imu,
        "pairwise_ruler_shape_gate_passed": all(
            r["passes_10_percent_and_reprojection"] for r in new[:3]
        ),
        "three_view_ruler_gate_passed": new[3]["passes_10_percent_and_reprojection"],
        "absolute_metric_scale_accepted": False,
        "scale_applied_to_scene": False,
    }
    plots(report, at, world, output)
    np.savez_compressed(
        output / "imu_world_acceleration.npz",
        time_s=at,
        acceleration_including_gravity_enu_m_s2=world,
    )
    if any(hashlib.sha256(p.read_bytes()).hexdigest() != h for p, h in sources.items()):
        raise RuntimeError("An input changed during the retry")
    (output / "ruler_retry.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print("Pairwise ruler shape gate:", report["pairwise_ruler_shape_gate_passed"])
    print("Three-view ruler gate:", report["three_view_ruler_gate_passed"])
    print("Absolute metric scale accepted: False; acceleration diagnostics remain experimental")
    print("Report:", output / "ruler_retry.json")


if __name__ == "__main__":
    main()
