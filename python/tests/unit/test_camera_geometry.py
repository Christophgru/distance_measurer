import numpy as np
import pytest
from numpy.testing import assert_allclose
from scipy.spatial.transform import Rotation

from distance_measuring.camera import (
    CameraExtrinsics,
    CameraIntrinsics,
    from_homogeneous,
    project_matrix,
    to_homogeneous,
)
from distance_measuring.camera.geometry import P0


def test_homogeneous_round_trip_and_individual_scales():
    points = np.array([[2.0, -3.0, 5.0], [1.0, 4.0, 2.0]])
    homogeneous = to_homogeneous(points)
    assert_allclose(homogeneous[:, -1], 1)
    assert_allclose(from_homogeneous(homogeneous * np.array([2.0, -7.0])[:, None]), points)
    assert_allclose(from_homogeneous(to_homogeneous([2.0, 4.0])), [2.0, 4.0])


@pytest.mark.parametrize("point", [[1, 2, 0], [0, 0, 0]])
def test_point_at_infinity_cannot_be_cartesian(point):
    with pytest.raises(ValueError, match="infinity"):
        from_homogeneous(point)


def test_world_camera_transform_and_camera_center():
    R_cw = np.array([[0.0, -1.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0]])
    pose = CameraExtrinsics(R_cw, np.array([1.0, 2.0, 3.0]))
    P_w = np.array([[2.0, 2.0, 5.0], [1.0, 3.0, 7.0]])
    assert_allclose(pose.world_to_camera(P_w), [[0.0, 1.0, 2.0], [-1.0, 0.0, 4.0]])
    assert_allclose(pose.camera_to_world(pose.world_to_camera(P_w)), P_w)
    assert_allclose(pose.world_to_camera(pose.C_w), 0)
    assert_allclose(pose.O_cw, [2.0, -1.0, -3.0])
    assert_allclose(
        from_homogeneous(to_homogeneous(P_w) @ pose.T_scene.T), pose.world_to_camera(P_w)
    )


def test_lecture_factorization_and_nullspace():
    intrinsics = CameraIntrinsics(800, 810, 320, 240, 640, 480, skew=2)
    pose = CameraExtrinsics(
        Rotation.from_rotvec([0.1, -0.2, 0.05]).as_matrix(), np.array([0.2, 0.1, -3.0])
    )
    M = pose.projection_matrix(intrinsics.K)
    assert M.shape == (3, 4)
    assert np.linalg.matrix_rank(M) == 3
    assert_allclose(M, intrinsics.K @ P0 @ pose.T_scene)
    assert_allclose(M, intrinsics.K @ np.column_stack((pose.R_cw, -pose.R_cw @ pose.C_w)))
    assert_allclose(M @ to_homogeneous(pose.C_w), 0, atol=1e-10)
    points = [[0.2, 0.3, 1.0], [1.0, -0.5, 2.0]]
    assert_allclose(project_matrix(M, points), intrinsics.project(points, pose))
    for scale in (-10.0, 3.0, 1e-8):
        assert_allclose(project_matrix(scale * M, points), project_matrix(M, points))


def test_known_projection_and_depth():
    camera = CameraIntrinsics(100, 200, 20, 30, 80, 60)
    assert_allclose(camera.project([[1.0, 2.0, 10.0], [0, 0, 5.0]]), [[30, 70], [20, 30]])
    with pytest.raises(ValueError, match="front"):
        camera.project([[0, 0, 0]])
    with pytest.raises(ValueError, match="front"):
        camera.project([[0, 0, -1]])


@pytest.mark.parametrize("rotation", [np.zeros((3, 3)), np.diag([1, 1, -1]), np.eye(2)])
def test_external_rotation_must_be_proper(rotation):
    with pytest.raises(ValueError, match="R_cw"):
        CameraExtrinsics(rotation, np.zeros(3))


def test_extrinsics_copy_input_arrays():
    rotation, center = np.eye(3), np.zeros(3)
    pose = CameraExtrinsics(rotation, center)
    center[0] = 9
    rotation[0, 0] = 5
    assert_allclose(pose.C_w, 0)
    assert_allclose(pose.R_cw, np.eye(3))
