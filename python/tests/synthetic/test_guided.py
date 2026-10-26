from dataclasses import replace

import cv2
import numpy as np
import pytest
from numpy.testing import assert_allclose
from scipy.spatial.transform import Rotation

from distance_measuring.camera import CameraExtrinsics, CameraIntrinsics
from distance_measuring.scene import Observation, Scene, SceneCamera, ScenePoint, SceneTrack
from distance_measuring.vision.features import Features
from distance_measuring.vision.guided import (
    GuidedConfig,
    epipolar_search,
    fundamental_from_cameras,
    predict_pose,
    project_landmarks,
    search_landmark_patches,
)


def features(points, descriptors):
    n = len(points)
    return Features(
        np.asarray(points),
        np.asarray(descriptors, np.float32),
        np.ones(n),
        np.full(n, 21),
        np.zeros(n),
        np.zeros(n, np.int32),
    )


def patch_scene():
    camera = CameraIntrinsics(400, 400, 319.5, 239.5, 640, 480)
    cameras = tuple(
        SceneCamera(i, camera, CameraExtrinsics(np.eye(3), [x, 0, 0]))
        for i, x in enumerate([-0.5, 0])
    )
    world = np.array([[-1, -1, 8], [0, 0, 8], [1, 1, 8]])
    tracks = tuple(
        SceneTrack(
            i,
            tuple(
                Observation(c.image_id, i, tuple(camera.project(p, c.extrinsics)), 0)
                for c in cameras
            ),
            i,
        )
        for i, p in enumerate(world)
    )
    scene = Scene(
        cameras, tracks, tuple(ScenePoint(i, i, tuple(p), 3) for i, p in enumerate(world))
    )
    image = np.random.default_rng(14).integers(0, 255, (480, 640), np.uint8)
    return scene, camera, image


def test_projection_records_visibility_counts_and_radius():
    scene, camera, _ = patch_scene()
    pose = CameraExtrinsics(np.eye(3), [0.2, 0, 0])
    rows = project_landmarks(scene, camera, pose)
    assert all(r["visible"] and r["positive_depth"] for r in rows)
    assert all(r["observation_count"] == 2 and r["search_radius_px"] == 32 for r in rows)
    assert_allclose(rows[1]["predicted_pixel"], camera.project(scene.points[1].position, pose))
    behind = project_landmarks(scene, camera, CameraExtrinsics(np.eye(3), [0, 0, 10]))
    assert all(not r["visible"] and r["predicted_pixel"] is None for r in behind)
    outside = project_landmarks(scene, camera, CameraExtrinsics(np.eye(3), [20, 0, 0]))
    assert all(not r["visible"] and r["positive_depth"] for r in outside)


def test_bounded_patch_search_recovers_known_motion_and_reduces_candidate_count():
    scene, camera, image = patch_scene()
    target = cv2.warpAffine(image, np.array([[1.0, 0, -10], [0, 1.0, 0]]), (640, 480))
    config = GuidedConfig(search_radius_px=8)
    # Coarse pose is two pixels off, allowing the image evidence to correct it.
    rows = project_landmarks(
        scene, camera, CameraExtrinsics(np.eye(3), [0.16, 0, 0]), config=config
    )
    found, report = search_landmark_patches(scene, {1: image}, target, rows, config=config)
    assert report["accepted_count"] == 3
    assert report["average_candidates_per_landmark"] == 17**2
    assert report["candidate_fraction_of_exhaustive"] < 0.002
    assert report["reverse_candidates_examined"] == 3 * 17**2
    for row, point in zip(found, scene.points, strict=True):
        expected = camera.project(point.position, CameraExtrinsics(np.eye(3), [0.2, 0, 0]))
        assert_allclose(row["second_pixel_refined"], expected, atol=1e-8)


def test_no_convincing_patch_does_not_produce_a_correspondence():
    scene, camera, image = patch_scene()
    rows = project_landmarks(scene, camera, scene.cameras[-1].extrinsics)
    found, report = search_landmark_patches(scene, {1: image}, np.zeros_like(image), rows)
    assert report["accepted_count"] == 0 and not any(r["accepted"] for r in found)
    assert all(r["reason"] == "textureless_search" for r in found)


def test_gyro_prior_requires_consistency_with_visual_prediction():
    first = CameraExtrinsics(np.eye(3), [0, 0, 0])
    latest = CameraExtrinsics(Rotation.from_rotvec([0, 0.02, 0]).as_matrix(), [1, 0, 0])
    good = Rotation.from_rotvec([0, 0.025, 0]).as_matrix()
    pose, report = predict_pose(first, latest, 1, gyro_rotation=good)
    assert report["gyro_used"]
    assert_allclose(pose.R_cw, good @ latest.R_cw)
    assert_allclose(pose.C_w, [2, 0, 0])
    bad = Rotation.from_rotvec([1, 0, 0]).as_matrix()
    pose, report = predict_pose(first, latest, 1, gyro_rotation=bad)
    assert not report["gyro_used"] and report["gyro_rejection_reason"]
    assert_allclose(pose.R_cw, latest.R_cw @ latest.R_cw)


def test_epipolar_corridor_matches_known_points_with_unequal_intrinsics():
    rng = np.random.default_rng(45)
    a = SceneCamera(
        0,
        CameraIntrinsics(800, 750, 639.5, 479.5, 1280, 960, skew=3),
        CameraExtrinsics(np.eye(3), [0, 0, 0]),
    )
    b = SceneCamera(
        1,
        CameraIntrinsics(820, 780, 639.5, 479.5, 1280, 960, skew=-2),
        CameraExtrinsics(Rotation.from_rotvec([0.02, -0.04, 0.01]).as_matrix(), [0.8, 0.1, 0]),
    )
    world = rng.uniform([-2, -1.5, 5], [2, 1.5, 10], (40, 3))
    first, second = a.intrinsics.project(world), b.intrinsics.project(world, b.extrinsics)
    descriptors = rng.uniform(0, 1, (40, 128)).astype(np.float32)
    # Add wrong descriptors directly along each true epipolar line, so the ratio
    # check always has a second candidate without comparing the whole image.
    matrix = fundamental_from_cameras(a, b)
    lines = np.c_[first, np.ones(40)] @ matrix.T
    distractors = second + np.c_[np.full(40, 30), -30 * lines[:, 0] / lines[:, 1]]
    f = features(first, descriptors)
    g = features(np.r_[second, distractors], np.r_[descriptors, rng.uniform(2, 3, (40, 128))])
    matches, report = epipolar_search(f, g, a, b)
    assert [(i, j) for i, j, _ in matches] == [(i, i) for i in range(40)]
    assert report["average_candidates_per_feature"] < len(g) / 4
    assert all(r["distance_px"] < 1e-9 for r in report["queries"])
    # Pure rotation supplies no triangulation baseline/corridor.
    matches, report = epipolar_search(f, g, a, replace(b, extrinsics=a.extrinsics))
    assert not matches and report["reason"] == "coincident_camera_centers"


@pytest.mark.parametrize(
    "options",
    [
        {"search_radius_px": 0},
        {"patch_size": 4},
        {"descriptor_ratio": 1},
        {"corridor_px": float("nan")},
    ],
)
def test_invalid_search_configuration_is_rejected(options):
    with pytest.raises(ValueError):
        GuidedConfig(**options)


def test_epipolar_search_never_matches_outside_target_image_bounds():
    camera = CameraIntrinsics(400, 400, 319.5, 239.5, 640, 480)
    a = SceneCamera(0, camera, CameraExtrinsics(np.eye(3), [0, 0, 0]))
    b = SceneCamera(1, camera, CameraExtrinsics(np.eye(3), [1, 0, 0]))
    descriptor = np.ones((1, 128), np.float32)
    first = features(np.array([[100.0, 200.0]]), descriptor)
    second = features(
        np.array([[-10.0, 200.0], [80.0, 200.0], [180.0, 200.0]]),
        np.r_[descriptor, descriptor + 0.01, descriptor + 1],
    )
    matches, report = epipolar_search(first, second, a, b)
    assert [(i, j) for i, j, _ in matches] == [(0, 1)]
    assert report["candidates_examined"] == 2
