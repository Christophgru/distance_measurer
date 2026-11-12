from dataclasses import replace
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
from numpy.testing import assert_allclose
from scipy.spatial.transform import Rotation

from distance_measuring.camera import resolve_intrinsics
from distance_measuring.dataset import load_session
from distance_measuring.dataset.schema import capture_path
from distance_measuring.vision.hybrid import (
    HybridConfig,
    PreviewSnapshot,
    associate_still,
    rotation_between,
)
from distance_measuring.vision.hybrid_diagnostics import capture_timing_diagnostics
from distance_measuring.vision.tracking import TrackingConfig, TrackObservation, track_points


def snapshots(data, session, event):
    before = max(
        (f for f in session.tracking.frames if f.timestamp_ns <= event.timestamp_ns),
        key=lambda f: f.timestamp_ns,
    )
    after = min(
        (f for f in session.tracking.frames if f.timestamp_ns >= event.timestamp_ns),
        key=lambda f: f.timestamp_ns,
    )
    result = []
    for frame in (before, after):
        pixels = frame.intrinsics.project(data.world, data.pose(frame.timestamp_ns))
        active = {
            i: TrackObservation(frame.frame_id, i, tuple(p), 2, 1, "tracked")
            for i, p in enumerate(pixels)
            if (p > 12).all() and (p < np.array(frame.intrinsics.image_size) - 12).all()
        }
        gray = cv2.imread(str(capture_path(session.path, frame.filename)), cv2.IMREAD_GRAYSCALE)
        result.append(PreviewSnapshot(frame, frame.intrinsics, gray, active, 0))
    return result


@pytest.mark.parametrize("cropped", [False, True])
def test_jpeg_pixels_are_measured_under_its_own_intrinsics_not_copied_from_preview(
    hybrid_scene_factory,
    cropped,
):
    data = hybrid_scene_factory(cropped_jpeg=cropped)
    session = load_session(data.path)
    event = session.images[1]
    before, after = snapshots(data, session, event)
    raw = cv2.imread(str(session.image_path(event)), cv2.IMREAD_GRAYSCALE)
    original = resolve_intrinsics(event.metadata, calibration_path=data.calibration)
    matches, report = associate_still(session, event, original, np.eye(3), raw, before, after)
    assert report["status"] == "verified_2d", report
    assert len(matches) >= 16
    ids = sorted(matches)
    actual = np.array([matches[i]["pixel"] for i in ids])
    truth = data.jpeg_camera.project(data.world[ids], data.pose(event.timestamp_ns))
    assert np.median(np.linalg.norm(actual - truth, axis=1)) < 1
    nearest_preview_scaled = np.array([before.observations[i].pixel for i in ids]) * 2
    assert np.median(np.linalg.norm(actual - nearest_preview_scaled, axis=1)) > 2
    assert all(r["gyro_prior_available"] for r in report["branches"])


def test_blank_or_wrong_jpeg_content_cannot_become_a_pose_from_its_timestamp(hybrid_scene_factory):
    data = hybrid_scene_factory()
    session = load_session(data.path)
    event = session.images[1]
    before, after = snapshots(data, session, event)
    matches, report = associate_still(
        session,
        event,
        data.jpeg_camera,
        np.eye(3),
        np.full((480, 640), 65, np.uint8),
        before,
        after,
    )
    assert not matches and report["status"] == "unsupported"


def test_pixel_center_resize_rejects_measurements_outside_working_jpeg(
    hybrid_scene_factory, monkeypatch
):
    data = hybrid_scene_factory()
    session = load_session(data.path)
    event = session.images[1]
    before, after = snapshots(data, session, event)

    def edge_flow(first, second, points, config, **kwargs):
        return SimpleNamespace(
            points=np.tile([319.9, 100], (len(points), 1)),
            backward_points=points.copy(),
            accepted=np.ones(len(points), bool),
            lk_error=np.zeros(len(points)),
            geometry_initialization=None,
            runtime_s=0.0,
            forward_runtime_s=0.0,
            backward_runtime_s=0.0,
            motion_initialization=None,
        )

    monkeypatch.setattr("distance_measuring.vision.hybrid.track_points", edge_flow)
    matches, report = associate_still(
        session,
        event,
        data.jpeg_camera,
        np.eye(3),
        np.zeros((480, 640), np.uint8),
        before,
        after,
    )
    assert not matches and report["status"] == "unsupported"
    assert all(b["outside_working_image_count"] == b["input_count"] for b in report["branches"])


def test_reverse_gyro_interval_and_actual_exposure_offset_have_correct_signs(hybrid_scene_factory):
    data = hybrid_scene_factory(mode="rotation")
    session = load_session(data.path)
    event = session.images[1]
    before, after = snapshots(data, session, event)
    for snapshot in (before, after):
        rotation, _ = rotation_between(session, snapshot.frame, event)
        dt = (event.timestamp_ns - snapshot.frame.timestamp_ns) / 1e9
        assert_allclose(rotation, Rotation.from_rotvec([0, 0, 0.15 * dt]).as_matrix(), atol=1e-10)


@pytest.mark.parametrize(
    "fault,reason",
    [
        ("segment", "preview_segment_boundary"),
        ("time", "still_outside_preview_bracket"),
        ("request", "still_exposure_timestamp_unavailable"),
        ("clock", "still_camera_clock_unknown"),
    ],
)
def test_unsupported_time_or_segment_does_not_invent_identity_rotation_or_coordinates(
    hybrid_scene_factory,
    fault,
    reason,
):
    data = hybrid_scene_factory()
    session = load_session(data.path)
    event = session.images[1]
    before, after = snapshots(data, session, event)
    if fault == "segment":
        after.segment_id = 1
    if fault == "time":
        event = replace(
            event,
            timestamp_ns=event.timestamp_ns + 1_000_000_000,
            metadata=replace(
                event.metadata, sensor_timestamp_ns=event.timestamp_ns + 1_000_000_000
            ),
        )
    if fault == "request":
        event = replace(event, timestamp_source="button_request")
    if fault == "clock":
        event = replace(event, clock_domain="camera_unknown")
    matches, report = associate_still(
        session, event, data.jpeg_camera, np.eye(3), np.zeros((480, 640), np.uint8), before, after
    )
    assert not matches and report["reason"] == reason


@pytest.mark.parametrize("warp", [np.zeros((3, 3)), np.eye(2), np.full((3, 3), np.nan)])
def test_optical_flow_rejects_invalid_geometry_search_priors(warp):
    with pytest.raises(ValueError, match="invertible"):
        track_points(
            np.zeros((64, 64), np.uint8),
            np.zeros((64, 64), np.uint8),
            np.empty((0, 2)),
            initial_warp=warp,
        )


def test_valid_geometry_prior_still_requires_image_flow_and_reports_all_work():
    rng = np.random.default_rng(4)
    first = cv2.resize(rng.integers(0, 255, (60, 80), np.uint8), (320, 240))
    warp = np.array([[1.0, 0, 30], [0, 1, 5], [0, 0, 1]])
    second = cv2.warpPerspective(first, warp, (320, 240))
    points = np.array([(x, y) for x in range(40, 240, 30) for y in range(40, 190, 30)], float)
    result = track_points(first, second, points, TrackingConfig(), initial_warp=warp)
    assert result.accepted.sum() > 20
    assert (
        np.median(
            np.linalg.norm(
                result.points[result.accepted] - (points[result.accepted] + [30, 5]), axis=1
            )
        )
        < 0.5
    )
    assert result.backward_points.shape == points.shape
    assert result.geometry_initialization is not None
    assert result.runtime_s >= result.forward_runtime_s + result.backward_runtime_s


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_preview_gap_s": 0},
        {"cross_stream_agreement_px": float("nan")},
        {"min_still_correspondences": 7},
    ],
)
def test_invalid_hybrid_configuration_is_rejected(kwargs):
    with pytest.raises(ValueError):
        HybridConfig(**kwargs)


def test_capture_timing_distinguishes_exposure_latency_and_photo_overlapping_pause(
    hybrid_scene_factory,
):
    data = hybrid_scene_factory(missing_frames=(3, 4, 5))
    session = load_session(data.path)
    timing = capture_timing_diagnostics(session, max_preview_gap_s=0.25)
    assert timing["preview_gaps_s"]["max"] == pytest.approx(0.4)
    assert timing["large_gap_count"] == 1
    assert timing["request_to_exposure_s"]["median"] == pytest.approx(0.03)
    assert timing["request_to_callback_s"]["median"] == pytest.approx(0.035)
    assert timing["preview_gaps_during_jpeg_requests_s"]["max"] == pytest.approx(0.4)
    assert timing["per_still"][1]["overlapping_large_gap_count"] == 1
    assert timing["stored_live_diagnostics"]["active_tracks"]["count"] == 0


def test_unknown_preview_clocks_are_excluded_from_capture_pause_measurement(hybrid_scene_factory):
    data = hybrid_scene_factory(unknown_clock=True)
    timing = capture_timing_diagnostics(load_session(data.path), max_preview_gap_s=0.25)
    assert timing["preview_gaps_s"]["count"] == 0
    assert timing["preview_gaps_during_jpeg_requests_s"]["max"] is None


def test_request_timestamp_fallback_is_never_reported_as_an_exposure(hybrid_scene_factory):
    session = load_session(hybrid_scene_factory().path)
    session = replace(
        session,
        events=tuple(
            replace(event, timestamp_source="button_request") if event.type == "image" else event
            for event in session.events
        ),
    )
    timing = capture_timing_diagnostics(session, max_preview_gap_s=0.25)
    assert timing["exposure_intervals_s"]["count"] == 0
    assert timing["request_to_exposure_s"]["median"] is None
    assert all(p["exposure_timestamp_ns"] is None for p in timing["per_still"])
    assert timing["request_to_callback_s"]["count"] == 3
