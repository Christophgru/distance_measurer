from dataclasses import replace

import numpy as np
import pytest
from numpy.testing import assert_allclose
from scipy.spatial.transform import Rotation

from distance_measuring.camera import CameraExtrinsics, CameraIntrinsics
from distance_measuring.vision.pnp import PnPConfig, estimate_pnp


def fixture():
    rng = np.random.default_rng(12)
    world = rng.uniform([-2, -1.5, 5], [2, 1.5, 10], (100, 3))
    camera = CameraIntrinsics(800, 750, 639.5, 479.5, 1280, 960, skew=5)
    pose = CameraExtrinsics(Rotation.from_rotvec([0.04, -0.07, 0.02]).as_matrix(), [0.8, 0.1, 0.04])
    pixels = camera.project(world, pose) + rng.normal(0, 0.15, (100, 2))
    return world, pixels, camera, pose


def test_pnp_recovers_known_pose_with_outliers_and_skew():
    world, pixels, camera, truth = fixture()
    pixels[-25:] = np.random.default_rng(4).uniform([20, 20], [1200, 900], (25, 2))
    result = estimate_pnp(world, pixels, camera)
    assert result.pose is not None and result.inliers.sum() >= 70
    angle = np.degrees(Rotation.from_matrix(result.pose.R_cw @ truth.R_cw.T).magnitude())
    direction = result.pose.O_cw / np.linalg.norm(result.pose.O_cw)
    expected = truth.O_cw / np.linalg.norm(truth.O_cw)
    assert angle < 0.2
    assert np.degrees(np.arccos(np.clip(direction @ expected, -1, 1))) < 1
    assert_allclose(result.pose.C_w, truth.C_w, atol=0.02)
    assert (result.errors_px[result.inliers] <= 3).all()
    assert (result.pose.world_to_camera(world[result.inliers])[:, 2] > 0).all()
    assert result.report["runtime_s"] > 0


def test_pnp_failure_does_not_emit_a_pose():
    world, pixels, camera, _ = fixture()
    for a, b, reason in (
        (world[:4], pixels[:4], "insufficient_3d_2d"),
        (np.ones_like(world), pixels, "degenerate_3d_2d"),
    ):
        result = estimate_pnp(a, b, camera)
        assert result.pose is None and not result.inliers.any()
        assert reason in result.report["reason"]


def test_pnp_validates_input_and_options():
    world, pixels, camera, _ = fixture()
    for changes in ({"min_inliers": 5}, {"threshold_px": 0}, {"seed": -1}):
        with pytest.raises(ValueError):
            PnPConfig(**changes)
    with pytest.raises(ValueError):
        estimate_pnp(world, pixels, replace(camera, distortion=(0.1, 0, 0, 0, 0)))
    with pytest.raises(ValueError):
        estimate_pnp(world, np.full_like(pixels, np.nan), camera)
