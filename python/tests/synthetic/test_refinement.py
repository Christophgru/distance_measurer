from dataclasses import replace

import cv2
import numpy as np
import pytest
from numpy.testing import assert_allclose

from distance_measuring.camera import CameraExtrinsics, CameraIntrinsics
from distance_measuring.optimization.local_bundle import LocalBundleConfig
from distance_measuring.scene import Observation, Scene, SceneCamera, ScenePoint, SceneTrack
from distance_measuring.vision.features import Features
from distance_measuring.vision.guided import GuidedConfig
from distance_measuring.vision.refinement import (
    RefinementConfig,
    refine_registered_camera,
    retry_unmatched_observations,
)


def fixture(*, missing=5, perturb=True):
    camera = CameraIntrinsics(400, 400, 319.5, 239.5, 640, 480)
    pixels = np.array([(x, y) for y in range(80, 401, 80) for x in range(180, 541, 90)], float)
    world = np.c_[camera.normalize_pixels(pixels), np.ones(len(pixels))] * 8
    cameras = tuple(
        SceneCamera(i, camera, CameraExtrinsics(np.eye(3), [i * 0.5, 0, 0])) for i in range(3)
    )
    tracks = tuple(
        SceneTrack(
            k,
            tuple(
                Observation(c.image_id, k, tuple(camera.project(p, c.extrinsics)), 0)
                for c in cameras
                if c.image_id < 2 or k < len(world) - missing
            ),
            k,
        )
        for k, p in enumerate(world)
    )
    if perturb:
        cameras = (
            *cameras[:2],
            replace(cameras[2], extrinsics=CameraExtrinsics(np.eye(3), [1.02, 0.01, 0])),
        )
    scene = Scene(
        cameras, tracks, tuple(ScenePoint(k, k, tuple(p), 3) for k, p in enumerate(world))
    )
    first = np.random.default_rng(54).integers(0, 255, (480, 640), np.uint8)
    images = {
        i: cv2.warpAffine(first, np.array([[1.0, 0, -25 * i], [0, 1.0, 0]]), (640, 480))
        for i in range(3)
    }
    empty = Features(
        np.empty((0, 2)),
        np.empty((0, 128), np.float32),
        np.empty(0),
        np.empty(0),
        np.empty(0),
        np.empty(0, np.int32),
    )
    return scene, images, empty


def test_refined_pose_recovers_previously_missing_landmark_observations():
    scene, images, empty = fixture()
    before = scene.to_dict()
    result, report = refine_registered_camera(
        scene, 1, 2, images, empty, empty, guided_config=GuidedConfig(search_radius_px=8)
    )
    assert scene.to_dict() == before
    assert report["added_observation_count"] == 5
    assert all(len(t.observations) == 3 for t in result.tracks)
    assert 1 <= report["round_count"] <= 2
    first = report["rounds"][0]
    assert first["bundle_adjustment"]["accepted"]
    assert (
        first["bundle_adjustment"]["after_rmse_px"] <= first["bundle_adjustment"]["before_rmse_px"]
    )
    assert first["radius_reduced"] and first["search_radius_px"] == 4
    assert report["bundle_adjustment_runtime_s"] > 0 and report["matching_runtime_s"] > 0
    assert_allclose(result.cameras[-1].extrinsics.C_w, [1, 0, 0], atol=1e-4)
    assert len({(o.image_id, o.feature_id) for t in result.tracks for o in t.observations}) == 75


def test_perfect_unchanged_scene_exits_early_instead_of_repeating_search():
    scene, images, empty = fixture(missing=0, perturb=False)
    result, report = refine_registered_camera(scene, 1, 2, images, empty, empty)
    assert report["round_count"] == 1
    assert report["stop_reason"] == "no_meaningful_improvement"
    assert report["added_observation_count"] == report["added_landmark_count"] == 0
    assert_allclose(
        [p.position for p in result.points], [p.position for p in scene.points], atol=1e-10
    )


def test_recovered_observations_reserve_future_temporal_feature_ids():
    scene, images, _ = fixture(perturb=False)
    result, report = retry_unmatched_observations(
        scene, 2, images, reserved_feature_id=2000, config=GuidedConfig(search_radius_px=8)
    )
    assert report["added_observation_count"] == 5
    new = [o for t in result.tracks[-5:] for o in t.observations if o.image_id == 2]
    assert [o.feature_id for o in new] == list(range(2001, 2006))


def test_round_limit_and_small_radius_are_respected():
    scene, images, empty = fixture()
    _, report = refine_registered_camera(
        scene,
        1,
        2,
        images,
        empty,
        empty,
        config=RefinementConfig(max_rounds=1),
        guided_config=GuidedConfig(search_radius_px=2),
    )
    assert report["round_count"] == 1 and report["stop_reason"] == "round_limit"
    assert report["rounds"][0]["search_radius_px"] <= 2


def test_unavailable_ba_exits_without_attempting_an_unjustified_search():
    scene, images, empty = fixture()
    result, report = refine_registered_camera(
        scene, 1, 2, images, empty, empty, bundle_config=LocalBundleConfig(min_landmarks=100)
    )
    assert report["round_count"] == 1 and report["stop_reason"] == "no_accepted_local_refinement"
    assert report["rounds"][0]["observation_retry"] is None
    assert [p.position for p in result.points] == [p.position for p in scene.points]


def test_appearance_match_cannot_add_an_observation_with_bad_projection_error():
    scene, images, _ = fixture(perturb=False)
    images[2] = cv2.warpAffine(images[0], np.array([[1.0, 0, -45], [0, 1.0, 0]]), (640, 480))
    result, report = retry_unmatched_observations(
        scene, 2, images, config=GuidedConfig(search_radius_px=8)
    )
    assert report["local_search"]["accepted_count"] == 5
    assert report["added_observation_count"] == 0
    assert all(r["reason"] == "retry_high_reprojection_error" for r in report["patch_matches"])
    assert [t.observations for t in result.tracks] == [t.observations for t in scene.tracks]


@pytest.mark.parametrize(
    "options",
    [
        {"max_rounds": 3},
        {"min_search_radius_px": 0},
        {"radius_shrink_factor": 1},
        {"tighten_rmse_px": float("nan")},
    ],
)
def test_invalid_refinement_options_fail(options):
    with pytest.raises(ValueError):
        RefinementConfig(**options)
