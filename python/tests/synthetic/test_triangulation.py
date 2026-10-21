import cv2
import numpy as np
import pytest
from numpy.testing import assert_allclose
from scipy.spatial.transform import Rotation

from distance_measuring.camera import CameraExtrinsics, CameraIntrinsics, project_matrix
from distance_measuring.vision.triangulation import triangulate_point


def cameras():
    intrinsics = [
        CameraIntrinsics(900 + 100 * i, 850 + 80 * i, 639.5, 479.5, 1280, 960, skew=1.5 * i)
        for i in range(3)
    ]
    poses = [
        CameraExtrinsics(np.eye(3), np.zeros(3)),
        CameraExtrinsics(
            Rotation.from_rotvec([0.02, -0.08, 0.01]).as_matrix(), np.array([0.8, 0.1, 0.02])
        ),
        CameraExtrinsics(
            Rotation.from_rotvec([-0.03, -0.12, -0.02]).as_matrix(), np.array([1.4, -0.1, 0.05])
        ),
    ]
    return intrinsics, poses


@pytest.mark.parametrize("solver", ["svd", "eigh"])
@pytest.mark.parametrize("view_count", [2, 3])
def test_known_3D_points_reconstruct_with_lecture_cameras(solver, view_count):
    intrinsics, poses = [items[:view_count] for items in cameras()]
    points = np.random.default_rng(39).uniform([-1, -1, 4], [1, 1, 9], (35, 3))
    for point in points:
        pixels = [
            camera.project(point, pose) for camera, pose in zip(intrinsics, poses, strict=True)
        ]
        result = triangulate_point(pixels, intrinsics, poses, solver=solver)
        assert result.rejection_reason is None
        assert_allclose(result.position, point, atol=1e-8)
        assert max(result.reprojection_errors_px) < 1e-8
        assert result.angle_degrees > 1
    if view_count == 2:
        # Independent homogeneous DLT oracle with the full 3x4 matrices M=K[R|O].
        one, two = [
            camera.project(points, pose) for camera, pose in zip(intrinsics, poses, strict=True)
        ]
        homogeneous = cv2.triangulatePoints(
            poses[0].projection_matrix(intrinsics[0].K),
            poses[1].projection_matrix(intrinsics[1].K),
            one.T,
            two.T,
        )
        assert_allclose((homogeneous[:3] / homogeneous[3]).T, points, atol=1e-9)


@pytest.mark.parametrize("scale", [0.003, 1000])
def test_similarity_change_preserves_reconstruction_and_angles(scale):
    intrinsics, poses = cameras()
    point = np.array([0.3, -0.2, 6])
    pixels = [camera.project(point, pose) for camera, pose in zip(intrinsics, poses, strict=True)]
    original = triangulate_point(pixels, intrinsics, poses)
    rotation = Rotation.from_rotvec([0.2, -0.4, 0.1]).as_matrix()
    offset = np.array([1e6, -2e6, 3e6])
    moved = [
        CameraExtrinsics(pose.R_cw @ rotation.T, scale * rotation @ pose.C_w + offset)
        for pose in poses
    ]
    result = triangulate_point(pixels, intrinsics, moved)
    assert result.rejection_reason is None
    assert_allclose(result.position, scale * rotation @ point + offset, atol=1e-6, rtol=0)
    assert result.angle_degrees == pytest.approx(original.angle_degrees, abs=1e-4)


def test_noisy_views_fit_close_to_truth_without_geometric_refinement():
    intrinsics, poses = cameras()
    rng = np.random.default_rng(18)
    for point in rng.uniform([-1, -1, 4], [1, 1, 8], (30, 3)):
        pixels = np.array(
            [camera.project(point, pose) for camera, pose in zip(intrinsics, poses, strict=True)]
        ) + rng.normal(0, 0.2, (3, 2))
        result = triangulate_point(pixels, intrinsics, poses)
        assert result.rejection_reason is None
        assert np.linalg.norm(np.array(result.position) - point) < 0.04
        assert max(result.reprojection_errors_px) < 0.9


def test_inconsistent_third_view_is_rejected_instead_of_dropped():
    intrinsics, poses = cameras()
    pixels = np.array(
        [
            camera.project([0.2, 0.1, 6], pose)
            for camera, pose in zip(intrinsics, poses, strict=True)
        ]
    )
    pixels[2] += [60, -40]
    result = triangulate_point(pixels, intrinsics, poses)
    assert result.position is None
    assert result.rejection_reason == "high_reprojection_error"
    assert len(result.reprojection_errors_px) == 3


def test_points_behind_any_camera_are_rejected():
    intrinsics, poses = cameras()
    poses[2] = CameraExtrinsics(
        Rotation.from_euler("y", 180, degrees=True).as_matrix(), poses[2].C_w
    )
    pixels = [
        project_matrix(pose.projection_matrix(camera.K), [0.2, 0.1, 6])
        for camera, pose in zip(intrinsics, poses, strict=True)
    ]
    result = triangulate_point(pixels, intrinsics, poses)
    assert result.position is None and result.rejection_reason == "behind_camera"


@pytest.mark.parametrize("opposed", [False, True])
def test_nearly_parallel_or_antiparallel_rays_are_rejected(opposed):
    camera = CameraIntrinsics(900, 900, 640, 480, 1280, 960)
    poses = [
        CameraExtrinsics(np.eye(3), np.zeros(3)),
        CameraExtrinsics(
            Rotation.from_euler("y", 180 if opposed else 0, degrees=True).as_matrix(),
            np.array([0, 0, 10]) if opposed else np.array([1e-5, 0, 0]),
        ),
    ]
    pixels = [camera.project([0.001, 0, 5], pose) for pose in poses]
    result = triangulate_point(pixels, [camera, camera], poses)
    assert result.position is None
    assert result.rejection_reason == "poor_triangulation_angle"


def test_coincident_cameras_and_point_at_infinity_fail_explicitly():
    camera = CameraIntrinsics(900, 900, 640, 480, 1280, 960)
    pose = CameraExtrinsics(np.eye(3), np.zeros(3))
    coincident = triangulate_point([[640, 480]] * 2, [camera] * 2, [pose] * 2)
    assert coincident.rejection_reason == "coincident_cameras"
    infinity = triangulate_point(
        [[640, 480]] * 2, [camera] * 2, [pose, CameraExtrinsics(np.eye(3), np.array([1, 0, 0]))]
    )
    assert infinity.rejection_reason == "point_at_infinity"


@pytest.mark.parametrize(
    "options",
    [
        {"max_reprojection_error_px": 0},
        {"max_reprojection_error_px": float("nan")},
        {"min_angle_degrees": 0},
        {"min_angle_degrees": 90},
        {"solver": "unknown"},
    ],
)
def test_bad_triangulation_configuration(options):
    intrinsics, poses = cameras()
    with pytest.raises(ValueError):
        triangulate_point([[640, 480]] * 3, intrinsics, poses, **options)


def test_wrong_coordinate_shape_or_distorted_camera_is_rejected():
    intrinsics, poses = cameras()
    for pixels in ([[1, 2]], [[1, 2, 3]] * 3, [[np.inf, 0]] * 3):
        with pytest.raises(ValueError):
            triangulate_point(pixels, intrinsics, poses)
    intrinsics[0] = CameraIntrinsics(900, 900, 640, 480, 1280, 960, distortion=(-0.1, 0, 0, 0, 0))
    with pytest.raises(ValueError, match="already-undistorted"):
        triangulate_point([[640, 480]] * 3, intrinsics, poses)
