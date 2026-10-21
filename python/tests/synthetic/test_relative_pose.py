import cv2
import numpy as np
import pytest
from numpy.testing import assert_allclose, assert_array_equal
from scipy.spatial.transform import Rotation

from distance_measuring.camera import CameraExtrinsics, CameraIntrinsics
from distance_measuring.vision.pose import (
    EssentialEstimate,
    RelativePose,
    chain_camera,
    decompose_essential,
    essential_manifold,
    estimate_essential,
    estimate_essential_linear,
    recover_relative_pose,
)


def skew(t):
    x, y, z = t
    return np.array([[0, -z, y], [z, 0, -x], [-y, x, 0]])


def angle(a, b):
    return np.degrees(
        np.arccos(np.clip(np.dot(a, b) / np.linalg.norm(a) / np.linalg.norm(b), -1, 1))
    )


def rotation_error(a, b):
    return Rotation.from_matrix(a @ b.T).magnitude() * 180 / np.pi


def known_motion(*, baseline=0.7, count=240):
    rng = np.random.default_rng(51)
    points = rng.uniform([-2.0, -1.5, 4], [2.0, 1.5, 9], (count, 3))
    camera = CameraIntrinsics(950, 850, 639.5, 479.5, 1280, 960, skew=2)
    pose = CameraExtrinsics(
        Rotation.from_rotvec([0.04, -0.10, 0.02]).as_matrix(), baseline * np.array([1, 0.13, 0.04])
    )
    first = camera.normalize_pixels(camera.project(points))
    second = camera.normalize_pixels(camera.project(points, pose))
    expected = essential_manifold(skew(pose.O_cw) @ pose.R_cw)
    return first, second, expected, pose


def test_eigenvector_eight_point_recovers_E_up_to_scale_sign():
    first, second, expected, _ = known_motion()
    E = estimate_essential_linear(first, second)
    assert min(np.linalg.norm(E - expected), np.linalg.norm(E + expected)) < 1e-9
    assert_allclose(
        np.linalg.svd(E, compute_uv=False), [1 / np.sqrt(2), 1 / np.sqrt(2), 0], atol=1e-12
    )
    eight = estimate_essential_linear(first[:8], second[:8])
    assert min(np.linalg.norm(eight - expected), np.linalg.norm(eight + expected)) < 1e-7


@pytest.mark.parametrize("scale", [1, -3, 1e-200, -1e200])
def test_four_decompositions_and_cheirality_choose_correct_rotation_and_sign(scale):
    first, second, E, expected = known_motion()
    estimate = EssentialEstimate(scale * E, np.ones(len(first), bool), np.zeros(len(first)))
    hypotheses = decompose_essential(estimate.E)
    assert len(hypotheses) == 4
    for R, t in hypotheses:
        assert_allclose(R @ R.T, np.eye(3), atol=1e-12)
        assert np.linalg.det(R) == pytest.approx(1)
        assert np.linalg.norm(t) == pytest.approx(1)
        reconstructed = essential_manifold(skew(t) @ R)
        assert min(np.linalg.norm(reconstructed - E), np.linalg.norm(reconstructed + E)) < 1e-12
    result = recover_relative_pose(first, second, estimate)
    assert result.failure is None
    assert rotation_error(result.R, expected.R_cw) < 1e-6
    assert angle(result.t_direction, expected.O_cw) < 1e-6
    assert sum(result.cheirality_counts) == len(first)
    assert max(result.cheirality_counts) == len(first)
    assert result.inliers.all()
    # Independent OpenCV oracle; production selects the hypotheses explicitly.
    count, R, t, _ = cv2.recoverPose(E, first, second, np.eye(3))
    assert count == len(first)
    assert_allclose(result.R, R, atol=1e-12)
    assert_allclose(result.t_direction, t.ravel(), atol=1e-12)


def test_robust_calibrated_estimation_rejects_outliers_and_recovers_pose():
    first, second, _, expected = known_motion()
    rng = np.random.default_rng(98)
    first += rng.normal(0, 0.00015, first.shape)
    second += rng.normal(0, 0.00015, second.shape)
    second[-60:] = rng.uniform(-0.35, 0.35, (60, 2))
    estimate = estimate_essential(first, second, threshold_normalized=0.0008, seed=12)
    repeat = estimate_essential(first, second, threshold_normalized=0.0008, seed=12)
    result = recover_relative_pose(first, second, estimate)
    assert estimate.failure is None and result.failure is None
    assert estimate.inliers[:180].sum() >= 170
    assert estimate.inliers[-60:].sum() <= 3
    assert rotation_error(result.R, expected.R_cw) < 0.5
    assert angle(result.t_direction, expected.O_cw) < 2
    assert_array_equal(estimate.E, repeat.E)
    assert_array_equal(estimate.inliers, repeat.inliers)
    assert np.all(~result.inliers | estimate.inliers)


@pytest.mark.parametrize("baseline", [0, 1e-7])
def test_pure_rotation_and_tiny_parallax_reject_translation(baseline):
    first, second, _, _ = known_motion()
    rotation = Rotation.from_rotvec([0.04, -0.10, 0.02]).as_matrix()
    if baseline == 0:
        rays = np.c_[first, np.ones(len(first))] @ rotation.T
        second = rays[:, :2] / rays[:, 2:]
        E = essential_manifold(skew(np.array([1, 0, 0])) @ rotation)
    else:
        first, second, E, _ = known_motion(baseline=baseline)
    estimate = EssentialEstimate(E, np.ones(len(first), bool), np.zeros(len(first)))
    result = recover_relative_pose(first, second, estimate)
    assert result.R is None and result.t_direction is None
    assert not result.inliers.any()
    assert result.failure


def test_essential_mask_is_preserved_by_cheirality():
    first, second, E, _ = known_motion()
    mask = np.ones(len(first), bool)
    mask[::3] = False
    estimate = EssentialEstimate(E, mask, np.zeros(len(first)))
    pose = recover_relative_pose(first, second, estimate)
    assert_array_equal(pose.inliers, mask)
    assert max(pose.cheirality_counts) == mask.sum()


def test_chain_composes_external_transforms_and_distinguishes_C_from_t():
    previous = CameraExtrinsics(
        Rotation.from_rotvec([0.1, 0.2, -0.1]).as_matrix(), np.array([2, -1, 0.4])
    )
    R = Rotation.from_rotvec([-0.2, 0.08, 0.1]).as_matrix()
    t = np.array([0.6, -0.8, 0])
    relative = RelativePose(R, t, np.ones(12, bool), (12, 0, 0, 0), 5.0)
    for baseline in (1, 2.7):
        chained = chain_camera(previous, relative, baseline=baseline)
        step = np.eye(4)
        step[:3, :3], step[:3, 3] = R, baseline * t
        assert_allclose(chained.T_scene, step @ previous.T_scene, atol=1e-12)
        assert np.linalg.norm(chained.C_w - previous.C_w) == pytest.approx(baseline)
        assert_allclose(chained.C_w, -chained.R_cw.T @ chained.O_cw)


@pytest.mark.parametrize(
    "points", [np.empty((0, 2)), np.zeros((7, 2)), np.c_[np.arange(20), np.arange(20)]]
)
def test_insufficient_or_degenerate_essential_input(points):
    estimate = estimate_essential(points, points)
    assert estimate.E is None and estimate.failure
    assert not estimate.inliers.any()
    with pytest.raises(ValueError):
        estimate_essential_linear(points, points)


@pytest.mark.parametrize(
    "matrix", [np.zeros((3, 3)), np.eye(2), np.full((3, 3), np.nan), np.diag([1, 0, 0])]
)
def test_invalid_essential_matrices(matrix):
    with pytest.raises(ValueError):
        decompose_essential(matrix)


@pytest.mark.parametrize(
    "options",
    [
        {"threshold_normalized": 0},
        {"threshold_normalized": float("nan")},
        {"seed": -1},
        {"seed": True},
    ],
)
def test_invalid_estimator_options(options):
    with pytest.raises(ValueError):
        estimate_essential(np.empty((0, 2)), np.empty((0, 2)), **options)


@pytest.mark.parametrize(
    "options",
    [
        {"min_parallax_degrees": 0},
        {"min_parallax_degrees": float("nan")},
        {"max_reprojection_error_normalized": 0},
        {"min_positive_fraction": 0.5},
    ],
)
def test_invalid_recovery_options(options):
    first, second, E, _ = known_motion()
    with pytest.raises(ValueError):
        recover_relative_pose(
            first,
            second,
            EssentialEstimate(E, np.ones(len(first), bool), np.zeros(len(first))),
            **options,
        )
