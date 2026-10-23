import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "tools/validation/audit_correspondences.py"


def test_excluded_ruler_and_screen_regions_never_enter_feature_search():
    spec = importlib.util.spec_from_file_location("audit_mask_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    regions = {
        "coordinate_frame": "normalized_stored_pixels_ignore_exif",
        "images": {"4": [[0, 0], [1, 0], [1, 1], [0, 1]]},
        "exclude_images": {"4": [[[0.2, 0.3], [0.6, 0.3], [0.6, 0.8], [0.2, 0.8]]]},
    }
    mask = module.search_mask(regions, 4, (101, 101))
    assert not mask[30:81, 20:61].any()
    assert mask[5, 5] == 255
    regions["exclude_images"]["4"][0][0][0] = -0.1
    with pytest.raises(ValueError, match="normalized"):
        module.search_mask(regions, 4, (101, 101))


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


def test_one_good_pair_cannot_hide_an_isolated_third_image(capture_factory, tmp_path):
    capture = capture_factory(image_count=3)
    rng = np.random.default_rng(918)
    first = cv2.GaussianBlur(rng.integers(15, 220, (240, 320), np.uint8), (3, 3), 0.6)
    second = cv2.warpAffine(first, np.array([[1, 0, 9], [0, 1, -4]], np.float32), (320, 240))
    blank = np.full_like(first, 127)
    for event, image in zip(
        [e for e in capture.events if e["type"] == "image"], [first, second, blank], strict=True
    ):
        assert cv2.imwrite(str(capture.path / event["filename"]), image)
        event["metadata"].update(width=320, height=240, exif_orientation=1, rotation_degrees=0)
    capture.write()
    output = tmp_path / "isolated_audit"
    result = run(capture, output, "--image-indices", "0", "1", "2")
    assert result.returncode == 1
    assert "Disconnected image overlap" in result.stderr and "[2]" in result.stderr
    report = json.loads((output / "correspondences.json").read_text())
    assert report["pairwise_usable_count"] > 8
    assert report["overlap"]["components"] == [[0, 1], [2]]
    assert report["quality_status"] == "disconnected_overlap"
    assert (output / "review.html").exists()  # retained solely to inspect the failure


def test_four_overlapping_images_are_supported_without_three_view_cycles(capture_factory, tmp_path):
    capture = capture_factory(image_count=4)
    rng = np.random.default_rng(918)
    base = cv2.GaussianBlur(rng.integers(15, 220, (240, 320), np.uint8), (3, 3), 0.6)
    for i, event in enumerate(e for e in capture.events if e["type"] == "image"):
        image = cv2.warpAffine(
            base, np.array([[1, 0, i * 5], [0, 1, -i * 2]], np.float32), (320, 240)
        )
        assert cv2.imwrite(str(capture.path / event["filename"]), image)
        event["metadata"].update(width=320, height=240, exif_orientation=1, rotation_degrees=0)
    capture.write()
    output = tmp_path / "four"
    result = run(capture, output, "--image-indices", "0", "1", "2", "3")
    assert result.returncode == 0, result.stderr
    report = json.loads((output / "correspondences.json").read_text())
    assert report["overlap"]["connected"] and report["tracks"] == []
    assert all(len(t["group_ids"]) == 4 for t in report["pairwise_matches"])


@pytest.mark.parametrize(
    "option", [["--ratio", "nan"], ["--image-indices", "0"], ["--image-indices", "0", "0"]]
)
def test_invalid_pair_options_do_not_create_outputs(capture, tmp_path, option):
    output = tmp_path / "bad_pair"
    result = run(capture, output, *option)
    assert result.returncode == 2 and "Cannot audit correspondences:" in result.stderr
    assert not output.exists()
