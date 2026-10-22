import numpy as np
import pytest
from numpy.testing import assert_allclose
from scipy.spatial.transform import Rotation

from distance_measuring.inertial.experimental import (
    back_camera_from_device,
    fit_visual_scale,
    integrate_interval,
    orientation_interpolator,
)


def test_android_back_camera_90_mount_is_proper_and_matches_stored_axes():
    R = back_camera_from_device(90)
    assert_allclose(R, [[0, -1, 0], [-1, 0, 0], [0, 0, -1]], atol=1e-15)
    assert_allclose(R @ R.T, np.eye(3), atol=1e-15)
    assert_allclose(np.linalg.det(R), 1)
    with pytest.raises(ValueError):
        back_camera_from_device(45)


def test_xyzw_interpolation_and_rotating_gravity_remove_stationary_acceleration():
    orientation = Rotation.from_euler("x", [[0], [90]], degrees=True)
    sl = orientation_interpolator([0, 2], orientation.as_quat())
    sampled = sl([0, 1, 2])
    gravity = np.tile([0, 0, 9.80665], (3, 1))
    raw = sampled.inv().apply(gravity)
    assert_allclose(sampled.apply(raw) - gravity, 0, atol=1e-14)
    assert_allclose(sl(1).apply([0, 1, 0]), [0, np.sqrt(0.5), np.sqrt(0.5)], atol=1e-14)


def test_sign_flipped_quaternions_interpolate_to_the_same_rotation():
    q = Rotation.from_euler("xyz", [10, 20, 30], degrees=True).as_quat()
    sl = orientation_interpolator([0, 1], [q, -q])
    assert_allclose(sl(0.5).as_matrix(), Rotation.from_quat(q).as_matrix())


def test_exact_piecewise_linear_acceleration_integration_includes_fractional_endpoints():
    times = np.array([0.0, 0.13, 0.51, 1.3, 2.0])
    a = np.c_[2 + 3 * times, np.zeros((len(times), 2))]
    p, v = integrate_interval(times, a, 0.2, 1.6)
    duration = 1.4
    a0 = 2 + 3 * 0.2
    assert_allclose(p, [a0 * duration**2 / 2 + 3 * duration**3 / 6, 0, 0], atol=1e-13)
    assert_allclose(v, [a0 * duration + 3 * duration**2 / 2, 0, 0], atol=1e-13)


def example(scale=0.3):
    times = np.array([0.0, 1.0, 2.0])
    velocity = np.array([0.2, -0.1, 0.05])
    acc = np.array([0.6, 0.1, -0.04])
    physical = times[:, None] * velocity + 0.5 * times[:, None] ** 2 * acc
    return physical / scale, times, 0.5 * times[1:, None] ** 2 * acc, velocity


def test_scale_fit_recovers_known_scale_and_unknown_velocity_without_ruler_input():
    centers, times, displacements, velocity = example()
    result = fit_visual_scale(centers, times, displacements)
    assert_allclose(result["positive_scale_candidate_m_per_unit"], 0.3, atol=1e-13)
    assert_allclose(result["initial_velocity_world_m_s"], velocity, atol=1e-13)
    assert result["fixed_bias_design_rank"] == 4
    assert result["free_bias_design_rank"] == 6 < result["free_bias_parameter_count"]
    assert not result["scale_velocity_bias_jointly_observable"]
    assert not result["metric_scale_accepted"]


def test_constant_velocity_cannot_supply_a_scale_with_unknown_initial_velocity():
    result = fit_visual_scale([[0, 0, 0], [1, 0, 0], [2, 0, 0]], [0, 1, 2], np.zeros((2, 3)))
    assert result["fixed_bias_design_rank"] == 3
    assert result["positive_scale_candidate_m_per_unit"] is None
    assert result["rejection_reason"] == "unobservable_scale"


def test_nonpositive_scale_is_rejected_instead_of_taking_its_absolute_value():
    centers, times, displacements, _ = example(-0.3)
    result = fit_visual_scale(centers, times, displacements)
    assert result["raw_scale_solution_m_per_unit"] < 0
    assert result["positive_scale_candidate_m_per_unit"] is None
    assert result["rejection_reason"] == "nonpositive_scale"


@pytest.mark.parametrize(
    "times,start,end",
    [
        ([0, 0, 1], 0, 0.5),
        ([0, 1, 2], -0.1, 1),
        ([0, 1, 2], 0, 3),
        ([0, 1, 2], 1, 1),
    ],
)
def test_integration_rejects_missing_coverage_or_invalid_timeline(times, start, end):
    with pytest.raises(ValueError):
        integrate_interval(times, np.zeros((3, 3)), start, end)


@pytest.mark.parametrize("q", [[[0, 0, 0, 0]] * 2, [[0, 0, 0, 1]], [[np.nan, 0, 0, 1]] * 2])
def test_bad_quaternion_streams_fail(q):
    with pytest.raises(ValueError):
        orientation_interpolator([0, 1], q)
