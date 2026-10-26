import hashlib
import json
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest
from numpy.testing import assert_allclose

ROOT = Path(__file__).resolve().parents[2]
ANALYZE = ROOT / "scripts/analyze_track_parallax.py"
TRACK = ROOT / "scripts/track_session.py"


def prepare(capture, tmp_path, *, static=False, mode="normal"):
    image = cv2.resize(
        np.random.default_rng(5).integers(0, 256, (60, 90), np.uint8),
        (360, 240),
        interpolation=cv2.INTER_CUBIC,
    )
    for event in capture.events:
        if event["type"] != "image":
            continue
        step = 0 if static else event["index"]
        pixels = cv2.warpAffine(
            image, np.array([[1.0, 0, 4 * step], [0, 1.0, 2 * step]]), (360, 240)
        )
        assert cv2.imwrite(str(capture.path / event["filename"]), pixels)
        event["metadata"].update(
            width=360,
            height=240,
            exif_orientation=1,
            rotation_degrees=0,
            lens_facing=0 if mode == "front" else 1,
            sensor_orientation_degrees=90,
            target_rotation=0,
            is_flipped=False,
        )
    if mode == "no_gyro":
        capture.metadata["sensors"]["gyroscope"] = False
        capture.metadata["sensor_counts"]["gyroscope"] = 0
        capture.rows = [row for row in capture.rows if row["sensor_type"] != "gyroscope"]
    capture.write()
    tracking = tmp_path / "tracking"
    result = subprocess.run(
        [
            sys.executable,
            str(TRACK),
            str(capture.path),
            "--output",
            str(tracking),
            "--max-points",
            "120",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return tracking


def run(capture, tracking, output, *args):
    return subprocess.run(
        [
            sys.executable,
            str(ANALYZE),
            str(capture.path),
            str(tracking),
            "--output",
            str(output),
            "--best-n",
            "12",
            *args,
        ],
        capture_output=True,
        text=True,
    )


def hashes(paths):
    return {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def test_command_reuses_p2_history_and_gyro_without_changing_sources(capture, tmp_path):
    tracking = prepare(capture, tmp_path)
    paths = [p for root in (tracking, capture.path) for p in root.rglob("*") if p.is_file()]
    before = hashes(paths)
    output = tmp_path / "parallax"
    result = run(capture, tracking, output)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "gyro=" in result.stdout and "median parallax=" in result.stdout
    report = json.loads((output / "parallax.json").read_text())
    assert report["success"] and len(report["pairs"]) == 2
    assert not any(report["assumptions"].values())
    for pair in report["pairs"]:
        assert pair["available"] and pair["gyro"]["sample_count"] >= 25
        assert pair["gyro"]["sample_span_s"] >= 0.25
        assert pair["approximate_image_timestamps"]
        assert 3 < pair["median_parallax_px"] < 6
        assert pair["selected_count"] == 12 and pair["selection_shortfall"] == 0
        assert pair["selected_source_cells"] >= 8 and pair["selected_target_cells"] >= 8
        assert_allclose(pair["R_second_first"], np.eye(3), atol=1e-14)
        with np.load(output / pair["arrays_file"], allow_pickle=False) as arrays:
            assert_allclose(
                arrays["parallax_px"],
                np.linalg.norm(arrays["target_pixels"] - arrays["source_pixels"], axis=1),
                atol=1e-10,
            )
            chosen = arrays["selected_indices"]
            assert arrays["track_ids"][chosen].tolist() == [
                t["track_id"] for t in pair["selected_tracks"]
            ]
            assert (arrays["parallax_px"][chosen] >= 2).all()
        assert cv2.imread(str(output / pair["debug_image"])) is not None
    assert before == hashes(paths)
    assert run(capture, tracking, output).returncode == 2


def test_rotation_only_sequence_has_no_artificially_useful_tracks(capture, tmp_path):
    tracking = prepare(capture, tmp_path, static=True)
    output = tmp_path / "static"
    result = run(capture, tracking, output)
    assert result.returncode == 0, result.stdout + result.stderr
    for pair in json.loads((output / "parallax.json").read_text())["pairs"]:
        assert pair["median_parallax_px"] < 1e-3
        assert pair["eligible_count"] == pair["selected_count"] == 0
        assert pair["selection_shortfall"] == 12


@pytest.mark.parametrize(
    "mode,reason", [("front", "unmirrored back camera"), ("no_gyro", "gyro samples")]
)
def test_unavailable_rotation_is_explicit_and_never_replaced_with_identity(
    capture, tmp_path, mode, reason
):
    tracking = prepare(capture, tmp_path, mode=mode)
    output = tmp_path / "unavailable"
    result = run(capture, tracking, output)
    assert result.returncode == 1, result.stdout + result.stderr
    report = json.loads((output / "parallax.json").read_text())
    assert not report["success"]
    for pair in report["pairs"]:
        assert not pair["available"] and reason in pair["reason"]
        assert "R_second_first" not in pair


def test_source_hash_mismatch_and_history_tampering_are_rejected(capture, tmp_path):
    tracking = prepare(capture, tmp_path)
    path = tracking / "observations.jsonl"
    lines = [json.loads(line) for line in path.read_text().splitlines()]
    lines[0]["pixel"][0] += 10
    path.write_text("".join(json.dumps(row) + "\n" for row in lines))
    result = run(capture, tracking, tmp_path / "bad_history")
    assert result.returncode == 2 and "disagree" in result.stderr
    capture.rows[0]["x"] = 0.2
    capture.write()
    result = run(capture, tracking, tmp_path / "changed_source")
    assert result.returncode == 2 and "Recording changed" in result.stderr


def test_raw_p2_and_symlink_outputs_are_protected(capture, tmp_path):
    tracking = prepare(capture, tmp_path)
    alias = tmp_path / "alias"
    alias.symlink_to(tracking, target_is_directory=True)
    for output in (capture.path / "p3", tracking / "p3", alias / "p3"):
        result = run(capture, tracking, output)
        assert result.returncode == 2 and "outside" in result.stderr
        assert not output.exists()


@pytest.mark.parametrize(
    "args", [("--best-n", "0"), ("--min-parallax-px", "nan"), ("--max-gyro-gap-s", "0")]
)
def test_bad_command_options_are_clear(capture, tmp_path, args):
    result = run(capture, tmp_path / "unused_p2", tmp_path / "bad", *args)
    assert result.returncode == 2 and "Cannot analyze track parallax:" in result.stderr
    assert "Traceback" not in result.stderr
