import copy
from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest
from numpy.testing import assert_allclose
from scipy.spatial.transform import Rotation

from distance_measuring.inertial.experimental import gyro_orientation_interpolator
from distance_measuring.inertial.heading import (
    VisualAnchoredOrientation,
    compass_reference,
    prepare_visual_heading,
    rotate_scene_world,
    stationary_calibration,
)
from distance_measuring.optimization.bundle import reprojection_residuals
from distance_measuring.vision.overlap import DisconnectedOverlapError


def test_tilt_compensated_compass_recovers_known_device_to_magnetic_world_rotation():
    expected = Rotation.from_euler("xyz", [24, -31, 78], degrees=True)
    actual = compass_reference(
        expected.inv().apply([0, 0, 9.81]), expected.inv().apply([0, 30, -40])
    )
    assert_allclose(actual.as_matrix(), expected.as_matrix(), atol=1e-14)


@pytest.mark.parametrize("field", [[0, 0, 40], [0, 0, 0], [0, 1000, 0], [0, np.nan, 0]])
def test_compass_rejects_vertical_implausible_or_invalid_magnetic_fields(field):
    with pytest.raises(ValueError):
        compass_reference([0, 0, 9.81], field)


def test_stationary_window_estimates_gyro_bias_and_refuses_moving_window():
    times = np.linspace(0, 2, 201)
    acceleration = np.tile([0, 0, 9.81], (201, 1))
    bias = np.array([0.001, -0.002, 0.003])
    gyro = np.tile(bias, (201, 1))
    result = stationary_calibration(times, acceleration, times, gyro, [0.1, 1.6])
    assert result["passed"] and not result["accelerometer_bias_estimated"]
    assert_allclose(result["gyro_bias_rad_s"], bias)
    acceleration[:, 0] = np.sin(times * 20)
    assert not stationary_calibration(times, acceleration, times, gyro, [0.1, 1.6])["passed"]


def test_visual_heading_is_exact_while_gyro_tilt_and_gravity_direction_are_preserved():
    times = np.linspace(0, 6, 601)
    gyro = gyro_orientation_interpolator(
        times, np.tile([0, 0, 0.03], (601, 1)), Rotation.identity()
    )
    photo_times = [2, 3, 5]
    visual = Rotation.from_euler("xyz", [[2, 3, 20], [4, -2, 30], [1, 2, 50]], degrees=True)
    corrected = VisualAnchoredOrientation(gyro, photo_times, visual)
    actual = corrected(photo_times).apply([0, 1, 0])[:, :2]
    target = visual.apply([0, 1, 0])[:, :2]
    assert_allclose(
        actual / np.linalg.norm(actual, axis=1)[:, None],
        target / np.linalg.norm(target, axis=1)[:, None],
        atol=1e-14,
    )
    assert_allclose(
        corrected(times).inv().apply([0, 0, 1]), gyro(times).inv().apply([0, 0, 1]), atol=1e-14
    )
    correction = Rotation.from_rotvec([0, 0, corrected.correction_angles[0]])
    assert_allclose(
        corrected([0, 1]).as_matrix(),
        (correction * gyro([0, 1])).as_matrix(),
        atol=1e-14,
    )
    assert np.max((corrected(3 - 1e-8).inv() * corrected(3 + 1e-8)).magnitude()) < 1e-7


def test_visual_heading_correction_crosses_the_wrap_boundary_smoothly():
    gyro = gyro_orientation_interpolator([0, 4], np.zeros((2, 3)), Rotation.identity())
    visual = Rotation.from_euler("z", [[170], [-170]], degrees=True)
    corrected = VisualAnchoredOrientation(gyro, [1, 3], visual)
    expected = Rotation.from_euler("z", 180, degrees=True)
    assert_allclose(corrected(2).as_matrix(), expected.as_matrix(), atol=1e-14)


def test_one_global_rotation_preserves_image_geometry_and_all_relative_rotations(
    bundle_scene_factory,
):
    scene, _, _ = bundle_scene_factory()
    Q = Rotation.from_euler("xyz", [30, -15, 80], degrees=True)
    result = rotate_scene_world(scene, Q, {"source": "test_compass"})
    assert_allclose(reprojection_residuals(result), reprojection_residuals(scene), atol=1e-12)
    for a, b in [(0, 1), (0, 2), (1, 2)]:
        old = scene.cameras[b].extrinsics.R_cw @ scene.cameras[a].extrinsics.R_cw.T
        new = result.cameras[b].extrinsics.R_cw @ result.cameras[a].extrinsics.R_cw.T
        assert_allclose(new, old, atol=1e-14)
        assert_allclose(
            np.linalg.norm(result.cameras[b].extrinsics.C_w - result.cameras[a].extrinsics.C_w),
            np.linalg.norm(scene.cameras[b].extrinsics.C_w - scene.cameras[a].extrinsics.C_w),
        )
    assert scene.metadata.get("heading") is None


@pytest.fixture
def heading_capture(bundle_scene_factory):
    scene, _, _ = bundle_scene_factory()
    scene = replace(scene, metadata={**scene.metadata, "session_id": "heading_synthetic"})
    times = np.arange(601) / 100

    def stream(values, quaternion=False):
        return [
            SimpleNamespace(
                timestamp_ns=round(t * 1e9),
                x=float(v[0]),
                y=float(v[1]),
                z=float(v[2]),
                w=float(v[3]) if quaternion else None,
                accuracy=3,
            )
            for t, v in zip(times, values, strict=True)
        ]

    session = SimpleNamespace(
        start_ns=0,
        metadata=SimpleNamespace(session_id="heading_synthetic"),
        images=[
            SimpleNamespace(
                index=i,
                timestamp_ns=round(t * 1e9),
                timestamp_source="camera_frame",
                metadata=SimpleNamespace(
                    sensor_orientation_degrees=90,
                    lens_facing=1,
                    target_rotation=0,
                    is_flipped=False,
                ),
            )
            for i, t in zip([4, 7, 9], [2, 3, 5], strict=True)
        ],
        sensor_streams={
            "accelerometer": stream(np.tile([0, 0, 9.81], (601, 1))),
            "gyroscope": stream(np.tile([0.001, -0.002, 0.003], (601, 1))),
            "magnetometer": stream(np.tile([0, 30, -40], (601, 1))),
            "rotation_vector": stream(np.tile([0, 0, 0, 1], (601, 1)), True),
        },
    )
    return session, scene


def test_compass_sets_only_one_world_alignment_and_later_compass_jumps_cannot_move_photos(
    heading_capture,
):
    session, scene = heading_capture
    result, trajectory, report = prepare_visual_heading(session, scene)
    assert report["compass"]["used"] and report["stationary_calibration"]["passed"]
    assert report["relative_image_orientation_source"] == "visual_reconstruction"
    changed = copy.deepcopy(session)
    for s in changed.sensor_streams["magnetometer"]:
        if s.timestamp_ns > 2e9:
            s.x, s.y, s.z = 70, -80, 200
    second, other, _ = prepare_visual_heading(changed, scene)
    for a, b in zip(result.cameras, second.cameras, strict=True):
        assert_allclose(a.extrinsics.R_cw, b.extrinsics.R_cw, atol=1e-14)
    assert_allclose(trajectory([2, 3, 5]).as_matrix(), other([2, 3, 5]).as_matrix(), atol=1e-14)


@pytest.mark.parametrize(
    "problem,reason",
    [
        ("missing", "magnetometer_not_recorded"),
        ("unreliable", "unreliable_compass_accuracy"),
        ("unstable", "unstable_magnetic_reference"),
        ("coverage", "insufficient_compass_reference_coverage"),
    ],
)
def test_missing_and_unreliable_compass_are_explicit_without_fabricating_north(
    heading_capture, problem, reason
):
    session, scene = heading_capture
    if problem == "missing":
        del session.sensor_streams["magnetometer"]
    elif problem == "unreliable":
        for s in session.sensor_streams["magnetometer"]:
            s.accuracy = 0
    elif problem == "unstable":
        for s in session.sensor_streams["magnetometer"][:100]:
            s.x = 60
    else:
        session.sensor_streams["magnetometer"] = session.sensor_streams["magnetometer"][200:]
    _, _, report = prepare_visual_heading(session, scene)
    assert not report["compass"]["used"]
    assert report["compass"]["reason"] == reason
    assert not report["metric_scale_accepted"]


def test_an_isolated_camera_aborts_before_any_orientation_fallback(heading_capture):
    session, scene = heading_capture
    # Remove image 9 from every observation; its camera remains selected.
    tracks = tuple(
        replace(t, observations=tuple(o for o in t.observations if o.image_id != 9))
        for t in scene.tracks
    )
    isolated = replace(scene, tracks=tracks)
    with pytest.raises(DisconnectedOverlapError, match=r"\[9\]"):
        prepare_visual_heading(session, isolated)
