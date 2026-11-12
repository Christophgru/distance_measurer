from dataclasses import replace

import cv2
import numpy as np
from numpy.testing import assert_allclose
from scipy.spatial.transform import Rotation

from distance_measuring.camera import CameraExtrinsics, CameraIntrinsics
from distance_measuring.scene import Observation, Scene, SceneCamera, ScenePoint, SceneTrack
from distance_measuring.vision.bootstrap import BootstrapPair
from distance_measuring.vision.features import Features
from distance_measuring.vision.guided import (
    GuidedConfig,
    project_landmarks,
    search_landmark_patches,
)
from distance_measuring.vision.incremental import grow_landmarks, register_camera
from distance_measuring.vision.pnp import estimate_pnp


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
    assert report["local_search"]["skipped_temporal_inlier_count"] == 20
    assert report["local_search"]["query_count"] == 5
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


def test_temporal_growth_preserves_ids_and_supplies_next_camera_without_descriptors():
    scene, world, camera, poses, pixels = fixture()
    empty = feature_set(np.empty((0, 2)), np.empty((0, 128)))
    history = {(i, 1000 + k): pixels[i][k] for i in range(2) for k in range(30, 70)}
    # A forward/backward-consistent track can still be geometrically wrong.
    history[1, 1030] = [50, 50]
    grown, report = grow_landmarks(
        scene, 0, 1, empty, empty, temporal_pixels=history, reserved_track_id=2000
    )
    assert report["temporal_candidate_count"] == 40
    assert report["new_landmark_count"] == 39
    assert 1030 not in {p.point_id for p in grown.points}
    assert set(range(1031, 1070)) <= {p.track_id for p in grown.points}
    for p in grown.points:
        if p.track_id >= 1031:
            assert_allclose(p.position, world[p.track_id - 1000], atol=1e-8)
    # Only the newly triangulated temporal tracks survive in frame 2.
    image = np.zeros((960, 1280), np.uint8)
    result, registration = register_camera(
        grown,
        2,
        camera,
        image,
        {0: image, 1: image},
        {1000 + k: pixels[2][k] for k in range(31, 70)},
        poses[2],
    )
    assert registration["success"] and registration["pnp"]["inlier_count"] == 39
    assert_allclose(result.cameras[-1].extrinsics.C_w, poses[2].C_w, atol=1e-6)


def test_same_temporal_pair_cannot_silently_reactivate_a_rejected_track():
    scene, _, _, _, pixels = fixture()
    rejected = SceneTrack(
        1500,
        tuple(Observation(i, 1500, tuple(pixels[i][25])) for i in range(2)),
        rejection_reason="essential_outlier",
    )
    scene = replace(scene, tracks=(*scene.tracks, rejected))
    empty = feature_set(np.empty((0, 2)), np.empty((0, 128)))
    grown, report = grow_landmarks(
        scene,
        0,
        1,
        empty,
        empty,
        temporal_pixels={(i, 1500): pixels[i][25] for i in range(2)},
        reserved_track_id=2000,
    )
    assert report["temporal_candidate_count"] == 0
    assert len(grown.points) == len(scene.points)
    assert next(t for t in grown.tracks if t.track_id == 1500) == rejected


def test_temporal_recovery_links_an_old_rejected_track_across_missing_views():
    scene, world, camera, poses, pixels = fixture()
    rejected = SceneTrack(
        1500,
        tuple(Observation(i, 1500, tuple(pixels[i][25])) for i in range(2)),
        rejection_reason="essential_outlier",
    )
    fourth = CameraExtrinsics(np.eye(3), [3, 0, 0])
    scene = replace(
        scene,
        cameras=(*scene.cameras, SceneCamera(2, camera, poses[2]), SceneCamera(3, camera, fourth)),
        tracks=(*scene.tracks, rejected),
    )
    empty = feature_set(np.empty((0, 2)), np.empty((0, 128)))
    recovered, _ = grow_landmarks(
        scene,
        2,
        3,
        empty,
        empty,
        temporal_pixels={(2, 1500): pixels[2][25], (3, 1500): camera.project(world[25], fourth)},
        reserved_track_id=2000,
    )
    assert len(recovered.points) == len(scene.points) + 1
    track = next(t for t in recovered.tracks if t.track_id == 1500)
    assert track.point_id == 1500
    assert [o.image_id for o in track.observations] == [0, 1, 2, 3]


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


def test_pose_depth_normalizes_rotated_scaled_patches_for_pnp():
    camera = CameraIntrinsics(400, 400, 319.5, 239.5, 640, 480)
    pixels = np.array([(x, y) for y in range(100, 381, 70) for x in range(160, 501, 85)], float)
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
    truth = CameraExtrinsics(Rotation.from_rotvec([0.06, -0.16, 0.12]).as_matrix(), [0.7, 0.1, 0.3])
    translation = -truth.R_cw @ truth.C_w
    H = camera.K @ (truth.R_cw + np.outer(translation, [0, 0, 1]) / 8) @ np.linalg.inv(camera.K)
    image = cv2.GaussianBlur(
        np.random.default_rng(80).integers(0, 255, (480, 640), np.uint8), (5, 5), 1
    )
    target = cv2.warpPerspective(image, H, (640, 480))
    config = GuidedConfig(search_radius_px=8)
    projections = project_landmarks(scene, camera, truth, normalize_patches=True, config=config)
    matches, report = search_landmark_patches(scene, {1: image}, target, projections, config=config)
    accepted = [r for r in matches if r["accepted"]]
    assert len(accepted) >= 12
    result = estimate_pnp(
        np.array([world[r["point_id"]] for r in accepted]),
        np.array([r["second_pixel_refined"] for r in accepted]),
        camera,
    )
    assert result.report["inlier_count"] >= 12
    assert report["candidate_fraction_of_exhaustive"] < 0.002
    assert_allclose(result.pose.C_w, truth.C_w, atol=0.02)
    assert_allclose(result.pose.R_cw, truth.R_cw, atol=0.003)
    _, rejected = search_landmark_patches(
        scene, {1: image}, np.zeros_like(target), projections, config=config
    )
    assert rejected["accepted_count"] == 0


def test_two_view_prior_recovers_a_camera_after_velocity_and_temporal_pnp_fail():
    rng = np.random.default_rng(2026)
    camera = CameraIntrinsics(400, 400, 319.5, 239.5, 640, 480)
    pixels = np.array([(x, y) for y in range(80, 401, 80) for x in range(180, 541, 90)], float)
    world = np.c_[camera.normalize_pixels(pixels), np.ones(len(pixels))]
    world *= rng.uniform(8, 12, len(world))[:, None]
    cameras = tuple(
        SceneCamera(i, camera, CameraExtrinsics(np.eye(3), [x, 0, 0]))
        for i, x in enumerate([-0.5, 0])
    )
    scene = Scene(
        cameras,
        tuple(
            SceneTrack(
                k,
                tuple(
                    Observation(c.image_id, k, tuple(camera.project(p, c.extrinsics)), 0)
                    for c in cameras
                ),
                k,
            )
            for k, p in enumerate(world)
        ),
        tuple(ScenePoint(k, k, tuple(p), 3) for k, p in enumerate(world)),
    )
    truth = CameraExtrinsics(np.eye(3), [0.5, 0, 0])
    target_pixels = camera.project(world, truth)
    image, target = [np.full((480, 640), 70, np.uint8) for _ in range(2)]
    for a, b in zip(pixels, target_pixels, strict=True):
        patch = rng.integers(0, 255, (31, 31), np.uint8)
        for im, xy in ((image, a), (target, b)):
            x, y = np.rint(xy).astype(int)
            im[y - 15 : y + 16, x - 15 : x + 16] = patch
    # Only five IDs are attached to mapped landmarks; other P2 tracks still
    # provide enough two-view motion to guide recovery of missing observations.
    ids = np.r_[np.arange(5), np.arange(100, 100 + len(world) - 5)]
    pair = BootstrapPair((1, 2), ids, pixels, target_pixels, camera, camera, np.zeros(len(ids)))
    temporal = {int(t): p for t, p in zip(ids, target_pixels, strict=True)}
    config = GuidedConfig(search_radius_px=8)
    coarse = CameraExtrinsics(np.eye(3), [2, 0, 0])
    recovered, report = register_camera(
        scene, 2, camera, target, {1: image}, temporal, coarse, motion_pair=pair, config=config
    )
    assert not report["temporal_pnp"]["success"]
    assert report["local_search"]["accepted_count"] == 0
    assert report["two_view_prior"]["available"]
    assert report["success"] and report["pnp"]["inlier_count"] >= 15
    assert len(report["pnp_attempts"]) == 3
    assert [r["success"] for r in report["pnp_attempts"]] == [False, False, True]
    assert report["pnp_runtime_s"] == sum(r["runtime_s"] for r in report["pnp_attempts"])
    assert_allclose(recovered.cameras[-1].extrinsics.C_w, truth.C_w, atol=0.03)
    for im, cfg in (
        (np.zeros_like(target), config),
        (target, replace(config, use_two_view_prior=False)),
    ):
        unchanged, rejected = register_camera(
            scene, 2, camera, im, {1: image}, temporal, coarse, motion_pair=pair, config=cfg
        )
        assert unchanged is scene and not rejected["success"]
