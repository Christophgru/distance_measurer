import pytest

from distance_measuring.vision.prototype_diagnostics import _camera_work, statistics


def test_nested_growth_and_matching_are_counted_once_including_reverse_searches():
    growth = {
        "epipolar_search": {
            "runtime_s": 2,
            "candidates_examined": 13,
            "reverse_candidates_examined": 7,
        },
        "triangulation_runtime_s": 3,
        "track_merging_runtime_s": 1,
        "runtime_s": 99,
    }
    local = {"runtime_s": 4, "candidates_examined": 17, "reverse_candidates_examined": 5}
    attempts = [{"runtime_s": 0.1}, {"runtime_s": 0.2}, {"runtime_s": 0.3}]
    registration = {
        "pnp_attempts": attempts,
        "pnp_runtime_s": 0.6,
        "local_search": local,
        "two_view_prior": {"local_search": local, "relative_pose": {"runtime_s": 0.5}},
    }
    refinement = {
        "rounds": [
            {
                "round": 1,
                "bundle_adjustment": {"runtime_s": 10},
                "observation_retry": {"runtime_s": 98, "local_search": local},
                "growth": growth,
                "search_radius_before_px": 10,
                "search_radius_px": 8,
            }
        ],
        "matching_runtime_s": 200,
        "bundle_adjustment_runtime_s": 300,
    }
    report = _camera_work(registration, growth, refinement)
    assert report["pnp_attempts"] == attempts
    assert report["pnp_runtime_s"] == pytest.approx(sum(a["runtime_s"] for a in attempts))
    assert report["local_patch_search_runtime_s"] == 12
    assert report["epipolar_search_runtime_s"] == 4
    assert report["triangulation_runtime_s"] == 6
    assert report["track_merging_runtime_s"] == 2
    assert report["local_ba_runtime_s"] == 10
    assert report["two_view_pose_runtime_s"] == 0.5
    assert report["descriptor_candidates_examined"] == 40
    assert report["patch_centers_examined"] == 66


def test_empty_statistics_are_json_safe():
    assert statistics([float("nan"), float("inf")]) == {
        "count": 0,
        "min": None,
        "median": None,
        "p95": None,
        "max": None,
    }
    assert statistics([1, 2, 3])["median"] == 2
