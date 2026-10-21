import numpy as np
import pytest
from numpy.testing import assert_allclose
from scipy.spatial.transform import Rotation

from distance_measuring.camera import (
    CameraExtrinsics,
    CameraIntrinsics,
    from_homogeneous,
    to_homogeneous,
)
from distance_measuring.camera.calibration import (
    calibrate_3d,
    calibrate_planar_views,
    checkerboard_points,
    decompose_projection_matrix,
    estimate_homography,
    estimate_projection_matrix,
)


def known_camera():
    camera = CameraIntrinsics(1000, 980, 640, 480, 1280, 960, skew=1.5)
    pose = CameraExtrinsics(
        Rotation.from_rotvec([0.1, -0.2, 0.05]).as_matrix(), np.array([0.1, 0.2, -4.0])
    )
    return camera, pose


@pytest.mark.parametrize("solver", ["eigh", "svd"])
def test_lecture_dlt_recovers_internal_and_external_parameters(solver):
    camera, pose = known_camera()
    points = np.random.default_rng(37).uniform(-1, 1, (60, 3))
    result = calibrate_3d(points, camera.project(points, pose), camera.image_size, solver=solver)
    assert_allclose(result.intrinsics.K, camera.K, atol=1e-7)
    assert_allclose(result.extrinsics[0].R_cw, pose.R_cw, atol=1e-9)
    assert_allclose(result.extrinsics[0].C_w, pose.C_w, atol=1e-9)
    assert result.rmse_px < 1e-7
    assert result.diagnostics["solver"] == solver
    assert len(result.diagnostics["eigenvalues"]) == 12


@pytest.mark.parametrize("scale", [-100.0, 0.03, 1e-8, -1e-150, 1e150])
def test_RQ_sign_and_projective_scale(scale):
    camera, pose = known_camera()
    recovered_K, recovered_pose = decompose_projection_matrix(
        scale * pose.projection_matrix(camera.K), camera.image_size
    )
    assert_allclose(recovered_K.K, camera.K, atol=1e-8)
    assert_allclose(recovered_pose.R_cw, pose.R_cw, atol=1e-10)
    assert_allclose(recovered_pose.C_w, pose.C_w, atol=1e-10)
    assert np.linalg.det(recovered_pose.R_cw) == pytest.approx(1)


def test_normalization_handles_different_world_units_and_offset():
    camera, pose = known_camera()
    original = np.random.default_rng(13).uniform(-1, 1, (60, 3))
    offset = np.array([10000.0, -20000.0, 15000.0])
    points = original * 1000 + offset
    scaled_pose = CameraExtrinsics(pose.R_cw, pose.C_w * 1000 + offset)
    result = calibrate_3d(points, camera.project(original, pose), camera.image_size)
    assert_allclose(result.intrinsics.K, camera.K, atol=1e-6)
    assert_allclose(result.extrinsics[0].C_w, scaled_pose.C_w, atol=1e-6)


def test_dlt_noisy_correspondences_reproject():
    camera, pose = known_camera()
    rng = np.random.default_rng(12)
    points = rng.uniform(-1, 1, (100, 3))
    pixels = camera.project(points, pose) + rng.normal(0, 0.1, (100, 2))
    result = calibrate_3d(points, pixels, camera.image_size)
    assert result.rmse_px < 0.2
    assert_allclose(result.intrinsics.K, camera.K, atol=3)


def test_coplanar_DLT_is_rejected_as_in_lecture():
    board = checkerboard_points(8, 6, 0.025)
    with pytest.raises(ValueError, match="coplanar"):
        estimate_projection_matrix(board, board[:, :2])


def test_insufficient_or_repeated_DLT_points_fail():
    with pytest.raises(ValueError, match="six"):
        estimate_projection_matrix(np.zeros((5, 3)), np.zeros((5, 2)))
    with pytest.raises(ValueError, match="coplanar"):
        estimate_projection_matrix(np.zeros((8, 3)), np.zeros((8, 2)))


@pytest.mark.parametrize("solver", ["eigh", "svd"])
def test_four_point_homography_and_homogeneous_division(solver):
    points = np.array([[0.0, 0.0], [1, 0], [1, 1], [0, 1]])
    expected = np.array([[500.0, 10.0, 200.0], [3.0, 450.0, 100.0], [0.1, 0.2, 1.0]])
    pixels = from_homogeneous(to_homogeneous(points) @ expected.T)
    H, _ = estimate_homography(points, pixels, solver=solver)
    assert_allclose(from_homogeneous(to_homogeneous(points) @ H.T), pixels, atol=1e-7)


POSES = [
    ([0.3, -0.2, 0.1], [-0.1, -0.08, 0.65]),
    ([-0.3, 0.15, -0.1], [-0.07, -0.06, 0.75]),
    ([0.15, 0.35, 0.2], [-0.12, -0.09, 0.6]),
    ([-0.25, -0.3, 0.05], [-0.04, -0.08, 0.8]),
    ([0.4, 0.1, -0.1], [-0.1, -0.03, 0.58]),
    ([-0.15, 0.45, 0.15], [-0.05, -0.07, 0.7]),
]


def planar_observations(camera, board):
    poses, observations = [], []
    for rvec, translation in POSES:
        R_cw = Rotation.from_rotvec(rvec).as_matrix()
        pose = CameraExtrinsics(R_cw, -R_cw.T @ np.array(translation))
        poses.append(pose)
        observations.append(camera.project(board, pose))
    return poses, observations


@pytest.mark.parametrize("solver", ["eigh", "svd"])
def test_planar_eigen_initialization_recovers_K_and_all_extrinsics(solver):
    camera = CameraIntrinsics(1000, 980, 640, 480, 1280, 960, skew=1.5)
    board = checkerboard_points(8, 6, 0.025)
    poses, observations = planar_observations(camera, board)
    result = calibrate_planar_views(
        board, observations, camera.image_size, solver=solver, refine=False, fit_skew=True
    )
    assert_allclose(result.intrinsics.K, camera.K, atol=1e-6)
    for expected, actual in zip(poses, result.extrinsics, strict=True):
        assert_allclose(actual.R_cw, expected.R_cw, atol=1e-8)
        assert_allclose(actual.C_w, expected.C_w, atol=1e-8)
    assert result.rmse_px < 1e-6


def test_distorted_noisy_planar_calibration_refines_reprojection_error():
    camera = CameraIntrinsics(
        1000, 980, 640, 480, 1280, 960, distortion=(-0.06, 0.015, 0.001, -0.001, 0)
    )
    board = checkerboard_points(8, 6, 0.025)
    _, observations = planar_observations(camera, board)
    rng = np.random.default_rng(37)
    observations = [pixels + rng.normal(0, 0.03, pixels.shape) for pixels in observations]
    result = calibrate_planar_views(board, observations, camera.image_size)
    assert result.rmse_px < result.initial_rmse_px
    assert result.rmse_px < 0.06
    assert_allclose([result.intrinsics.fx, result.intrinsics.fy], [1000, 980], rtol=0.01)
    assert result.diagnostics["optimization"]["success"]


def test_repeated_flat_views_do_not_calibrate():
    board = checkerboard_points(8, 6, 0.025)
    camera = CameraIntrinsics(1000, 980, 640, 480, 1280, 960)
    _, observations = planar_observations(camera, board)
    with pytest.raises(ValueError, match="Degenerate"):
        calibrate_planar_views(board, [observations[0]] * 4, camera.image_size)


def test_planar_calibration_requires_multiple_views():
    board = checkerboard_points(8, 6, 0.025)
    with pytest.raises(ValueError, match="three"):
        calibrate_planar_views(board, [board[:, :2]] * 2, (1280, 960))


@pytest.mark.parametrize("columns,rows,square", [(1, 6, 0.02), (8, 1, 0.02), (8, 6, 0), (8, 6, -1)])
def test_invalid_checkerboard_definition(columns, rows, square):
    with pytest.raises(ValueError):
        checkerboard_points(columns, rows, square)
