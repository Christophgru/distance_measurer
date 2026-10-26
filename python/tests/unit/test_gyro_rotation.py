from dataclasses import replace

import numpy as np
import pytest
from numpy.testing import assert_allclose
from scipy.spatial.transform import Rotation

from distance_measuring.dataset import load_session
from distance_measuring.dataset.schema import ImageMetadata, SensorSample
from distance_measuring.inertial.rotation import camera_ray_rotation, integrate_gyro_interval


def session_with_rates(capture, times, rates):
    session = load_session(capture.path, validate=False)
    return replace(
        session,
        sensor_samples=tuple(
            SensorSample(int(t), "gyroscope", *map(float, rate), None, 3)
            for t, rate in zip(times, rates, strict=True)
        ),
    )


def test_fractional_endpoints_use_bracketing_samples_and_preserve_large_integer_times(capture):
    base = 10**18 + 123
    times = [base + offset for offset in (0, 100_000_000, 200_000_000, 300_000_000)]
    rate = [0.2, -0.1, 0.3]
    session = session_with_rates(capture, times, [rate] * 4)
    result = integrate_gyro_interval(session, base + 25_000_001, base + 275_000_002)
    expected = Rotation.from_rotvec(-np.array(rate) * 0.250000001).as_matrix()
    assert_allclose(result.R_device_second_first, expected, atol=1e-14)
    assert result.sample_count == 4 and result.integration_sample_count == 4
    assert result.interpolated_endpoint_count == 2
    assert result.sample_span_s == pytest.approx(0.3)
    assert result.start_timestamp_ns == base + 25_000_001
    assert type(result.to_dict()["start_timestamp_ns"]) is int


def test_varying_single_axis_rate_is_integrated_at_exact_endpoints(capture):
    times = np.arange(5) * 100_000_000 + 1_000_000_000
    t = np.arange(5) * 0.1
    rates = np.c_[np.zeros((5, 2)), 0.2 + 0.4 * t]
    session = session_with_rates(capture, times, rates)
    result = integrate_gyro_interval(session, 1_050_000_000, 1_350_000_000)
    angle = 0.2 * 0.3 + 0.2 * (0.35**2 - 0.05**2)
    assert_allclose(
        result.R_device_second_first, Rotation.from_euler("z", -angle).as_matrix(), atol=1e-14
    )


def test_exact_sample_endpoints_do_not_duplicate_integration_rows(capture):
    session = session_with_rates(
        capture, [1_000_000_000, 1_100_000_000, 1_200_000_000], [[0, 0, 0]] * 3
    )
    result = integrate_gyro_interval(session, 1_000_000_000, 1_200_000_000)
    assert result.sample_count == result.integration_sample_count == 3
    assert result.interpolated_endpoint_count == 0
    assert_allclose(result.R_device_second_first, np.eye(3))


@pytest.mark.parametrize(
    "times,start,end",
    [
        ([100, 200], 99, 150),
        ([100, 200], 150, 201),
        ([100, 100], 100, 101),
        ([100], 100, 101),
        ([100, 200], 150, 150),
        ([100, 200], True, 150),
    ],
)
def test_bad_intervals_and_coverage_fail_explicitly(capture, times, start, end):
    session = session_with_rates(capture, times, [[0, 0, 0]] * len(times))
    with pytest.raises(ValueError):
        integrate_gyro_interval(session, start, end)


def test_large_sensor_gap_is_not_silently_integrated(capture):
    session = session_with_rates(capture, [1_000_000_000, 1_500_000_000], [[0, 0, 1]] * 2)
    with pytest.raises(ValueError, match="gap"):
        integrate_gyro_interval(session, 1_100_000_000, 1_200_000_000)


def test_exact_gap_limit_and_noncommuting_body_rotations(capture):
    times = [1_000_000_000 + i * 250_000_000 for i in range(4)]
    rates = [[0.4, 0, 0], [0.4, 0, 0], [0, 0.8, 0], [0, 0.8, 0]]
    session = session_with_rates(capture, times, rates)
    result = integrate_gyro_interval(session, times[0], times[-1], max_gap_s=0.25)
    expected = (
        Rotation.from_rotvec([0.1, 0, 0])
        * Rotation.from_rotvec([0.05, 0.1, 0])
        * Rotation.from_rotvec([0, 0.2, 0])
    )
    assert_allclose(result.R_device_second_first, expected.inv().as_matrix(), atol=1e-14)


def camera_metadata(orientation=90):
    return ImageMetadata(
        camera_id="0",
        lens_facing=1,
        sensor_orientation_degrees=orientation,
        target_rotation=0,
        is_flipped=False,
    )


@pytest.mark.parametrize(
    "mount,pitch_direction,yaw_direction",
    [
        (0, [0, 1, 0], [1, 0, 0]),
        (90, [1, 0, 0], [0, -1, 0]),
        (180, [0, -1, 0], [-1, 0, 0]),
        (270, [-1, 0, 0], [0, 1, 0]),
    ],
)
def test_positive_device_pitch_and_yaw_predict_independent_stored_ray_directions(
    mount, pitch_direction, yaw_direction
):
    metadata = camera_metadata(mount)
    for axis, expected in (("x", pitch_direction), ("y", yaw_direction)):
        # Gyro measures ACTIVE device motion; static scene rays transform inversely.
        passive = Rotation.from_euler(axis, -0.1).as_matrix()
        R = camera_ray_rotation(passive, metadata, metadata)
        ray = R @ [0, 0, 1]
        assert_allclose(ray[:2], np.asarray(expected)[:2] * np.sin(0.1), atol=1e-14)
        assert_allclose(ray[2], np.cos(0.1), atol=1e-14)


@pytest.mark.parametrize("mount", [0, 90, 180, 270])
def test_positive_device_roll_rotates_right_ray_down_in_stored_camera_axes(mount):
    metadata = camera_metadata(mount)
    R = camera_ray_rotation(Rotation.from_euler("z", -0.1).as_matrix(), metadata, metadata)
    assert_allclose(R @ [1, 0, 1], [np.cos(0.1), np.sin(0.1), 1], atol=1e-14)


@pytest.mark.parametrize(
    "changes",
    [
        {"lens_facing": 0},
        {"is_flipped": True},
        {"target_rotation": 1},
        {"sensor_orientation_degrees": None},
        {"camera_id": "tele"},
    ],
)
def test_unsupported_camera_conventions_are_not_silently_assumed(changes):
    metadata = camera_metadata()
    with pytest.raises(ValueError):
        camera_ray_rotation(np.eye(3), metadata, replace(metadata, **changes))


def test_known_physical_lens_switch_and_invalid_rotation_are_rejected():
    a, b = (
        replace(camera_metadata(), physical_camera_id="wide"),
        replace(camera_metadata(), physical_camera_id="tele"),
    )
    with pytest.raises(ValueError, match="physical_camera_id"):
        camera_ray_rotation(np.eye(3), a, b)
    with pytest.raises(ValueError, match="orthonormal"):
        camera_ray_rotation(np.diag([1, 1, -1]), a, a)


def test_different_known_stored_mounts_are_composed_at_both_interval_endpoints():
    rotation = camera_ray_rotation(np.eye(3), camera_metadata(0), camera_metadata(90))
    assert_allclose(rotation @ [1, 0, 1], [0, -1, 1], atol=1e-14)
