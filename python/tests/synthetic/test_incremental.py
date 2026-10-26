from dataclasses import replace

import cv2
import numpy as np
from numpy.testing import assert_allclose
from scipy.spatial.transform import Rotation

from distance_measuring.camera import CameraExtrinsics, CameraIntrinsics
from distance_measuring.scene import Observation, Scene, SceneCamera, ScenePoint, SceneTrack
from distance_measuring.vision.features import Features
from distance_measuring.vision.guided import GuidedConfig
from distance_measuring.vision.incremental import grow_landmarks, register_camera


def fixture():
    rng = np.random.default_rng(51)
    camera = CameraIntrinsics(800, 780, 639.5, 479.5, 1280, 960)
    world = rng.uniform([-1.5, -1.5, 6], [2, 1.5, 12], (80, 3))
    poses = [
        CameraExtrinsics(Rotation.from_rotvec([0, i * 0.01, 0]).as_matrix(), [i, 0, 0])
        for i in range(3)
    ]
    pixels = [camera.project(world, p) for p in poses]
    tracks = tuple(
        SceneTrack(
            1000 + i,
            tuple(Observation(k, 1000 + i, tuple(pixels[k][i]), 0) for k in range(2)),
            1000 + i,
        )
        for i in range(25)
    )
    points = tuple(ScenePoint(1000 + i, 1000 + i, tuple(p), 5) for i, p in enumerate(world[:25]))
    scene = Scene(
        tuple(SceneCamera(i, camera, p) for i, p in enumerate(poses[:2])),
        tracks,
        points,
        {
            "gauge": {"fixed_camera_id": 0, "first_baseline_length": 1},
            "scale_status": "arbitrary_visual_scale_unverified",
        },
    )
    return scene, world, camera, poses, pixels


def feature_set(points, descriptors):
    n = len(points)
    return Features(
        points,
        descriptors.astype(np.float32),
        np.ones(n),
        np.full(n, 21),
        np.zeros(n),
        np.zeros(n, np.int32),
    )


def test_incremental_registration_keeps_gauge_and_outlier_observations_out():
    scene, _, camera, poses, pixels = fixture()
    before = scene.to_dict()
    temporal = {1000 + i: pixel for i, pixel in enumerate(pixels[2][:25])}
    for i in range(5):
        temporal[1000 + i] = [50 + i * 50, 50]
    image = np.zeros((960, 1280), np.uint8)
    grown, report = register_camera(
        scene, 2, camera, image, {0: image, 1: image}, temporal, poses[2]
    )
    assert report["success"] and report["pnp"]["inlier_count"] >= 20
    assert scene.to_dict() == before
    assert_allclose(grown.cameras[-1].extrinsics.C_w, poses[2].C_w, atol=1e-6)
    assert_allclose(grown.cameras[0].extrinsics.R_cw, scene.cameras[0].extrinsics.R_cw)
    assert grown.metadata["gauge"] == scene.metadata["gauge"]
    assert all(len(t.observations) == (2 if t.track_id < 1005 else 3) for t in grown.tracks)


def test_insufficient_registration_preserves_the_original_scene():
    scene, _, camera, poses, _ = fixture()
    image = np.zeros((960, 1280), np.uint8)
    result, report = register_camera(scene, 2, camera, image, {0: image, 1: image}, {}, poses[2])
    assert result is scene and not report["success"]
    assert report["pnp"]["reason"] == "insufficient_3d_2d_correspondences"


def test_new_camera_grows_landmarks_from_corridors_without_global_matching():
    scene, world, camera, poses, pixels = fixture()
    image = np.zeros((960, 1280), np.uint8)
    registered, report = register_camera(
        scene,
        2,
        camera,
        image,
        {0: image, 1: image},
        {1000 + i: p for i, p in enumerate(pixels[2][:25])},
        poses[2],
    )
    assert report["success"]
    rng = np.random.default_rng(82)
    descriptors = rng.uniform(0, 1, (40, 128)).astype(np.float32)
    # Rectified horizontal baseline: distractors on each scanline provide the
    # second candidate for the reused descriptor ratio test.
    a = feature_set(pixels[1][30:70], descriptors)
    target = pixels[2][30:70]
    # Derive a distractor from a different depth on the SAME ray in camera 1.
    distractor_world = poses[1].C_w + 0.6 * (world[30:70] - poses[1].C_w)
    distractor = camera.project(distractor_world, poses[2])
    b = feature_set(np.r_[target, distractor], np.r_[descriptors, rng.uniform(2, 3, (40, 128))])
    before = registered.to_dict()
    grown, growth = grow_landmarks(registered, 1, 2, a, b, reserved_track_id=2000)
    assert registered.to_dict() == before
    assert growth["new_landmark_count"] >= 35
    assert len(grown.points) >= 60
    assert growth["epipolar_search"]["average_candidates_per_feature"] < len(b) / 3
    assert growth["track_conflict_count"] == 0
    original = {p.point_id: p for p in registered.points}
    for point in grown.points:
        quality = grown.metadata["landmark_quality"][str(point.point_id)]
        assert quality["observation_count"] >= 2
        assert (
            quality["best_triangulation_angle_degrees"]
            >= quality["median_triangulation_angle_degrees"]
            > 0
        )
        assert max(quality["reprojection_errors_px"]) <= 3
        if point.point_id in original:
            assert point.position == original[point.point_id].position
        else:
            assert point.point_id > 2000
            assert np.min(np.linalg.norm(world[30:70] - point.position, axis=1)) < 1e-6
    strict, failure = grow_landmarks(
        registered,
        1,
        2,
        a,
        b,
        reserved_track_id=2000,
        config=replace(GuidedConfig(), min_triangulation_angle_degrees=89),
    )
    assert failure["new_landmark_count"] == 0 and len(strict.points) == 25
    assert all(
        not row["accepted"] and row["reason"] == "poor_triangulation_angle"
        for row in failure["triangulations"]
    )


def test_rejected_tracks_need_new_guided_evidence_before_retriangulation():
    scene, _, _, _, pixels = fixture()
    rejected = SceneTrack(
        1500,
        tuple(Observation(i, 1500, tuple(pixels[i][25])) for i in range(2)),
        rejection_reason="essential_outlier",
    )
    scene = replace(scene, tracks=(*scene.tracks, rejected))
    empty = feature_set(np.empty((0, 2)), np.empty((0, 128)))
    grown, report = grow_landmarks(scene, 0, 1, empty, empty)
    assert len(grown.points) == len(scene.points) and not report["triangulations"]
    assert (
        next(t for t in grown.tracks if t.track_id == 1500).rejection_reason == "essential_outlier"
    )


def test_local_patches_can_supply_pnp_when_no_temporal_landmarks_survive():
    camera = CameraIntrinsics(400, 400, 319.5, 239.5, 640, 480)
    pixels = np.array([(x, y) for y in range(100, 381, 70) for x in range(100, 501, 100)], float)
    world = np.c_[camera.normalize_pixels(pixels), np.ones(len(pixels))] * 8
    cameras = tuple(
        SceneCamera(i, camera, CameraExtrinsics(np.eye(3), [x, 0, 0]))
        for i, x in enumerate([-0.5, 0])
    )
    tracks = tuple(
        SceneTrack(
            k,
            tuple(
                Observation(c.image_id, k, tuple(camera.project(p, c.extrinsics)), 0)
                for c in cameras
            ),
            k,
        )
        for k, p in enumerate(world)
    )
    scene = Scene(
        cameras, tracks, tuple(ScenePoint(k, k, tuple(p), 3) for k, p in enumerate(world))
    )
    image = np.random.default_rng(99).integers(0, 255, (480, 640), np.uint8)
    target = cv2.warpAffine(image, np.array([[1.0, 0, -10], [0, 1.0, 0]]), (640, 480))
    grown, report = register_camera(
        scene,
        2,
        camera,
        target,
        {1: image},
        {},
        CameraExtrinsics(np.eye(3), [0.16, 0, 0]),
        config=GuidedConfig(search_radius_px=8),
    )
    assert not report["temporal_pnp"]["success"]
    assert report["local_search"]["accepted_count"] == len(world)
    assert report["success"] and report["pnp"]["inlier_count"] == len(world)
    assert_allclose(grown.cameras[-1].extrinsics.C_w, [0.2, 0, 0], atol=1e-5)
