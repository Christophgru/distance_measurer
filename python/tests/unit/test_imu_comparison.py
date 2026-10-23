from types import SimpleNamespace

import numpy as np
import pytest
from numpy.testing import assert_allclose
from scipy.spatial.transform import Rotation

from distance_measuring.inertial.comparison import (
    align_heading_only,
    compare_recorded_imu,
    increment_comparison,
)
from distance_measuring.inertial.experimental import gyro_orientation_interpolator


def recording(*, heading_jump=0, tilt_jump=0, gyro_bias=None, with_game=True):
    times = np.arange(1, 1002) / 100
    gyro_bias = np.zeros(3) if gyro_bias is None else np.array(gyro_bias)
    rate = np.array([0, 0, 0.4]) if np.all(gyro_bias == 0) else np.zeros(3)
    initial = Rotation.from_euler("x", 20, degrees=True)
    truth = initial * Rotation.from_rotvec(times[:, None] * rate)
    jump = Rotation.from_euler(
        "zyx",
        np.c_[
            np.where(times >= 5, heading_jump, 0),
            np.where(times >= 5, tilt_jump, 0),
            np.zeros(len(times)),
        ],
        degrees=True,
    )
    phone = jump * truth
    game = Rotation.from_euler("z", -90, degrees=True) * truth
    gravity = np.tile([0, 0, 9.80665], (len(times), 1))

    def samples(vectors, quaternion=False):
        return [
            SimpleNamespace(
                timestamp_ns=round(t * 1e9),
                x=float(v[0]),
                y=float(v[1]),
                z=float(v[2]),
                w=float(v[3]) if quaternion else None,
                accuracy=3,
            )
            for t, v in zip(times, vectors, strict=True)
        ]

    streams = {
        "accelerometer": samples(truth.inv().apply(gravity)),
        "gyroscope": samples(np.tile(rate + gyro_bias, (len(times), 1))),
        "rotation_vector": samples(phone.as_quat(), True),
    }
    if with_game:
        streams["game_rotation_vector"] = samples(game.as_quat(), True)
    return SimpleNamespace(
        start_ns=0,
        metadata=SimpleNamespace(session_id="synthetic_imu"),
        sensor_streams=streams,
        images=(),
    )


def test_game_heading_alignment_is_constant_and_preserves_tilt():
    reference = Rotation.from_euler("xyz", [12, -7, 33], degrees=True)
    other = Rotation.from_euler("z", 93, degrees=True) * reference
    Q = align_heading_only(reference, other)
    assert_allclose((Q * other).as_matrix(), reference.as_matrix(), atol=1e-14)
    assert_allclose((Q * other).inv().apply([0, 0, 1]), other.inv().apply([0, 0, 1]), atol=1e-14)


def test_fused_heading_jump_is_detected_without_reporting_gyro_supported_motion_as_jump():
    report, arrays = compare_recorded_imu(recording(heading_jump=4))
    assert_allclose(report["game_constant_heading_alignment_degrees"], 90, atol=1e-12)
    assert_allclose(
        report["methods"]["phone_rotation_vector"]["orientation_vs_gyro_degrees"]["max"],
        4,
        atol=1e-10,
    )
    assert report["native_increment_comparison"]["rotation_vector"]["difference_max_degrees"] > 3.9
    assert (
        report["native_increment_comparison"]["game_rotation_vector"]["difference_max_degrees"]
        < 1e-10
    )
    assert (
        np.max(
            np.linalg.norm(
                arrays["phone_game_rotation_vector_linear_acceleration_enu_m_s2"], axis=1
            )
        )
        < 1e-12
    )
    assert (
        not report["metric_scale_accepted"]
        and not report["orientation_accuracy_ground_truth_available"]
    )


def test_two_degree_tilt_error_causes_expected_gravity_leakage_and_is_not_aligned_away():
    report, arrays = compare_recorded_imu(recording(tilt_jump=2))
    phone = report["methods"]["phone_rotation_vector"]
    assert_allclose(phone["tilt_vs_gyro_degrees"]["max"], 2, atol=1e-10)
    expected = 2 * 9.80665 * np.sin(np.radians(1))
    assert_allclose(
        phone["linear_acceleration_difference_vs_gyro_m_s2"]["norm_max"], expected, atol=1e-10
    )
    orientation = Rotation.from_quat(arrays["phone_rotation_vector_xyzw"])
    assert_allclose(
        orientation.apply(arrays["phone_rotation_vector_linear_acceleration_device_m_s2"]),
        arrays["phone_rotation_vector_linear_acceleration_enu_m_s2"],
        atol=1e-13,
    )


def test_declared_rest_adds_bias_corrected_comparison_and_keeps_zero_bias_baseline():
    bias = [0.001, -0.002, 0.003]
    report, arrays = compare_recorded_imu(recording(gyro_bias=bias))
    assert report["stationary_calibration"]["passed"]
    assert_allclose(report["stationary_calibration"]["gyro_bias_rad_s"], bias, atol=1e-14)
    assert "integrated_gyro" in report["methods"] and "bias_corrected_gyro" in report["methods"]
    assert (
        np.max(np.linalg.norm(arrays["bias_corrected_gyro_linear_acceleration_enu_m_s2"], axis=1))
        < 1e-12
    )


def test_legacy_recording_without_game_or_magnetometer_remains_comparable():
    report, _ = compare_recorded_imu(recording(with_game=False))
    assert report["magnetometer"] is None
    assert report["phone_game_vs_phone_rotation_vector"] is None
    assert set(report["methods"]) == {"integrated_gyro", "phone_rotation_vector"}


def test_comparison_rejects_large_gyro_gaps():
    session = recording()
    session.sensor_streams["gyroscope"] = [
        s for s in session.sensor_streams["gyroscope"] if not 3e9 < s.timestamp_ns < 4e9
    ]
    with pytest.raises(ValueError, match="Gyro gaps"):
        compare_recorded_imu(session)


def test_increment_comparison_uses_exact_native_intervals_for_real_fast_rotation():
    times = np.array([0.0, 0.009, 0.023, 0.04, 0.057])
    rate = np.array([0, 0, 4.0])
    gyro = gyro_orientation_interpolator(times, np.tile(rate, (len(times), 1)), Rotation.identity())
    observed = Rotation.from_rotvec(times[:, None] * rate)
    report, _, _ = increment_comparison(times, observed, gyro)
    assert report["difference_max_degrees"] < 1e-12
