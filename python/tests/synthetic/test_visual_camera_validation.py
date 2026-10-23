import copy
from dataclasses import replace
from itertools import combinations

import numpy as np
import pytest
from numpy.testing import assert_allclose
from scipy.spatial.transform import Rotation

from distance_measuring.camera import CameraExtrinsics, CameraIntrinsics
from distance_measuring.optimization.bundle import reprojection_residuals
from distance_measuring.vision.pairwise_scene import rebuild_pairwise_scene
from distance_measuring.vision.validation import (
    camera_fit_quality,
    evaluate_camera_holdout,
    prune_visual_outliers,
    split_camera_matches,
    subset_camera_holdout,
    subset_camera_matches,
    supported_camera_subsets,
)


@pytest.fixture
def visual_audit():
    ids = list(range(8))
    K = CameraIntrinsics(1100, 1100, 1199.5, 899.5, 2400, 1800)
    world = np.random.default_rng(21).uniform([-1, -0.6, 4], [1.2, 0.8, 8], (80, 3))
    poses = [
        CameraExtrinsics(
            Rotation.from_rotvec([0.005 * i, 0.03 * i, 0.002 * i]).as_matrix(),
            [0.2 * i, 0.01 * i, 0.003 * i],
        )
        for i in ids
    ]
    pixels = [K.project(world, p, distort=False) for p in poses]
    rows = []
    for i, j in combinations(ids, 2):
        for k in range(80):
            groups, xy = [None] * 8, [None] * 8
            groups[i] = groups[j] = k
            xy[i] = pixels[i][k].tolist()
            xy[j] = pixels[j][k].tolist()
            rows.append(
                {
                    "usable_pairwise": True,
                    "group_ids": groups,
                    "working_pixels": xy,
                    "correlation": {"score": 0.99},
                }
            )
    return {
        "image_ids": ids,
        "images": [{"image_id": i, "working_intrinsics": K.to_dict()} for i in ids],
        "pairwise_matches": rows,
        "session_id": "synthetic_visual_only",
        "review_id": "original",
    }


def test_eight_camera_registration_and_complete_track_holdout_without_inertial_inputs(visual_audit):
    original = copy.deepcopy(visual_audit)
    training, holdout = split_camera_matches(visual_audit)
    reserved = {(o["image_id"], o["feature_id"]) for r in holdout for o in r["observations"]}
    used = {
        (i, k)
        for r in training["pairwise_matches"]
        if r["usable_pairwise"]
        for i, k in zip(training["image_ids"], r["group_ids"], strict=True)
        if k is not None
    }
    assert not reserved & used and visual_audit == original
    result = rebuild_pairwise_scene(training, refine_incremental=True, seed_strategy="conditioned")
    assert len(result.scene.cameras) == 8 and len(result.report["registrations"]) == 6
    assert_allclose(reprojection_residuals(result.scene), 0, atol=1e-4)
    check = evaluate_camera_holdout(result.scene, holdout)
    assert (
        check["accepted_fraction"] == 1
        and min(check["accepted_observations_by_camera"].values()) >= 8
    )
    assert camera_fit_quality(result.scene, check, converged=True, removed_fraction=0)["passed"]


def test_bad_holdout_rejects_cameras_despite_low_training_error(visual_audit):
    training, holdout = split_camera_matches(visual_audit)
    result = rebuild_pairwise_scene(training)
    for r in holdout:
        next(o for o in r["observations"] if o["image_id"] == 4)["pixel"][0] += 30
    check = evaluate_camera_holdout(result.scene, holdout)
    quality = camera_fit_quality(result.scene, check, converged=True, removed_fraction=0)
    assert quality["training_rmse_px"] < 1e-4 and not quality["passed"]


def test_training_outlier_removal_keeps_other_views_and_never_removes_camera(visual_audit):
    result = rebuild_pairwise_scene(visual_audit)
    scene = result.scene
    track = next(t for t in scene.tracks if t.point_id is not None)
    observations = (
        replace(track.observations[0], reprojection_error_px=8),
        *track.observations[1:],
    )
    changed = replace(
        scene,
        tracks=tuple(
            replace(t, observations=observations) if t.track_id == track.track_id else t
            for t in scene.tracks
        ),
    )
    pruned, removed = prune_visual_outliers(changed)
    assert len(pruned.cameras) == 8 and len(removed) == 1
    assert len(next(t for t in pruned.tracks if t.track_id == track.track_id).observations) == 7
    assert len(track.observations) == 8


@pytest.mark.parametrize("collinear", [False, True])
def test_pair_only_tracks_register_visual_baseline_loops_but_refuse_collinear_scale(collinear):
    K = CameraIntrinsics(1000, 1000, 1199.5, 899.5, 2400, 1800)
    centers = [[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]]
    if collinear:
        centers = [[i * 0.4, 0, 0] for i in range(4)]
    poses = [CameraExtrinsics(np.eye(3), C) for C in centers]
    rows = []
    for pair_id, (i, j) in enumerate(combinations(range(4), 2)):
        world = np.random.default_rng(85 + pair_id).uniform([-0.5, -0.5, 4], [1.5, 1.5, 8], (60, 3))
        xy1, xy2 = K.project(world, poses[i]), K.project(world, poses[j])
        for k in range(60):
            groups, xy = [None] * 4, [None] * 4
            groups[i] = groups[j] = pair_id * 60 + k
            xy[i] = xy1[k].tolist()
            xy[j] = xy2[k].tolist()
            rows.append(
                {
                    "usable_pairwise": True,
                    "group_ids": groups,
                    "working_pixels": xy,
                    "correlation": {"score": 0.99},
                }
            )
    audit = {
        "image_ids": list(range(4)),
        "images": [{"image_id": i, "working_intrinsics": K.to_dict()} for i in range(4)],
        "pairwise_matches": rows,
        "session_id": "pair_only_loop",
        "review_id": "synthetic",
    }
    if collinear:
        with pytest.raises(ValueError, match="remaining images"):
            rebuild_pairwise_scene(audit, refine_incremental=True, seed_strategy="conditioned")
        partial = rebuild_pairwise_scene(
            audit, refine_incremental=True, seed_strategy="conditioned", allow_unregistered=True
        )
        assert len(partial.scene.cameras) == 2
        assert len(partial.report["excluded_images"]) == 2
        assert_allclose(reprojection_residuals(partial.scene), 0, atol=1e-4)
    else:
        result = rebuild_pairwise_scene(audit, refine_incremental=True, seed_strategy="conditioned")
        assert len(result.scene.cameras) == 4
        assert all(
            r["method"] == "visual_baseline_intersection" for r in result.report["registrations"]
        )
        assert all(len(t.observations) == 2 for t in result.scene.tracks)
        assert_allclose(reprojection_residuals(result.scene), 0, atol=1e-4)


def test_subset_keeps_two_nonconsecutive_views_and_original_reserved_tracks(visual_audit):
    training, reserved = split_camera_matches(visual_audit)
    audit = subset_camera_matches(training, [0, 3, 7])
    audit["pairwise_matches"] = [r for r in audit["pairwise_matches"] if r["group_ids"][2] is None]
    original = copy.deepcopy(audit)
    with pytest.raises(ValueError, match="Disconnected"):
        rebuild_pairwise_scene(audit)
    result = rebuild_pairwise_scene(audit, allow_unregistered=True)
    assert [c.image_id for c in result.scene.cameras] == [0, 3]
    assert [r["image_id"] for r in result.report["excluded_images"]] == [7]
    assert audit == original
    holdout = subset_camera_holdout(reserved, [0, 3])
    assert len(holdout) == len(reserved)
    assert all([o["image_id"] for o in r["observations"]] == [0, 3] for r in holdout)
    quality = camera_fit_quality(
        result.scene,
        evaluate_camera_holdout(result.scene, holdout),
        converged=True,
        removed_fraction=0,
    )
    assert quality["passed"]
    assert all(len(r["observations"]) == 8 for r in reserved)


def test_subset_still_aborts_when_no_two_overlapping_cameras_remain(visual_audit):
    audit = subset_camera_matches(visual_audit, [0, 3])
    audit["pairwise_matches"] = []
    with pytest.raises(ValueError, match="Fewer than two"):
        rebuild_pairwise_scene(audit, allow_unregistered=True)


def test_fallback_groups_use_only_visual_support_and_try_larger_groups_first():
    rows = [
        {"image_ids": [a, b], "pose_inliers": n}
        for a, b, n in [(0, 1, 40), (0, 2, 50), (1, 2, 60), (2, 3, 100)]
    ]
    groups = supported_camera_subsets([0, 1, 2, 3], rows)
    assert groups[0] == (0, 1, 2)
    assert groups[1] == (2, 3)
    assert all(len(g) >= 2 for g in groups)
