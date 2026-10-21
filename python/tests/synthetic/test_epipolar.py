import numpy as np
import pytest
from numpy.testing import assert_allclose, assert_array_equal
from scipy.spatial.transform import Rotation

from distance_measuring.camera import CameraExtrinsics, CameraIntrinsics, to_homogeneous
from distance_measuring.vision import estimate_fundamental, rectify_matches, sampson_errors


def stereo_points():
    rng = np.random.default_rng(93)
    camera = CameraIntrinsics(800, 790, 639.5, 479.5, 1280, 960)
    points = rng.uniform([-2, -1.5, 4], [2, 1.5, 10], (240, 3))
    pose = CameraExtrinsics(
        Rotation.from_rotvec([0.02, -0.08, 0.01]).as_matrix(), np.array([0.6, 0.03, 0.02])
    )
    return camera.project(points), camera.project(points, pose), camera, pose


def test_known_camera_matrices_obey_lecture_epipolar_lines():
    # Lecture 002 pp. 82–83: M_i = K_i [R_i|O_i]. Page 138's corresponding
    # rays give E=[O]_x R and F=K_2^-T E K_1^-1 (algebraic extension).
    first, second, camera, pose = stereo_points()
    x, y, z = pose.O_cw
    cross = np.array([[0, -z, y], [z, 0, -x], [-y, x, 0]])
    F = np.linalg.inv(camera.K).T @ cross @ pose.R_cw @ np.linalg.inv(camera.K)
    assert_allclose(
        np.sum(to_homogeneous(second) * (to_homogeneous(first) @ F.T), axis=1), 0, atol=1e-12
    )
    assert_allclose(sampson_errors(F, first, second), 0, atol=1e-10)
    assert_allclose(sampson_errors(-1e-8 * F, first, second), 0, atol=1e-10)


def test_ransac_rejects_injected_outliers_and_is_repeatable():
    first, second, _, _ = stereo_points()
    rng = np.random.default_rng(7)
    first += rng.normal(0, 0.15, first.shape)
    second += rng.normal(0, 0.15, second.shape)
    second[-60:] = rng.uniform([100, 100], [1100, 850], (60, 2))
    result = estimate_fundamental(first, second, threshold_px=1.0, seed=17)
    repeat = estimate_fundamental(first, second, threshold_px=1.0, seed=17)
    assert result.failure is None
    assert np.linalg.matrix_rank(result.F) == 2
    assert result.inliers[:180].sum() >= 172
    assert result.inliers[-60:].sum() <= 3
    assert np.median(result.sampson_error_px[:180]) < 0.4
    assert_array_equal(result.inliers, repeat.inliers)
    assert_array_equal(result.F, repeat.F)


def test_rectification_maps_known_matches_to_visible_horizontal_scanlines():
    first, second, _, _ = stereo_points()
    result = estimate_fundamental(first, second, threshold_px=0.5)
    rect = rectify_matches(first, second, result, (1280, 960))
    assert_allclose(rect.first_points[:, 1], rect.second_points[:, 1], atol=0.001)
    # Robust viewport may clip extreme x disparities, but retains most support.
    for points in (rect.first_points, rect.second_points):
        visible = (points >= 0).all(axis=1) & (points < [1280, 960]).all(axis=1)
        assert visible.mean() >= 0.95
    transformed_F = np.linalg.inv(rect.H_second).T @ result.F @ np.linalg.inv(rect.H_first)
    assert_allclose(
        sampson_errors(transformed_F, rect.first_points, rect.second_points), 0, atol=0.001
    )


@pytest.mark.parametrize(
    "points", [np.empty((0, 2)), np.zeros((7, 2)), np.c_[np.arange(20), np.arange(20)]]
)
def test_insufficient_or_collinear_correspondences_fail_explicitly(points):
    result = estimate_fundamental(points, points)
    assert result.F is None
    assert not result.inliers.any()
    assert result.failure
    with pytest.raises(ValueError, match="verified F"):
        rectify_matches(points, points, result, (640, 480))


@pytest.mark.parametrize(
    "options",
    [
        {"threshold_px": 0},
        {"threshold_px": float("nan")},
        {"confidence": 1},
        {"max_iterations": 0},
        {"seed": -1},
    ],
)
def test_invalid_ransac_options(options):
    with pytest.raises(ValueError):
        estimate_fundamental(np.empty((0, 2)), np.empty((0, 2)), **options)


@pytest.mark.parametrize(
    "first,second",
    [([1, 2], [1, 2]), (np.zeros((8, 2)), np.zeros((9, 2))), ([[0, np.inf]], [[0, 0]])],
)
def test_invalid_correspondence_arrays(first, second):
    with pytest.raises(ValueError):
        estimate_fundamental(first, second)
