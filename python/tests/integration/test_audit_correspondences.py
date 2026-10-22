import hashlib
import json
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "tools/validation/audit_correspondences.py"


def run(capture, output, *args):
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            str(capture.path),
            "--output",
            str(output),
            "--image-indices",
            "0",
            "1",
            "--max-tilt",
            "0",
            "--max-features",
            "400",
            "--stored-orientation",
            *args,
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def test_two_photo_recording_exports_usable_pairwise_matches_without_any_third_view(
    capture_factory, tmp_path
):
    capture = capture_factory(image_count=2)
    rng = np.random.default_rng(918)
    first = cv2.GaussianBlur(rng.integers(15, 220, (240, 320), np.uint8), (3, 3), 0.6)
    second = cv2.warpAffine(first, np.array([[1, 0, 9], [0, 1, -4]], np.float32), (320, 240))
    for event, image in zip(
        [e for e in capture.events if e["type"] == "image"], [first, second], strict=True
    ):
        assert cv2.imwrite(str(capture.path / event["filename"]), image)
        event["metadata"].update(width=320, height=240, exif_orientation=1, rotation_degrees=0)
    capture.write()
    before = {
        p: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in capture.path.rglob("*")
        if p.is_file()
    }
    output = tmp_path / "pairwise"
    result = run(capture, output)
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads((output / "correspondences.json").read_text())
    assert report["image_ids"] == [0, 1]
    assert report["third_view_required"] is False
    assert report["pose_or_scale_updated"] is False
    assert report["tracks"] == []
    assert report["cycle_summary"] == {"status": "not_required_for_pairwise_matching"}
    assert report["pairwise_usable_count"] > 8
    for record in report["pairwise_matches"]:
        assert record["support_count"] == 2 and len(record["working_pixels"]) == 2
        if record["usable_pairwise"]:
            assert record["pairwise_epipolar_consistent"] and record["correlation"]["accepted"]
            assert record["correlation"]["score"] >= 0.7
            assert record["correlation"]["query_excluded_from_model"]
    assert (output / "match_cards_00.jpg").exists()
    html = (output / "review.html").read_text()
    assert "__AUDIT_DATA__" not in html and "data:image/jpeg;base64," in html
    assert before == {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in before}


def test_featureless_two_images_report_no_usable_points(capture_factory, tmp_path):
    capture = capture_factory(image_count=2)
    output = tmp_path / "blank_pair"
    result = run(capture, output)
    assert result.returncode == 1, result.stdout + result.stderr
    report = json.loads((output / "correspondences.json").read_text())
    assert not report["pairwise_matches"] and not report["pairwise_usable_count"]
    assert (output / "review.html").exists()


@pytest.mark.parametrize(
    "option", [["--ratio", "nan"], ["--image-indices", "0"], ["--image-indices", "0", "0"]]
)
def test_invalid_pair_options_do_not_create_outputs(capture, tmp_path, option):
    output = tmp_path / "bad_pair"
    result = run(capture, output, *option)
    assert result.returncode == 2 and "Cannot audit correspondences:" in result.stderr
    assert not output.exists()
