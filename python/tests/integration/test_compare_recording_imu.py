import hashlib
import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[3] / "tools/validation/compare_recording_imu.py"


def test_comparison_cli_exports_diagnostics_without_changing_capture(capture, tmp_path):
    inputs = [p for p in capture.path.rglob("*") if p.is_file()]
    hashes = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs}
    output = tmp_path / "comparison"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(capture.path), "--output", str(output)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads((output / "imu_comparison.json").read_text())
    assert report["inspection"]["valid"]
    assert not report["visual_reconstruction_performed"] and not report["metric_scale_accepted"]
    assert not report["phone_linear_acceleration_stream_recorded"]
    for name in [
        "acceleration_comparison.png",
        "gravity_comparison.png",
        "orientation_comparison.png",
        "imu_comparison.npz",
    ]:
        assert (output / name).exists()
    assert hashes == {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs}
