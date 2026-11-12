from copy import deepcopy

import numpy as np
import pytest
from scipy.spatial.transform import Rotation

from distance_measuring.camera import CameraExtrinsics, CameraIntrinsics
from distance_measuring.camera.calibration import checkerboard_points
from distance_measuring.camera.calibration_audit import (
    audit_board_observations,
    compare_capture_settings,
)
from distance_measuring.dataset import load_session


@pytest.fixture(scope="module")
def board_case():
    camera = CameraIntrinsics(
        800, 810, 399.5, 299.5, 800, 600, distortion=(-0.03, 0.01, 0.001, -0.001, 0)
    )
    board = checkerboard_points(7, 5, 0.025)
    angles = [
        [0.3, -0.25, 0.1],
        [-0.35, 0.2, -0.1],
        [0.2, 0.4, 0.15],
        [-0.3, -0.3, 0.05],
        [0.4, 0.15, -0.1],
        [-0.2, 0.35, 0.05],
    ]
    observations = {}
    for i, angle in enumerate(angles):
        rotation = Rotation.from_rotvec(angle).as_matrix()
        pose = CameraExtrinsics(rotation, -rotation.T @ np.array([-0.08, -0.05, 0.6 + i * 0.01]))
        observations[i] = camera.project(board, pose)
    return camera, observations


def test_reserved_views_and_corners_do_not_fit_intrinsics_or_held_out_poses(board_case):
    camera, observations = board_case
    before = {i: p.copy() for i, p in observations.items()}
    good, candidate = audit_board_observations(
        observations, camera.image_size, columns=7, rows=5, camera_id="0"
    )
    assert good["calibration_audit_passed"]
    assert good["training_image_ids"] == [0, 1, 3, 4]
    assert good["reserved_image_ids"] == [2, 5]
    assert candidate.view_names == ("0", "1", "3", "4")
    assert not good["metric_measurement_accepted"]
    for view in good["held_out_views"]:
        assert set(view["pose_fit_corner_indices"]).isdisjoint(view["tested_corner_indices"])
        assert view["test_rmse_native_px"] < 0.05
        assert not view["intrinsics_fitted_on_this_view"]
    changed = deepcopy(observations)
    tested = good["held_out_views"][0]["tested_corner_indices"]
    changed[2][tested] += [10, 0]
    bad, second_candidate = audit_board_observations(
        changed, camera.image_size, columns=7, rows=5, camera_id="0"
    )
    np.testing.assert_array_equal(candidate.intrinsics.K, second_candidate.intrinsics.K)
    assert not bad["calibration_audit_passed"]
    assert "reserved_view_checks_failed" in bad["blocking_reasons"]
    assert bad["held_out_views"][0]["test_rmse_native_px"] > 5
    for i, points in before.items():
        np.testing.assert_array_equal(points, observations[i])


def test_insufficient_and_duplicate_calibration_views_fail_without_profile(board_case):
    camera, observations = board_case
    small, result = audit_board_observations(
        {i: observations[i] for i in range(5)}, camera.image_size, columns=7, rows=5
    )
    assert result is None and not small["calibration_audit_passed"]
    repeated, result = audit_board_observations(
        {i: observations[0] for i in range(6)}, camera.image_size, columns=7, rows=5
    )
    assert result is None and "training_calibration_failed" in repeated["blocking_reasons"]


def test_unknown_capture_settings_never_become_a_match(capture_factory):
    first, second = capture_factory(), capture_factory()
    report = compare_capture_settings(load_session(first.path), load_session(second.path))
    assert report["status"] == "unknown"
    assert report["fields"]["width"]["status"] == "matched"
    assert report["fields"]["physical_camera_id"]["status"] == "unknown"
    for event in second.events:
        if event["type"] == "image":
            event["metadata"]["zoom_ratio"] = 2.0
    second.write()
    for event in first.events:
        if event["type"] == "image":
            event["metadata"]["zoom_ratio"] = 1.0
    first.write()
    mismatch = compare_capture_settings(load_session(first.path), load_session(second.path))
    assert mismatch["status"] == "mismatch"
    assert mismatch["fields"]["zoom_ratio"]["status"] == "mismatch"


def test_all_recorded_settings_match_but_do_not_certify_optics(capture_factory):
    recordings = [capture_factory(), capture_factory()]
    for recording in recordings:
        for event in recording.events:
            if event["type"] == "image":
                event["metadata"].update(
                    physical_camera_id="lens0",
                    zoom_ratio=1.0,
                    crop_region=[0, 0, 80, 48],
                    focus_distance_diopters=1.0,
                )
        recording.write()
    report = compare_capture_settings(*(load_session(r.path) for r in recordings))
    assert report["status"] == "matched"
    assert report["recorded_settings_cannot_prove_identical_optics"]
