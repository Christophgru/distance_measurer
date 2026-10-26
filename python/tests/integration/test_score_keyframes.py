import hashlib
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

from distance_measuring.camera import CameraIntrinsics
from distance_measuring.scene.model import COORDINATE_FRAME

COMMAND = Path(__file__).resolve().parents[2] / "scripts/score_keyframes.py"


def prepare(tmp_path):
    root = tmp_path / "parallax"
    root.mkdir()
    camera = CameraIntrinsics(400, 400, 319.5, 239.5, 640, 480)
    points = np.array(
        [(x, y) for x in [60, 180, 300, 420, 540] for y in [60, 180, 300, 420]], float
    )
    points = np.r_[points, points[:10] + 5]
    np.savez(
        root / "pair.npz",
        track_ids=np.arange(30),
        source_pixels=points,
        target_pixels=points + [3, 0],
        parallax_px=np.full(30, 3.0),
    )
    report = {
        "schema_version": 1,
        "session_id": "original_synthetic",
        "coordinate_frame": COORDINATE_FRAME,
        "selected_image_indices": [0, 1],
        "images": [{"index": i, "working_intrinsics": camera.to_dict()} for i in range(2)],
        "pairs": [
            {
                "image_indices": [0, 1],
                "input_track_count": 30,
                "available": True,
                "arrays_file": "pair.npz",
                "compensation_increases_median_motion": True,
            }
        ],
    }
    (root / "parallax.json").write_text(json.dumps(report))
    return root


def run(root, output):
    return subprocess.run(
        [sys.executable, str(COMMAND), str(root), "--output", str(output)],
        capture_output=True,
        text=True,
    )


def hashes(root):
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in root.iterdir()}


def test_cli_labels_frames_and_retains_unverified_compensation_warning(tmp_path):
    root, output = prepare(tmp_path), tmp_path / "out"
    before = hashes(root)
    result = run(root, output)
    assert result.returncode == 0, result.stderr
    assert "Image 0: skip" in result.stdout and "Image 1: candidate keyframe" in result.stdout
    report = json.loads((output / "keyframes.json").read_text())
    assert (
        report["candidate_count"] == 1
        and report["frames"][1]["compensation_increases_median_motion"]
    )
    assert report["input_files_sha256"] == before and hashes(root) == before
    assert run(root, output).returncode == 2


def test_invalid_or_missing_rotation_never_creates_a_keyframe_candidate(tmp_path):
    root, output = prepare(tmp_path), tmp_path / "out"
    with np.load(root / "pair.npz") as arrays:
        values = dict(arrays)
    values["parallax_px"][10:] = np.nan
    np.savez(root / "pair.npz", **values)
    assert run(root, output).returncode == 0
    report = json.loads((output / "keyframes.json").read_text())
    assert not report["frames"][1]["candidate"] and report["frames"][1]["track_count"] == 10
    assert run(root, root / "out").returncode == 2
