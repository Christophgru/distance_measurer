import cv2
import numpy as np
import pytest
from numpy.testing import assert_allclose

from distance_measuring.vision.correlation import (
    correlate_local_point,
    correlate_pair_matches,
    correlate_patch,
)


def texture(size=180):
    return cv2.GaussianBlur(
        np.random.default_rng(17).uniform(20, 180, (size, size)).astype(np.float32), (3, 3), 0.6
    )


def test_normalized_correlation_finds_known_patch_despite_brightness_gain_and_offset():
    search = texture(60)
    template = search[13:30, 24:43] * 1.4 + 18
    peak = correlate_patch(template, search)
    assert peak.rejection_reason is None
    assert peak.top_left == (24, 13)
    assert peak.score > 0.999


def test_repeated_pattern_is_ambiguous_instead_of_accepted():
    template = texture(15)
    search = np.tile(template, (1, 3))
    peak = correlate_patch(template, search)
    assert peak.rejection_reason == "ambiguous_peak"
    assert peak.peak_margin < 1e-5


@pytest.mark.parametrize(
    "template,search,reason",
    [
        (np.ones((5, 5)), np.ones((15, 15)), "textureless_template"),
        (np.arange(25).reshape(5, 5), np.ones((15, 15)), "textureless_search"),
    ],
)
def test_homogeneous_areas_cannot_produce_fake_perfect_correlations(template, search, reason):
    peak = correlate_patch(template, search)
    assert peak.rejection_reason == reason and peak.top_left is None


def test_independent_two_image_correlation_refines_known_translation():
    first = texture()
    H = np.array([[1, 0, 7], [0, 1, -3], [0, 0, 1]], float)
    second = cv2.warpPerspective(first, H, first.shape[::-1])
    result = correlate_local_point(first, second, [80, 90], [88, 86])
    assert result["accepted"] and result["score"] > 0.999
    assert_allclose(result["second_pixel_refined"], [87, 87], atol=1e-7)


def test_pair_correlation_handles_oblique_patch_using_only_an_image_homography():
    first = texture()
    H = np.array([[0.94, -0.1, 17], [0.11, 1.04, -2], [0.0003, -0.0001, 1]])
    second = cv2.warpPerspective(first, H, first.shape[::-1])
    p = np.array([80.0, 90.0])
    target = H @ np.r_[p, 1]
    target = target[:2] / target[2]
    result = correlate_local_point(first, second, p, target, homography=H)
    assert result["accepted"] and result["score"] > 0.85
    assert_allclose(result["second_pixel_refined"], target, atol=1e-7)


def test_local_model_excludes_the_query_and_rejects_wrong_correspondence():
    first = texture()
    H = np.array([[1, 0, 7], [0, 1, -3], [0, 0, 1]], float)
    second = cv2.warpPerspective(first, H, first.shape[::-1])
    x, y = np.meshgrid(np.arange(30, 151, 25), np.arange(30, 151, 25))
    a = np.c_[x.ravel(), y.ravel()]
    b = a + [7, -3]
    b[0] += [24, 15]
    results = correlate_pair_matches(first, second, a, b)
    assert not results[0]["accepted"]
    assert sum(r["accepted"] for r in results[1:]) >= 20
    assert all(r["query_excluded_from_model"] for r in results)


def test_insufficient_local_neighbors_are_unverified_without_demanding_a_third_view():
    image = texture()
    points = np.array([[40, 40], [80, 80]])
    result = correlate_pair_matches(image, image, points, points)
    assert [r["reason"] for r in result] == ["insufficient_local_support"] * 2
    # Direct two-patch correlation still works without extra correspondences.
    assert correlate_local_point(image, image, points[0], points[0])["accepted"]


@pytest.mark.parametrize(
    "template,search",
    [
        (np.ones((10, 10)), np.ones((5, 5))),
        (np.array([[np.nan]]), np.ones((5, 5))),
        (np.zeros((0, 2)), np.ones((5, 5))),
    ],
)
def test_invalid_patch_inputs_fail(template, search):
    with pytest.raises(ValueError):
        correlate_patch(template, search)
