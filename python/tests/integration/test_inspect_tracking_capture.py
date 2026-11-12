import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/inspect_session.py"


def run(path, *options):
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(path), *options],
        capture_output=True,
        text=True,
        check=False,
    )


def test_existing_inspector_reports_both_streams(hybrid_capture):
    result = run(hybrid_capture.path, "--json")
    assert result.returncode == 0, result.stdout + result.stderr
    summary = json.loads(result.stdout)
    assert summary["valid"] and len(summary["images"]) == 3
    assert summary["tracking"]["frame_count"] == 3
    assert summary["tracking"]["dropped_frame_count"] == 1
    assert summary["tracking"]["unknown_clock_frame_count"] == 0
    result = run(hybrid_capture.path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Tracking: complete; 3 saved frames; 1 explicit drops; 1 resets" in result.stdout
    assert "Images: 3" in result.stdout


def test_existing_inspector_reports_missing_preview_pixels_as_validation_error(hybrid_capture):
    frame = next(r for r in hybrid_capture.records if r["type"] == "frame")
    (hybrid_capture.path / frame["filename"]).unlink()
    result = run(hybrid_capture.path, "--json")
    assert result.returncode == 1 and "Traceback" not in result.stderr
    report = json.loads(result.stdout)
    assert "tracking_frame_file" in {e["code"] for e in report["errors"]}


def test_existing_inspector_reports_malformed_sidecar_as_format_error(hybrid_capture):
    (hybrid_capture.path / "tracking.json").write_text('{"schema_version":2}')
    result = run(hybrid_capture.path, "--json")
    assert result.returncode == 2 and "Traceback" not in result.stderr
    assert "unsupported version 2" in json.loads(result.stdout)["format_error"]
