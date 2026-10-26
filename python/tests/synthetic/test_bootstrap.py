from dataclasses import replace

import numpy as np
import pytest
from numpy.testing import assert_allclose
from scipy.spatial.transform import Rotation

from distance_measuring.camera import CameraExtrinsics, CameraIntrinsics
from distance_measuring.vision.bootstrap import (
    BootstrapConfig,
    BootstrapPair,
    estimate_bootstrap_pose,
    evaluate_bootstrap_pair,
    select_bootstrap_pair,
    triangulate_bootstrap,
)


def known_pair(image_ids=(0, 1), *, translation=0.8, count=120, noise=0):
    rng = np.random.default_rng(47)
    world = rng.uniform([-2, -1.5, 4], [2, 1.5, 9], (count, 3))
    a = CameraIntrinsics(820, 790, 639.5, 479.5, 1280, 960, skew=2)
    b = CameraIntrinsics(850, 810, 639.5, 479.5, 1280, 960, skew=-1)
    pose = CameraExtrinsics(
        Rotation.from_rotvec([0.04, -0.08, 0.02]).as_matrix(),
        np.array([translation, 0.12 * translation, 0.03 * translation]),
    )
    first = a.project(world) + rng.normal(0, noise, (count, 2))
    second = b.project(world, pose) + rng.normal(0, noise, (count, 2))
    # Deliberately large gyro residual: the visual rotation check must be independent.
    pair = BootstrapPair(image_ids, np.arange(count), first, second, a, b, np.full(count, 50.0))
    return pair, pose, world


def test_selects_earliest_viable_pair_instead_of_largest_later_score():
    early, _, _ = known_pair((4, 7))
    later, _, _ = known_pair((7, 9))
    later = replace(later, residual_parallax_px=np.full(120, 100.0))
    selected, diagnostics = select_bootstrap_pair([later, early])
    assert selected is early and len(diagnostics) == 1
    assert diagnostics[0]["preselection_passed"]


def test_bad_first_pair_is_explained_before_selecting_the_next():
    small, _, _ = known_pair(count=12)
    good, _, _ = known_pair((1, 2))
    selected, diagnostics = select_bootstrap_pair([small, good])
    assert selected is good
    assert "insufficient_tracks" in diagnostics[0]["reasons"][0]
    assert diagnostics[1]["rotation_only_fit_median_px"] >= 3


def test_pure_rotation_is_rejected_even_with_large_but_wrong_gyro_parallax():
    pair, _, _ = known_pair(translation=0)
    result = evaluate_bootstrap_pair(pair)
    assert not result["preselection_passed"]
    assert result["rotation_only_fit_median_px"] < 1e-9
    assert any("rotation_only_motion" in reason for reason in result["reasons"])


def test_coverage_and_parallax_gates_report_thresholds():
    pair, _, _ = known_pair()
    concentrated = replace(pair, first_pixels=np.tile([20.0, 20.0], (120, 1)))
    result = evaluate_bootstrap_pair(concentrated)
    assert not result["preselection_passed"]
    assert any("insufficient_coverage" in reason for reason in result["reasons"])
    small = replace(pair, residual_parallax_px=np.full(120, 1.0))
    assert any(
        "insufficient_residual_parallax" in reason
        for reason in evaluate_bootstrap_pair(small)["reasons"]
    )


@pytest.mark.parametrize(
    "options",
    [
        {"min_tracks": 7},
        {"min_occupied_cells": 49},
        {"min_visual_parallax_px": 0},
        {"seed": -1},
        {"min_landmarks": 101, "max_landmarks": 100},
    ],
)
def test_invalid_thresholds_are_rejected(options):
    with pytest.raises(ValueError):
        BootstrapConfig(**options)


def test_invalid_correspondence_shapes_frames_and_ids_are_rejected():
    pair, _, _ = known_pair()
    for changes in (
        {"track_ids": np.zeros(120, int)},
        {"first_pixels": np.zeros((120, 3))},
        {"first_camera": replace(pair.first_camera, distortion=(0.1, 0, 0, 0, 0))},
        {"second_pixels": np.full((120, 2), np.nan)},
    ):
        with pytest.raises(ValueError):
            replace(pair, **changes)


def test_existing_calibrated_solver_recovers_known_pose_and_reports_counts():
    pair, expected, _ = known_pair(noise=0.1)
    second = pair.second_pixels.copy()
    second[-30:] = np.random.default_rng(12).uniform([50, 50], [1200, 900], (30, 2))
    pair = replace(pair, second_pixels=second)
    result = estimate_bootstrap_pose(pair)
    assert result.report["success"], result.report
    assert result.report["input_track_count"] == 120
    assert result.report["ransac_inlier_count"] >= 85
    assert result.report["pose_inlier_count"] >= 80
    error = np.degrees(Rotation.from_matrix(result.relative.R @ expected.R_cw.T).magnitude())
    assert error < 1
    direction = expected.O_cw / np.linalg.norm(expected.O_cw)
    assert np.degrees(np.arccos(np.clip(result.relative.t_direction @ direction, -1, 1))) < 3
    assert result.report["runtime_s"] > 0


def test_strong_landmarks_keep_ids_gauge_and_per_point_quality():
    pair, expected, world = known_pair()
    result = triangulate_bootstrap(pair, estimate_bootstrap_pose(pair))
    assert result.report["success"] and len(result.scene.points) == 100
    assert result.report["rejection_counts"]["landmark_budget"] == 20
    assert_allclose(result.scene.cameras[0].extrinsics.R_cw, np.eye(3))
    assert_allclose(result.scene.cameras[0].extrinsics.C_w, 0)
    assert np.linalg.norm(result.scene.cameras[1].extrinsics.C_w) == pytest.approx(1)
    baseline = np.linalg.norm(expected.C_w)
    for point in result.scene.points:
        assert point.point_id == point.track_id
        assert_allclose(point.position, world[point.track_id] / baseline, atol=1e-6)
        assert point.triangulation_angle_degrees >= 1
        track = next(t for t in result.scene.tracks if t.track_id == point.track_id)
        for camera, observation in zip(result.scene.cameras, track.observations, strict=True):
            assert camera.extrinsics.world_to_camera(point.position)[2] > 0
            error = np.linalg.norm(
                camera.intrinsics.project(point.position, camera.extrinsics, distort=False)
                - observation.pixel
            )
            assert error <= 3
            assert error == pytest.approx(observation.reprojection_error_px, abs=1e-10)


def test_quality_gates_are_not_relaxed_to_fill_the_landmark_budget():
    pair, _, _ = known_pair()
    config = BootstrapConfig(min_triangulation_angle_degrees=89)
    result = triangulate_bootstrap(pair, estimate_bootstrap_pose(pair, config), config)
    assert not result.report["success"] and not result.scene.points
    assert result.report["reprojection_rmse_px"] is None
    assert result.report["rejection_counts"]["poor_triangulation_angle"] == 120
    assert all(t.point_id is None and t.rejection_reason for t in result.scene.tracks)


def test_unsuccessful_pose_cannot_create_placeholder_cameras_or_landmarks():
    pair, _, _ = known_pair()
    pose = estimate_bootstrap_pose(pair, BootstrapConfig(min_pose_inliers=121))
    assert not pose.report["success"]
    with pytest.raises(ValueError, match="unsuccessful"):
        triangulate_bootstrap(pair, pose)
