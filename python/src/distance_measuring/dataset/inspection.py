"""Inspection summaries and acceleration plots for recorded sessions."""

from itertools import pairwise
from pathlib import Path
from typing import Any

from .schema import Session
from .validation import ValidationReport


def summarize_session(session: Session, report: ValidationReport) -> dict[str, Any]:
    """Return JSON-serializable diagnostics, retaining integer nanosecond timestamps."""
    sensors = {}
    for name, stream in session.sensor_streams.items():
        timestamps = sorted(sample.timestamp_ns for sample in stream)
        span_ns = timestamps[-1] - timestamps[0] if timestamps else 0
        sensors[name] = {
            "available": session.metadata.sensors[name],
            "count": len(stream),
            "rate_hz": (len(stream) - 1) * 1e9 / span_ns if span_ns > 0 else None,
            "max_gap_s": max((b - a for a, b in pairwise(timestamps)), default=0) / 1e9,
        }
    images = []
    previous_ns = None
    for image in session.images:
        images.append(
            {
                "index": image.index,
                "filename": image.filename,
                "timestamp_ns": image.timestamp_ns,
                "offset_s": (image.timestamp_ns - session.start_ns) / 1e9
                if session.start_ns is not None
                else None,
                "delta_previous_s": (image.timestamp_ns - previous_ns) / 1e9
                if previous_ns is not None
                else None,
                "callback_latency_s": (image.callback_timestamp_ns - image.request_timestamp_ns)
                / 1e9
                if image.callback_timestamp_ns is not None
                and image.request_timestamp_ns is not None
                else None,
                "timestamp_source": image.timestamp_source,
                "width": image.metadata.width,
                "height": image.metadata.height,
                "rotation_degrees": image.metadata.rotation_degrees,
            }
        )
        previous_ns = image.timestamp_ns
    return {
        "session_id": session.metadata.session_id,
        "directory": str(session.path),
        "state": session.metadata.state,
        "clock_domain": session.metadata.clock_domain,
        "timeline_start_ns": session.start_ns,
        "timeline_end_ns": session.end_ns,
        "duration_s": session.duration_s,
        "dropped_sensor_samples": session.metadata.dropped_sensor_samples,
        "sensors": sensors,
        "images": images,
        "valid": report.ok,
        "errors": [{"code": issue.code, "message": issue.message} for issue in report.errors],
        "warnings": [{"code": issue.code, "message": issue.message} for issue in report.warnings],
    }


def format_summary(summary: dict[str, Any]) -> str:
    duration = summary["duration_s"]
    lines = [
        f"Session: {summary['session_id']}",
        f"Directory: {summary['directory']}",
        f"State: {summary['state'] or 'unspecified'}",
        f"Duration: {duration:.6f} s" if duration is not None else "Duration: unavailable",
        f"Clock: {summary['clock_domain'] or 'unspecified'}",
        f"Dropped sensor samples: {summary['dropped_sensor_samples']}",
        "",
        "Sensors (rates over each stream's observed span):",
    ]
    for name, sensor in summary["sensors"].items():
        rate = (
            f"{sensor['rate_hz']:.2f} Hz" if sensor["rate_hz"] is not None else "rate unavailable"
        )
        lines.append(
            f"  {name}: {sensor['count']} samples, {rate}, "
            f"max gap {sensor['max_gap_s'] * 1000:.2f} ms, "
            f"available={sensor['available']}"
        )
    lines.extend(["", f"Images: {len(summary['images'])}"])
    for image in summary["images"]:
        offset = (
            f"+{image['offset_s']:.6f} s" if image["offset_s"] is not None else "unknown offset"
        )
        delta = (
            f"{image['delta_previous_s']:.6f} s" if image["delta_previous_s"] is not None else "n/a"
        )
        latency = (
            f"{image['callback_latency_s']:.6f} s"
            if image["callback_latency_s"] is not None
            else "unavailable"
        )
        lines.append(
            f"  [{image['index']}] {image['filename']}: {image['timestamp_ns']} ns, "
            f"{offset}, delta={delta}, callback latency={latency}, "
            f"size={image['width']}x{image['height']}, "
            f"rotation={image['rotation_degrees']}"
        )
    for label, key in (("Errors", "errors"), ("Warnings", "warnings")):
        if summary[key]:
            lines.extend(["", f"{label}:"])
            lines.extend(f"  [{issue['code']}] {issue['message']}" for issue in summary[key])
    lines.extend(
        [
            "",
            f"Validation: {'PASS' if summary['valid'] else 'FAIL'} "
            f"({len(summary['errors'])} errors, {len(summary['warnings'])} warnings)",
        ]
    )
    return "\n".join(lines)


def plot_acceleration(session: Session, destination: str | Path) -> Path:
    """Save a headless acceleration-magnitude plot with image request markers."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    destination = Path(destination).resolve()
    if destination.is_relative_to(session.path.resolve()):
        raise ValueError("Plot output must be outside the raw session directory")
    if session.start_ns is None:
        raise ValueError("Cannot plot without a timeline_start event")
    stream = session.sensor_streams["accelerometer"]
    if not stream:
        raise ValueError("Cannot plot without accelerometer samples")
    times = [(sample.timestamp_ns - session.start_ns) / 1e9 for sample in stream]
    magnitudes = np.linalg.norm([(s.x, s.y, s.z) for s in stream], axis=1)
    figure, axis = plt.subplots(figsize=(10, 4))
    try:
        axis.plot(times, magnitudes, linewidth=0.8, label="Accelerometer (includes gravity)")
        for index, image in enumerate(session.images):
            axis.axvline(
                (image.timestamp_ns - session.start_ns) / 1e9,
                color="tab:red",
                alpha=0.6,
                label="Image request timestamp" if index == 0 else None,
            )
        axis.set(
            xlabel="Time since timeline start (s)",
            ylabel="Acceleration magnitude (m/s²)",
            title=f"Session {session.metadata.session_id}",
        )
        axis.grid(alpha=0.2)
        axis.legend()
        figure.tight_layout()
        destination.parent.mkdir(parents=True, exist_ok=True)
        figure.savefig(destination, dpi=150)
    finally:
        plt.close(figure)
    return destination
