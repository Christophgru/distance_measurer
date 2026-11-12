from copy import deepcopy

import numpy as np
import pytest
from scipy.spatial.transform import Rotation

from distance_measuring.camera import CameraExtrinsics, CameraIntrinsics
from distance_measuring.scene import SceneCamera
from distance_measuring.vision.planar_measurement import (
    MeasurementPlane,
    check_plane_transfer,
    estimate_planar_point,
    fit_measurement_plane,
    intersect_measurement_plane,
)
from distance_measuring.vision.triangulation import triangulate_point


@pytest.fixture
def plane_case():
    k = CameraIntrinsics(1000, 1020, 799.5, 599.5, 1600, 1200)
    centers = [[-0.5, 0.1, 0.2], [0.7, -0.2, 0.4], [-0.8, 0.5, 0.1]]
    rotations = [[0.02, 0.03, -0.01], [0.01, -0.04, 0.02], [-0.03, 0.02, 0]]
    cameras = [
        SceneCamera(i, k, CameraExtrinsics(Rotation.from_rotvec(r).as_matrix(), c))
        for i, (r, c) in enumerate(zip(rotations, centers, strict=True))
    ]
    xy = np.random.default_rng(4).uniform([-1.0, -0.8], [1.0, 0.8], (60, 2))
    xyz = np.c_[xy, 6 + 0.2 * xy[:, 0] - 0.1 * xy[:, 1]]
    pixels = [c.intrinsics.project(xyz, c.extrinsics) for c in cameras]
    return cameras, xyz, pixels


def test_texture_only_fit_recovers_world_plane_with_noncanonical_cameras(plane_case):
    cameras, xyz, pixels = plane_case
    before = deepcopy([c.to_dict() for c in cameras])
    fit = fit_measurement_plane(cameras[0], cameras[1], pixels[0][:40], pixels[1][:40])
    assert fit.rejection_reason is None
    assert fit.symmetric_error_rmse_px < 1e-8
    np.testing.assert_allclose(xyz @ fit.plane.normal_w, fit.plane.offset_w, atol=1e-8)
    for camera, observations in zip(cameras, pixels, strict=True):
        point = intersect_measurement_plane(observations[45], camera, fit.plane)
        np.testing.assert_allclose(point.position, xyz[45], atol=1e-8)
    query = estimate_planar_point([p[45] for p in pixels], cameras, fit.plane)
    np.testing.assert_allclose(query.position, xyz[45], atol=1e-8)
    assert [c.to_dict() for c in cameras] == before
    reserved = check_plane_transfer(
        cameras[0], cameras[2], pixels[0][40:], pixels[2][40:], fit.plane
    )
    assert reserved["passes_80_percent_within_3px"]
    assert reserved["fraction_within_3px"] == 1.0


def test_unused_image_texture_detects_incorrect_shared_camera_geometry(plane_case):
    cameras, _, pixels = plane_case
    fit = fit_measurement_plane(cameras[0], cameras[1], pixels[0][:40], pixels[1][:40])
    wrong = SceneCamera(
        2,
        cameras[2].intrinsics,
        CameraExtrinsics(
            Rotation.from_rotvec([0, 0.06, 0]).as_matrix() @ cameras[2].extrinsics.R_cw,
            cameras[2].extrinsics.C_w,
        ),
    )
    report = check_plane_transfer(cameras[0], wrong, pixels[0][40:], pixels[2][40:], fit.plane)
    assert not report["passes_80_percent_within_3px"]
    assert report["fraction_within_3px"] == 0.0
    assert report["median_error_working_px"] > 20


def test_nonplanar_endpoint_is_rejected_without_dropping_a_view(plane_case):
    cameras, _, _ = plane_case
    plane = MeasurementPlane((-0.2, 0.1, 1), 6)
    object_point = [0.2, 0.3, 4.5]
    pixels = [c.intrinsics.project(object_point, c.extrinsics) for c in cameras]
    result = estimate_planar_point(pixels, cameras, plane)
    assert result.position is None
    assert result.rejection_reason == "high_reprojection_error"
    assert len(result.reprojection_errors_px) == 3


def test_plane_normalization_and_similar_world_frames_preserve_points(plane_case):
    cameras, xyz, pixels = plane_case
    plane = MeasurementPlane((-2, 1, 10), 60)
    rotation = Rotation.from_rotvec([0.2, -0.3, 0.1]).as_matrix()
    scale, translation = 12.0, np.array([100, -20, 35])
    n = rotation @ plane.normal_w
    moved_plane = MeasurementPlane(tuple(n), scale * plane.offset_w + n @ translation)
    moved = [
        SceneCamera(
            c.image_id,
            c.intrinsics,
            CameraExtrinsics(
                c.extrinsics.R_cw @ rotation.T, scale * rotation @ c.extrinsics.C_w + translation
            ),
        )
        for c in cameras
    ]
    result = estimate_planar_point([p[45] for p in pixels], moved, moved_plane)
    np.testing.assert_allclose(result.position, scale * rotation @ xyz[45] + translation, atol=1e-7)


def test_flat_surface_reduces_short_interval_depth_noise():
    k = CameraIntrinsics(1000, 1000, 799.5, 599.5, 1600, 1200)
    cameras = [
        SceneCamera(0, k, CameraExtrinsics(np.eye(3), [0, 0, 0])),
        SceneCamera(1, k, CameraExtrinsics(np.eye(3), [0.29, 0.58, 0.762])),
    ]
    plane = MeasurementPlane((0, 0, 1), 6.2)
    points = np.array([[x, 1.9, 6.2] for x in [0.85, 0.93, 1.13, 1.25]])
    pixels = np.array([[k.project(p, c.extrinsics) for c in cameras] for p in points])
    rng, free, constrained = np.random.default_rng(31), [], []
    for _ in range(50):
        noisy = pixels + rng.uniform(-0.78, 0.78, pixels.shape)
        ordinary = [triangulate_point(p, [k, k], [c.extrinsics for c in cameras]) for p in noisy]
        planar = [estimate_planar_point(p, cameras, plane) for p in noisy]
        if any(p.position is None for p in ordinary + planar):
            continue
        for result, values in [(ordinary, free), (planar, constrained)]:
            xyz = np.array([p.position for p in result])
            scale = 0.1 / np.linalg.norm(xyz[3] - xyz[0])
            values.append(np.linalg.norm(xyz[2] - xyz[1]) * scale)
    assert len(free) >= 40
    assert np.std(constrained) < np.std(free) / 3
    assert abs(np.mean(constrained) - 0.05) < 0.001


def test_rotation_only_cannot_fit_a_surface(plane_case):
    cameras, _, pixels = plane_case
    other = SceneCamera(1, cameras[0].intrinsics, cameras[0].extrinsics)
    fit = fit_measurement_plane(cameras[0], other, pixels[0], pixels[0])
    assert fit.plane is None and fit.rejection_reason == "no_translation"


def test_collinear_texture_cannot_define_plane(plane_case):
    cameras, _, _ = plane_case
    xyz = np.array([[x, 0, 6] for x in np.linspace(-1, 1, 20)])
    pixels = [c.intrinsics.project(xyz, c.extrinsics) for c in cameras]
    fit = fit_measurement_plane(cameras[0], cameras[1], pixels[0], pixels[1])
    assert fit.plane is None
    assert fit.rejection_reason == "surface_support_ill_conditioned"


def test_grazing_and_behind_camera_intersections_are_rejected():
    k = CameraIntrinsics(1000, 1000, 799.5, 599.5, 1600, 1200)
    camera = SceneCamera(0, k, CameraExtrinsics(np.eye(3), [0, 0, 0]))
    grazing = intersect_measurement_plane([799.5, 599.5], camera, MeasurementPlane((1, 0, 0), 1))
    assert grazing.position is None and grazing.rejection_reason == "grazing_plane_ray"
    behind = intersect_measurement_plane([799.5, 599.5], camera, MeasurementPlane((0, 0, 1), -1))
    assert behind.position is None
    assert behind.rejection_reason == "plane_intersection_behind_camera"


def test_wrong_shape_and_outside_observations_are_rejected(plane_case):
    cameras, _, pixels = plane_case
    with pytest.raises(ValueError, match="12 aligned"):
        fit_measurement_plane(cameras[0], cameras[1], pixels[0][:4], pixels[1][:4])
    wrong = pixels[0].copy()
    wrong[0, 0] = -1
    with pytest.raises(ValueError, match="outside"):
        fit_measurement_plane(cameras[0], cameras[1], wrong, pixels[1])
    with pytest.raises(ValueError, match="nonzero"):
        MeasurementPlane((0, 0, 0), 1)
