from dataclasses import replace

import numpy as np
import pytest
from numpy.testing import assert_allclose
from scipy.spatial.transform import Rotation

from distance_measuring.camera import CameraExtrinsics, CameraIntrinsics
from distance_measuring.dataset.schema import ImageMetadata
from distance_measuring.inertial.rotation import camera_ray_rotation
from distance_measuring.vision.parallax import rotation_compensated_parallax


def cameras():
    return (
        CameraIntrinsics(700, 680, 399.5, 299.5, 800, 600, skew=2),
        CameraIntrinsics(760, 710, 405, 290, 820, 620, skew=-1),
    )


def test_rotating_known_scene_has_zero_residual_with_different_intrinsics():
    a, b = cameras()
    world = np.random.default_rng(17).uniform([-1, -0.5, 4], [1, 0.5, 8], (80, 3))
    rotation = Rotation.from_rotvec([0.05, -0.1, 0.02]).as_matrix()
    first = a.project(world)
    second = b.project(world, CameraExtrinsics(rotation, np.zeros(3)))
    result = rotation_compensated_parallax(first, second, a, b, rotation)
    assert result.valid.all() and result.predicted_in_bounds.all()
    assert np.median(result.parallax_px) < 1e-10
    assert_allclose(result.predicted_pixels, second, atol=1e-10)


def test_translated_scene_has_depth_dependent_pixel_parallax():
    camera = CameraIntrinsics(700, 680, 399.5, 299.5, 800, 600)
    world = np.random.default_rng(17).uniform([-1, -0.5, 4], [1, 0.5, 8], (80, 3))
    first = camera.project(world)
    second = camera.project(world, CameraExtrinsics(np.eye(3), np.array([0.2, 0, 0])))
    result = rotation_compensated_parallax(first, second, camera, camera, np.eye(3))
    assert np.median(result.parallax_px) > 15
    assert_allclose(result.parallax_px, 700 * 0.2 / world[:, 2], atol=1e-10)
    assert (result.residual_vectors_px[:, 0] < 0).all()


@pytest.mark.parametrize(
    "axis,source,expected_sign",
    [
        ("x", [400, 300], [1, 0]),
        ("y", [400, 300], [0, -1]),
        ("z", [480, 300], [-1, 1]),
    ],
)
def test_device_pitch_yaw_roll_predict_expected_stored_pixel_motion(axis, source, expected_sign):
    metadata = ImageMetadata(
        camera_id="0",
        lens_facing=1,
        sensor_orientation_degrees=90,
        target_rotation=0,
        is_flipped=False,
    )
    camera = CameraIntrinsics(700, 700, 400, 300, 800, 600)
    passive = Rotation.from_euler(axis, -0.1).as_matrix()
    rotation = camera_ray_rotation(passive, metadata, metadata)
    result = rotation_compensated_parallax([source], [source], camera, camera, rotation)
    delta = result.predicted_pixels[0] - source
    assert_allclose(np.sign(np.where(abs(delta) < 1e-10, 0, delta)), expected_sign)


def test_horizon_or_behind_camera_rays_are_invalid_instead_of_huge_parallax():
    a, b = cameras()
    for angle in (90, 180):
        result = rotation_compensated_parallax(
            [[a.cx, a.cy]],
            [[b.cx, b.cy]],
            a,
            b,
            Rotation.from_euler("y", angle, degrees=True).as_matrix(),
        )
        assert not result.valid[0] and not result.predicted_in_bounds[0]
        assert np.isnan(result.parallax_px[0])


def test_offscreen_forward_prediction_is_reported_separately_and_empty_arrays_work():
    a, b = cameras()
    result = rotation_compensated_parallax(
        [[790, 300]], [[400, 300]], a, b, Rotation.from_euler("y", 30, degrees=True).as_matrix()
    )
    assert result.valid[0] and not result.predicted_in_bounds[0]
    empty = rotation_compensated_parallax(np.empty((0, 2)), np.empty((0, 2)), a, b, np.eye(3))
    assert empty.predicted_pixels.shape == (0, 2) and empty.parallax_px.shape == (0,)


def test_distortion_misaligned_points_and_invalid_rotations_are_rejected():
    a, b = cameras()
    with pytest.raises(ValueError, match="ideal"):
        rotation_compensated_parallax(
            [[2, 3]], [[4, 5]], replace(a, distortion=(0.1, 0, 0, 0, 0)), b, np.eye(3)
        )
    for first, second, rotation in (
        ([[1, 2]], [], np.eye(3)),
        ([[1, 2]], [[3, 4]], np.zeros((3, 3))),
    ):
        with pytest.raises(ValueError):
            rotation_compensated_parallax(first, second, a, b, rotation)
