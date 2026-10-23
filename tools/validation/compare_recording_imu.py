"""Plot acceleration using phone rotation vectors and gyro-integrated orientation.

No image reconstruction or metric scale fitting. Source captures stay immutable.
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

from distance_measuring.dataset import load_session, validate_session
from distance_measuring.dataset.inspection import summarize_session
from distance_measuring.inertial.comparison import compare_recorded_imu

COLORS = {
    "integrated_gyro": "#555b66",
    "phone_rotation_vector": "#ce7027",
    "phone_game_rotation_vector": "#228c65",
    "bias_corrected_gyro": "#367cb7",
}


def photo_markers(axes, report):
    start, end = report["common_coverage_s"]
    for ax in axes:
        ax.set_xlim(start, end)
        ax.grid(alpha=0.2)
        lo, hi = report["stationary_calibration"]["window_s"]
        ax.axvspan(
            lo,
            hi,
            color="#41a271" if report["stationary_calibration"]["passed"] else "#d78b68",
            alpha=0.1,
        )
        for p in report["photos"]:
            if not start <= p["time_s"] <= end:
                continue
            color = "#a5aab2" if p["timestamp_source"] == "camera_frame" else "#c07727"
            ax.axvline(p["time_s"], ls=":", color=color, lw=0.8)
    for p in report["photos"]:
        axes[0].text(
            p["time_s"],
            0.98,
            str(p["image_id"]),
            transform=axes[0].get_xaxis_transform(),
            va="top",
            ha="center",
            fontsize=8,
        )


def plots(report, arrays, output):
    times = arrays["time_s"]
    methods = report["methods"]
    figure = Figure(figsize=(14, 11))
    FigureCanvasAgg(figure)
    axes = figure.subplots(4, 1, sharex=True)
    baseline = arrays["integrated_gyro_linear_acceleration_enu_m_s2"]
    for name, stats in methods.items():
        acceleration = arrays[name + "_linear_acceleration_enu_m_s2"]
        style = "--" if name == "phone_game_rotation_vector" else "-"
        for i in range(3):
            axes[i].plot(
                times, acceleration[:, i], style, color=COLORS[name], lw=0.8, label=stats["label"]
            )
        if name != "integrated_gyro":
            rms = stats["linear_acceleration_difference_vs_gyro_m_s2"]["vector_rms"]
            axes[3].plot(
                times,
                np.linalg.norm(acceleration - baseline, axis=1),
                style,
                color=COLORS[name],
                lw=1,
                label=f"{stats['label']} vs gyro (RMS {rms:.3f})",
            )
    for ax, label in zip(
        axes,
        ["East acceleration", "North acceleration", "Up acceleration", "Vector difference vs gyro"],
        strict=True,
    ):
        ax.set_ylabel(label + "\n(m/s²)")
    axes[0].legend(fontsize=8, loc="upper right")
    axes[3].legend(fontsize=8, loc="upper left")
    axes[-1].set_xlabel("Seconds since recording start; vertical lines mark photos")
    photo_markers(axes, report)
    rest = (
        "passed"
        if report["stationary_calibration"]["passed"]
        else "FAILED; gyro bias remains unestimated"
    )
    figure.suptitle(
        f"{report['session_id']}\n"
        "Same accelerometer → different orientations → subtract nominal gravity\n"
        f"Declared rest window {rest}; phone-based curves are calculated offline",
        fontsize=12,
    )
    figure.tight_layout()
    figure.savefig(output / "acceleration_comparison.png", dpi=170)
    figure = Figure(figsize=(14, 9))
    FigureCanvasAgg(figure)
    axes = figure.subplots(2, 2).ravel()
    for name, stats in methods.items():
        if name == "integrated_gyro":
            continue
        style = "--" if name == "phone_game_rotation_vector" else "-"
        axes[0].plot(
            times,
            arrays[name + "_orientation_difference_degrees"],
            style,
            color=COLORS[name],
            label=stats["label"],
        )
        axes[1].plot(
            times,
            arrays[name + "_tilt_difference_degrees"],
            style,
            color=COLORS[name],
            label=stats["label"],
        )
    axes[0].set(title="3D orientation difference from gyro", ylabel="Degrees")
    axes[1].set(title="Tilt difference from gyro (gravity direction)", ylabel="Degrees")
    for name in ["rotation_vector", "game_rotation_vector"]:
        if name not in report["native_increment_comparison"]:
            continue
        key = "phone_" + name
        axes[2].plot(
            arrays[name + "_increment_time_s"],
            arrays[name + "_increment_difference_degrees"],
            color=COLORS[key],
            lw=0.8,
            label=methods[key]["label"],
        )
    axes[2].set(
        title="Native orientation increments unsupported by gyro",
        ylabel="Increment disagreement (degrees)",
    )
    if "magnetic_field_microtesla" in arrays:
        strength = np.linalg.norm(arrays["magnetic_field_microtesla"], axis=1)
        axes[3].plot(arrays["magnetometer_time_s"], strength, color="#73549c")
        axes[3].set(title="Recorded magnetic field strength", ylabel="Microtesla")
    else:
        axes[3].text(
            0.5, 0.5, "Magnetometer not recorded", ha="center", transform=axes[3].transAxes
        )
    photo_markers(axes, report)
    for ax in axes:
        ax.set_xlabel("Seconds since recording start")
    axes[0].legend(fontsize=8)
    axes[1].legend(fontsize=8)
    axes[2].legend(fontsize=8)
    figure.suptitle(
        "Phone orientation vs gyro: disagreement does not establish which source is accurate",
        fontsize=12,
    )
    figure.tight_layout()
    figure.savefig(output / "orientation_comparison.png", dpi=170)
    figure = Figure(figsize=(14, 9))
    FigureCanvasAgg(figure)
    axes = figure.subplots(3, 1, sharex=True)
    measured = arrays["device_acceleration_including_gravity_m_s2"]
    for i, axis in enumerate("XYZ"):
        axes[i].plot(
            times,
            measured[:, i],
            color="#2476b5",
            lw=0.8,
            label="Measured accelerometer (gravity + motion)",
        )
        for name, stats in methods.items():
            axes[i].plot(
                times,
                arrays[name + "_gravity_device_m_s2"][:, i],
                "--" if name == "phone_game_rotation_vector" else "-",
                color=COLORS[name],
                lw=0.8,
                label=stats["label"],
            )
        axes[i].set_ylabel(f"Device {axis} (m/s²)")
    axes[0].legend(fontsize=8, loc="upper right")
    axes[-1].set_xlabel("Seconds since recording start")
    photo_markers(axes, report)
    figure.suptitle(
        "Measured acceleration vs gravity predicted by each orientation\n"
        "Predicted device gravity = Rᵀ × [0, 0, 9.80665]; residual also includes real motion",
        fontsize=12,
    )
    figure.tight_layout()
    figure.savefig(output / "gravity_comparison.png", dpi=170)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--stationary-window", type=float, nargs=2, default=[0.1, 1.6])
    args = parser.parse_args(argv)
    try:
        output = args.output.resolve()
        raw = Path(__file__).resolve().parents[2] / "data/raw"
        if any(output.is_relative_to(p) for p in [raw, args.session.resolve()]):
            raise ValueError("Comparison outputs must stay outside raw capture directories")
        session = load_session(args.session, validate=False)
        validation = validate_session(session)
        validation.raise_for_errors()
        inputs = [args.session / n for n in ["session.json", "events.jsonl", "sensors.csv"]]
        inputs += [session.image_path(e) for e in session.images]
        hashes = {str(p.resolve()): hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs}
        report, arrays = compare_recorded_imu(session, stationary_window=args.stationary_window)
        report["input_sha256"] = hashes
        report["inspection"] = summarize_session(session, validation)
        output.mkdir(parents=True, exist_ok=True)
        plots(report, arrays, output)
        np.savez_compressed(output / "imu_comparison.npz", **arrays)
        if any(hashlib.sha256(Path(p).read_bytes()).hexdigest() != h for p, h in hashes.items()):
            raise ValueError("An input changed during IMU comparison")
        (output / "imu_comparison.json").write_text(
            json.dumps(report, indent=2, allow_nan=False) + "\n"
        )
    except (ValueError, OSError) as error:
        print(f"Cannot compare recording IMU: {error}", file=sys.stderr)
        return 2
    print("Recording:", report["session_id"])
    print("Declared rest window passed:", report["stationary_calibration"]["passed"])
    for name, method in report["methods"].items():
        if name == "integrated_gyro":
            continue
        print(
            name,
            "acceleration difference RMS (m/s²):",
            method["linear_acceleration_difference_vs_gyro_m_s2"]["vector_rms"],
        )
    print("Report:", output / "imu_comparison.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
