from dataclasses import replace

import numpy as np
from numpy.testing import assert_allclose
from scipy.spatial.transform import Rotation

from distance_measuring.camera import CameraExtrinsics, CameraIntrinsics
from distance_measuring.scene import Observation, Scene, SceneCamera, ScenePoint, SceneTrack
from distance_measuring.vision.bootstrap import BootstrapPair
from distance_measuring.vision.pose_prior import tracked_motion_prior


def fixture():
    rng = np.random.default_rng(932)
    camera = CameraIntrinsics(800, 780, 639.5, 479.5, 1280, 960)
    world = rng.uniform([-1.5, -1.5, 6], [2, 1.5, 12], (80, 3))
    poses = [
        CameraExtrinsics(Rotation.from_rotvec([0, i * 0.02, 0.01]).as_matrix(), [i, 0, 0])
        for i in range(3)
    ]
    pixels = [camera.project(world, p) for p in poses]
    scene = Scene(
        tuple(SceneCamera(i, camera, poses[i]) for i in range(2)),
        tuple(
            SceneTrack(k, tuple(Observation(i, k, tuple(pixels[i][k]), 0) for i in range(2)), k)
            for k in range(25)
        ),
        tuple(ScenePoint(k, k, tuple(p), 5) for k, p in enumerate(world[:25])),
    )
    pair = BootstrapPair((1, 2), np.arange(80), pixels[1], pixels[2], camera, camera, np.zeros(80))
    return scene, pair, poses[2]


def test_three_anchors_align_two_view_scale_without_registering_a_camera():
    scene, pair, truth = fixture()
    before = scene.to_dict()
    pose, report = tracked_motion_prior(scene, pair, trusted_point_ids={0, 1, 2})
    assert report["available"] and report["supported_anchor_count"] == 3
    assert report["relative_pose"]["pose_inlier_count"] >= 70
    assert_allclose(pose.R_cw, truth.R_cw, atol=1e-7)
    assert_allclose(pose.C_w, truth.C_w, atol=1e-6)
    assert scene.to_dict() == before and len(scene.cameras) == 2


def test_two_anchors_and_missing_tracks_cannot_produce_a_prior():
    scene, pair, _ = fixture()
    pose, report = tracked_motion_prior(scene, pair, trusted_point_ids={0, 1})
    assert pose is None and report["reason"] == "insufficient_trusted_scale_anchors"
    short = replace(
        pair,
        track_ids=pair.track_ids[:5],
        first_pixels=pair.first_pixels[:5],
        second_pixels=pair.second_pixels[:5],
        residual_parallax_px=np.zeros(5),
    )
    pose, report = tracked_motion_prior(scene, short)
    assert pose is None and report["reason"] == "insufficient_two_view_tracks"


def test_inconsistent_existing_structure_cannot_supply_a_search_pose():
    scene, pair, _ = fixture()
    rng = np.random.default_rng(42)
    scene = replace(
        scene,
        points=tuple(
            replace(p, position=tuple(rng.uniform([-3, -3, 6], [3, 3, 12]))) for p in scene.points
        ),
    )
    pose, report = tracked_motion_prior(scene, pair, max_error_px=8)
    assert pose is None and report["reason"] == "inconsistent_translation_scale_anchors"
