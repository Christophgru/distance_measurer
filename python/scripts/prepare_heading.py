"""Align a visual scene once to compass north and anchor gyro motion to images."""

import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

from distance_measuring.dataset import load_session
from distance_measuring.inertial.heading import prepare_visual_heading
from distance_measuring.scene import load_scene, save_scene


def motion_plot(session, continuous, report, output):
    samples = session.sensor_streams["accelerometer"]
    times = np.array([(s.timestamp_ns - session.start_ns) / 1e9 for s in samples])
    lo, hi = report["continuous_orientation_coverage_s"]
    times = times[(times >= lo) & (times <= hi)]
    axis = np.array(report["heading_axis_device"])
    figure = Figure(figsize=(11, 7))
    FigureCanvasAgg(figure)
    heading, error = figure.subplots(2, 1)
    corrected, gyro = continuous(times), continuous.gyro(times)
    for orientation, label, style in [
        (gyro, "Gyro propagation", "--"),
        (corrected, "Gyro with image heading corrections", "-"),
    ]:
        # Lecture 002 pp. 38–45/81–83: camera forward direction in world axes.
        forward = orientation.apply(axis)
        azimuth = np.degrees(np.unwrap(np.arctan2(forward[:, 0], forward[:, 1])))
        azimuth[np.linalg.norm(forward[:, :2], axis=1) < 1e-6] = np.nan
        heading.plot(times, azimuth, style, label=label)
    photo_times = np.array([p["time_s"] for p in report["photos"]])
    photo_azimuth = np.array([p["image_heading_degrees"] for p in report["photos"]])
    # Place photo markers on the displayed unwrapped branch, without changing rotations.
    corrected_forward = corrected.apply(axis)
    branch = np.degrees(np.unwrap(np.arctan2(corrected_forward[:, 0], corrected_forward[:, 1])))
    photo_azimuth += 360 * np.round((np.interp(photo_times, times, branch) - photo_azimuth) / 360)
    heading.scatter(photo_times, photo_azimuth, color="black", zorder=5, label="Image orientations")
    for p, azimuth in zip(report["photos"], photo_azimuth, strict=True):
        heading.annotate(
            str(p["image_id"]), (p["time_s"], azimuth), xytext=(5, 6), textcoords="offset points"
        )
    heading.set(
        xlabel="Seconds since recording start",
        ylabel=f"Image heading (degrees; {report['heading_axis']})",
    )
    heading.legend(fontsize=8)
    delta = (gyro.inv() * corrected).magnitude()
    error.plot(times, np.degrees(delta))
    error.set(
        xlabel="Seconds since recording start", ylabel="Visual correction magnitude (degrees)"
    )
    for ax in [heading, error]:
        ax.grid(alpha=0.2)
        for t in photo_times:
            ax.axvline(t, color="gray", lw=0.6, ls=":")
    source = (
        "Single compass reference: magnetic north"
        if report["compass"]["used"]
        else "Initial reference only: compass heading unverified"
    )
    figure.suptitle(source + "\nRelative photo rotations come from images; scale remains arbitrary")
    figure.tight_layout()
    figure.savefig(output / "heading.png", dpi=170)
    np.savez_compressed(
        output / "orientation.npz",
        time_s=times,
        device_to_reference_xyzw=corrected.as_quat(),
        gyro_only_xyzw=gyro.as_quat(),
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session", type=Path)
    parser.add_argument("--scene", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--stationary-window", nargs=2, type=float, default=[0.1, 1.6], metavar=("START", "END")
    )
    args = parser.parse_args(argv)
    try:
        output = args.output.resolve()
        raw = Path(__file__).resolve().parents[2] / "data/raw"
        if any(
            output.is_relative_to(p)
            for p in [raw, args.session.resolve(), args.scene.resolve().parent]
        ):
            raise ValueError("Heading output must be outside raw and input scene directories")
        session, scene = load_session(args.session), load_scene(args.scene)
        inputs = [
            args.scene,
            *(args.session / n for n in ["session.json", "events.jsonl", "sensors.csv"]),
            *(session.image_path(e) for e in session.images),
        ]
        hashes = {str(p.resolve()): hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs}
        aligned, continuous, report = prepare_visual_heading(
            session, scene, stationary_window=args.stationary_window
        )
        report["input_sha256"] = hashes
        report["schema_version"] = 1
        output.mkdir(parents=True, exist_ok=True)
        motion_plot(session, continuous, report, output)
        if any(hashlib.sha256(Path(p).read_bytes()).hexdigest() != h for p, h in hashes.items()):
            raise ValueError("A heading input changed during processing")
        save_scene(output / "scene.json", aligned)
        (output / "heading.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    except (ValueError, OSError) as error:
        print(f"Cannot prepare heading: {error}", file=sys.stderr)
        return 2
    print("Relative photo rotations: visual reconstruction")
    print("Global reference:", report["world_alignment_source"])
    if not report["compass"]["used"]:
        print("Compass reference unavailable:", report["compass"]["reason"])
    print("Metric scale accepted: False")
    print("Report:", output / "heading.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
