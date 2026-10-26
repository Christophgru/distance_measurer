import json
from dataclasses import asdict
from pathlib import Path

import pytest

from distance_measuring.vision.keyframes import KeyframeConfig, score_keyframe

FIXTURES = Path(__file__).resolve().parents[3] / "tools/validation/keyframe_cases.json"


def test_shared_python_kotlin_cases_and_defaults():
    data = json.loads(FIXTURES.read_text())
    assert asdict(KeyframeConfig()) == data["settings"]
    for row in data["cases"]:
        result = score_keyframe(
            row["tracks"],
            row["source_cells"],
            row["target_cells"],
            row["parallax_px"],
            row["size"],
            rotation_available=row["rotation_available"],
        )
        assert result["candidate"] == row["candidate"], row["name"]
        assert result["reasons"] == row["reasons"]
        assert 0 <= result["score"] <= 1


@pytest.mark.parametrize(
    "options",
    [{"min_tracks": 0}, {"min_occupied_cells": 49}, {"min_parallax_fraction": float("nan")}],
)
def test_bad_keyframe_configuration_fails(options):
    with pytest.raises(ValueError):
        KeyframeConfig(**options)


def test_invalid_counts_or_parallax_fail():
    with pytest.raises(ValueError):
        score_keyframe(24, 25, 6, 3.0, (1600, 1200))
    with pytest.raises(ValueError):
        score_keyframe(24, 6, 6, float("nan"), (1600, 1200))


def test_threshold_scales_with_preview_resolution():
    full = score_keyframe(24, 6, 6, 2.4, (1600, 1200))
    preview = score_keyframe(24, 6, 6, 0.96, (640, 480))
    assert not full["candidate"] and full["score"] == pytest.approx(preview["score"])
