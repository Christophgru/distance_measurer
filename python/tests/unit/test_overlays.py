import numpy as np
import pytest
from numpy.testing import assert_allclose, assert_array_equal
from scipy.spatial.transform import Rotation

from distance_measuring.camera import CameraExtrinsics, CameraIntrinsics
from distance_measuring.scene import SceneCamera
from distance_measuring.visualization.overlays import pose_rectification, red_blue_overlay


def test_red_blue_channels_preserve_intensity_without_blending_or_swapping():
    a = np.array([[0, 255], [80, 60]], dtype=np.uint8)
    b = np.array([[255, 0], [80, 20]], dtype=np.uint8)
    rgb = red_blue_overlay(a, b)
    assert_array_equal(rgb[..., 0], a)
    assert_array_equal(rgb[..., 1], 0)
    assert_array_equal(rgb[..., 2], b)
    assert_array_equal(rgb[1, 0], [80, 0, 80])


@pytest.mark.parametrize("baseline", [[1, 0.1, 0], [0.1, 1, 0]])
def test_pose_rectification_places_clean_correspondences_on_common_scanlines(baseline):
    K = CameraIntrinsics.from_K([[650, 0, 399.5], [0, 680, 299.5], [0, 0, 1]], (800, 600))
    R1 = Rotation.from_euler("xyz", [12, -9, 6], degrees=True).as_matrix()
    R2 = Rotation.from_euler("xyz", [15, -12, 10], degrees=True).as_matrix()
    center = np.array([8, -3, 5])
    a = SceneCamera(0, K, CameraExtrinsics(R1, center))
    b = SceneCamera(1, K, CameraExtrinsics(R2, center + np.array(baseline) @ R1))
    points = a.extrinsics.camera_to_world([[0.2, -0.4, 7], [-1, 0.1, 5], [0.5, 0.7, 9]])
    H1, H2, direction = pose_rectification(a, b)
    rectified = []
    for camera, H in [(a, H1), (b, H2)]:
        pixels = camera.intrinsics.project(points, camera.extrinsics, distort=False)
        homogeneous = np.c_[pixels, np.ones(len(pixels))] @ H.T
        rectified.append(homogeneous[:, :2] / homogeneous[:, 2:])
    coordinate = 1 if direction == "horizontal" else 0
    assert_allclose(rectified[0][:, coordinate], rectified[1][:, coordinate], atol=1e-9)
    assert direction == ("horizontal" if baseline[0] > baseline[1] else "vertical")


def test_coincident_camera_centers_do_not_create_fake_rectification(bundle_scene_factory):
    scene, _, _ = bundle_scene_factory(count=12)
    camera = scene.cameras[0]
    with pytest.raises(ValueError, match="distinct camera centers"):
        pose_rectification(camera, camera)


@pytest.mark.parametrize(
    "a,b",
    [
        (np.zeros((2, 2), np.uint8), np.zeros((2, 3), np.uint8)),
        (np.zeros((2, 2)), np.zeros((2, 2))),
        (np.zeros((2, 2, 3), np.uint8), np.zeros((2, 2, 3), np.uint8)),
    ],
)
def test_invalid_overlay_shapes_and_types_fail(a, b):
    with pytest.raises(ValueError):
        red_blue_overlay(a, b)
