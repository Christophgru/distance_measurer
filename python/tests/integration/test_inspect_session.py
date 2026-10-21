import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from distance_measuring.dataset import load_session, validate_session
from distance_measuring.dataset.inspection import plot_acceleration, summarize_session

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "inspect_session.py"


def run_inspector(path, *options):
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(path), *options],
        text=True,
        capture_output=True,
        check=False,
    )


def test_text_inspection_reports_requested_diagnostics(capture):
    result = run_inspector(capture.path)
    assert result.returncode == 0, result.stderr
    for text in (
        "Duration: 1.000000 s",
        "accelerometer: 99 samples, 100.00 Hz",
        "gyroscope:",
        "rotation_vector:",
        "Images: 3",
        "delta=0.250000 s",
        "1250000000 ns",
        "request_timestamps",
        "Validation: PASS",
    ):
        assert text in result.stdout


def test_json_summary_retains_exact_nanosecond_integers(capture):
    result = run_inspector(capture.path, "--json")
    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["valid"] is True
    assert summary["images"][0]["timestamp_ns"] == 1_250_000_000
    assert type(summary["images"][0]["timestamp_ns"]) is int
    assert summary["images"][0]["callback_latency_s"] == pytest.approx(0.005)
    assert summary["sensors"]["gyroscope"]["max_gap_s"] == pytest.approx(0.01)


def test_invalid_timeline_has_nonzero_exit_and_actionable_error(capture):
    (capture.path / "images/image_000.jpg").unlink()
    result = run_inspector(capture.path, "--json")
    assert result.returncode == 1
    summary = json.loads(result.stdout)
    assert not summary["valid"]
    assert "missing_image" in {issue["code"] for issue in summary["errors"]}


@pytest.mark.parametrize("options", [(), ("--json",)])
def test_malformed_input_is_reported_without_traceback(capture, options):
    (capture.path / "session.json").write_text("broken")
    result = run_inspector(capture.path, *options)
    assert result.returncode == 2
    assert "session.json" in result.stdout + result.stderr
    assert "Traceback" not in result.stdout + result.stderr
    if options:
        assert not json.loads(result.stdout)["valid"]


def test_inspection_does_not_modify_capture(capture):
    paths = [path for path in capture.path.rglob("*") if path.is_file()]
    before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    session = load_session(capture.path)
    assert summarize_session(session, validate_session(session))["valid"]
    assert before == {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


def test_plot_is_saved_without_gui_and_raw_output_is_rejected(capture, tmp_path):
    session = load_session(capture.path)
    destination = plot_acceleration(session, tmp_path / "processed" / "acceleration.png")
    assert destination.read_bytes().startswith(b"\x89PNG")
    with pytest.raises(ValueError, match="outside the raw session"):
        plot_acceleration(session, capture.path / "acceleration.png")


def test_cli_plot_and_json_work_together(capture, tmp_path):
    destination = tmp_path / "processed" / "plot.png"
    result = run_inspector(capture.path, "--json", "--plot", str(destination))
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["plot"] == str(destination)
    assert destination.is_file()


def test_cli_invalid_gap_threshold(capture):
    result = run_inspector(capture.path, "--max-sensor-gap-s", "nan")
    assert result.returncode == 2
    assert "positive finite" in result.stderr
