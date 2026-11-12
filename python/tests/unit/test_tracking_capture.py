import hashlib
from copy import deepcopy

import cv2
import numpy as np
import pytest
from numpy.testing import assert_allclose

from distance_measuring.dataset import (
    DatasetFormatError,
    SessionValidationError,
    TrackingFrame,
    load_session,
    validate_session,
)
from distance_measuring.dataset.inspection import summarize_session
from distance_measuring.dataset.schema import CLOCK_DOMAIN


def frames(fixture):
    return [r for r in fixture.records if r["type"] == "frame"]


def codes(fixture):
    fixture.write()
    return validate_session(load_session(fixture.path, validate=False))


def test_sidecar_loads_without_mixing_preview_and_still_identities(hybrid_capture):
    paths = [p for p in hybrid_capture.path.rglob("*") if p.is_file()]
    hashes = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    session = load_session(hybrid_capture.path)
    assert len(session.images) == 3 and session.images[0].index == 0
    tracking = session.tracking
    assert tracking.schema_version == 1 and tracking.session_id == session.metadata.session_id
    assert [f.frame_id for f in tracking.frames] == [0, 2, 3]
    assert tracking.frames[0].timestamp_ns == 1_200_000_000
    assert session.images[0].timestamp_ns == 1_250_000_000
    assert tracking.frames[0].metadata.rotation_degrees == 90  # Stored pixels stay unrotated.
    assert tracking.frames[0].intrinsics.image_size == (40, 24)
    assert session.images[0].metadata.width == 80
    assert tracking.counts["dropped_frames"] == 1
    report = validate_session(session)
    assert report.ok
    assert "tracking_dropped_frames" in {w.code for w in report.warnings}
    summary = summarize_session(session, report)["tracking"]
    assert summary["observed_camera_rate_hz"] == pytest.approx(2 / 0.6)
    assert summary["max_frame_gap_s"] == pytest.approx(0.4)
    assert summary["capture_status_counts"] == {"requested": 1, "skipped": 1}
    assert summary["acquisition_loss_count"] is None
    assert hashes == {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def test_crop_sample_affine_matches_android_integer_sampling(hybrid_capture):
    frame = load_session(hybrid_capture.path).tracking.frames[0]
    native = np.arange(96 * 64, dtype=np.uint8).reshape(64, 96)
    pixels = cv2.imread(str(hybrid_capture.path / frame.filename), cv2.IMREAD_UNCHANGED)
    assert np.array_equal(pixels, native[4:52:2, 8:88:2])
    points = np.array([[8, 4, 1], [10, 6, 1], [86, 50, 1]])
    assert_allclose(points @ frame.buffer_to_saved_affine.T, [[0, 0, 1], [1, 1, 1], [39, 23, 1]])
    altered = deepcopy(frames(hybrid_capture)[0])
    altered["buffer_crop_region"] = [8, 4, 87, 51]  # Odd crop extents still ceil correctly.
    assert TrackingFrame.from_dict(altered, "frame").metadata.width == 40


def test_old_capture_load_and_summary_are_unchanged(capture):
    session = load_session(capture.path)
    assert session.tracking is None and len(session.images) == 3
    assert "tracking" not in summarize_session(session, validate_session(session))


@pytest.mark.parametrize("clock", ["camera_unknown", CLOCK_DOMAIN])
def test_unknown_clock_is_retained_without_an_invented_sensor_offset(hybrid_capture, clock):
    for row in frames(hybrid_capture):
        row["clock_domain"] = clock
        if clock == "camera_unknown":
            row["timestamp_ns"] += 90_000_000_000
    session = load_session(hybrid_capture.write())
    assert session.tracking.frames[0].timestamp_ns == (
        91_200_000_000 if clock == "camera_unknown" else 1_200_000_000
    )
    report = validate_session(session)
    assert report.ok
    assert ("tracking_unknown_clock" in {w.code for w in report.warnings}) == (
        clock == "camera_unknown"
    )


def test_missing_intrinsics_is_explicit_and_approximation_requires_provenance(hybrid_capture):
    for row in frames(hybrid_capture):
        row["intrinsics"] = None
        row["intrinsics_unavailable_reason"] = "preview_camera_metadata_missing"
    session = load_session(hybrid_capture.write())
    assert all(f.intrinsics is None for f in session.tracking.frames)
    assert "tracking_intrinsics_unavailable" in {w.code for w in validate_session(session).warnings}


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema_version", True),
        ("schema_version", 2),
        ("session_id", ""),
        ("state", "unknown"),
        ("pixel_format", "jpeg"),
        ("records_file", "events.jsonl"),
        ("records_file", "tracking/../events.jsonl"),
        ("records_file", "/tmp/events.jsonl"),
        ("settings", None),
        ("counts", []),
    ],
)
def test_malformed_manifest_is_rejected_with_its_location(hybrid_capture, field, value):
    hybrid_capture.manifest[field] = value
    hybrid_capture.write()
    with pytest.raises(DatasetFormatError, match=rf"tracking.json.*{field}"):
        load_session(hybrid_capture.path, validate=False)


@pytest.mark.parametrize(
    "field,value",
    [
        ("max_analysis_fps", "10"),
        ("max_analysis_fps", float("nan")),
        ("still_interval_s", 0),
        ("requested_width", True),
        ("writer_queue_capacity", 0),
    ],
)
def test_settings_are_strict_finite_numbers(hybrid_capture, field, value):
    hybrid_capture.manifest["settings"][field] = value
    hybrid_capture.write()
    with pytest.raises(DatasetFormatError, match="tracking.json"):
        load_session(hybrid_capture.path, validate=False)


@pytest.mark.parametrize(
    "field,value",
    [
        ("frame_id", -1),
        ("frame_id", True),
        ("timestamp_ns", 0),
        ("timestamp_ns", 1.5),
        ("clock_domain", None),
        ("clock_domain", "utc"),
        ("received_timestamp_ns", True),
        ("filename", "../outside.png"),
        ("filename", "tracking/frames/../../outside.png"),
        ("filename", "C:/outside.png"),
        ("filename", "tracking\\frames\\x.png"),
        ("filename", "images/image_000.jpg"),
        ("filename", "tracking/frames/x.jpg"),
        ("buffer_size", [0, 64]),
        ("buffer_size", [96]),
        ("buffer_crop_region", [8, 4, 97, 52]),
        ("buffer_crop_region", [8, 4, 8, 52]),
        ("buffer_crop_region", [-1, 4, 88, 52]),
        ("sample_step", 0),
        ("sample_step", True),
        ("metadata", {}),
        ("diagnostics", []),
    ],
)
def test_malformed_frame_fields_include_jsonl_line(hybrid_capture, field, value):
    frames(hybrid_capture)[0][field] = value
    hybrid_capture.write()
    with pytest.raises(DatasetFormatError, match=r"tracking/events.jsonl:2"):
        load_session(hybrid_capture.path, validate=False)


@pytest.mark.parametrize(
    "field,value",
    [
        ("width", 41),
        ("height", 23),
        ("rotation_degrees", 45),
        ("camera_id", None),
        ("is_flipped", None),
        ("rotation_degrees", None),
    ],
)
def test_frame_metadata_cannot_hide_layout_or_orientation(hybrid_capture, field, value):
    frames(hybrid_capture)[0]["metadata"][field] = value
    hybrid_capture.write()
    with pytest.raises(DatasetFormatError, match="metadata"):
        load_session(hybrid_capture.path, validate=False)


@pytest.mark.parametrize(
    "field,value",
    [
        ("pixel_convention", "display_pixels"),
        ("approximate", None),
        ("approximate", True),
        ("image_width", 80),
        ("image_height", True),
        ("camera_id", "other"),
        ("distortion", [1, 2, 3]),
        ("distortion", [0, 0, 0, float("inf")]),
        ("camera_matrix", [[True, 0, 0], [0, 1, 0], [0, 0, 1]]),
        ("camera_matrix", [[1, 2], [3, 4]]),
        ("assumptions", "unknown"),
        ("source", "invented"),
    ],
)
def test_intrinsics_reuse_camera_validation_and_require_saved_coordinate_provenance(
    hybrid_capture,
    field,
    value,
):
    frames(hybrid_capture)[0]["intrinsics"][field] = value
    hybrid_capture.write()
    with pytest.raises(DatasetFormatError, match="tracking/events.jsonl"):
        load_session(hybrid_capture.path, validate=False)


def test_approximate_intrinsics_and_null_intrinsics_both_require_an_explanation(hybrid_capture):
    first = frames(hybrid_capture)[0]
    first["intrinsics"].update(source="metadata-derived", approximate=True)
    hybrid_capture.write()
    with pytest.raises(DatasetFormatError, match="documented assumptions"):
        load_session(hybrid_capture.path)
    first["intrinsics"]["assumptions"] = ["Nominal sensor/crop with unknown distortion"]
    assert load_session(hybrid_capture.write()).tracking.frames[0].intrinsics.approximate
    first["intrinsics"] = None
    with pytest.raises(DatasetFormatError, match="intrinsics_unavailable_reason"):
        load_session(hybrid_capture.write())


@pytest.mark.parametrize(
    "kind,key,value",
    [
        ("drop", "count", 0),
        ("drop", "reason", ""),
        ("drop", "first_frame_id", True),
        ("reset", "next_frame_id", -1),
        ("reset", "reason", None),
        ("capture", "status", "saved"),
        ("capture", "scheduled_timestamp_ns", None),
    ],
)
def test_malformed_diagnostic_events_are_rejected(hybrid_capture, kind, key, value):
    next(r for r in hybrid_capture.records if r["type"] == kind)[key] = value
    with pytest.raises(DatasetFormatError, match="tracking/events.jsonl"):
        load_session(hybrid_capture.write(), validate=False)


@pytest.mark.parametrize(
    "mutation,expected",
    [
        (lambda f: f.manifest.update(session_id="another"), "tracking_session_id"),
        (lambda f: f.manifest.update(state="recording"), "tracking_unfinished"),
        (lambda f: f.manifest["counts"].update(frames=4), "tracking_counts"),
        (lambda f: f.records[1].update(sequence=0), "tracking_sequence"),
        (
            lambda f: f.records[1].update(recorded_timestamp_ns=1_140_000_000),
            "tracking_record_order",
        ),
        (lambda f: frames(f)[1].update(frame_id=0), "tracking_frame_order"),
        (lambda f: frames(f)[1].update(timestamp_ns=1_200_000_000), "tracking_camera_order"),
        (
            lambda f: frames(f)[0].update(received_timestamp_ns=1_000_000_000),
            "tracking_outside_timeline",
        ),
        (
            lambda f: frames(f)[0].update(received_timestamp_ns=1_230_000_000),
            "tracking_receive_time",
        ),
        (lambda f: frames(f)[0].update(timestamp_ns=1_210_000_000), "tracking_camera_time"),
        (lambda f: frames(f)[1].update(frame_id=20), "tracking_frame_ids"),
        (lambda f: f.records[2].update(first_frame_id=0), "tracking_frame_ids"),
        (lambda f: f.records[0].update(next_frame_id=100), "tracking_reset_frame"),
        (
            lambda f: frames(f)[0]["metadata"].update(sensor_timestamp_ns=100),
            "tracking_sensor_timestamp",
        ),
        (
            lambda f: frames(f)[0]["metadata"].update(
                sensor_timestamp_clock_domain="camera_unknown"
            ),
            "tracking_sensor_clock",
        ),
        (lambda f: f.records[4].update(image_index=99), "tracking_capture_request"),
        (
            lambda f: f.records[4].update(request_timestamp_ns=1_450_000_000),
            "tracking_capture_time",
        ),
        (
            lambda f: f.records[5].update(scheduled_timestamp_ns=1_480_000_000),
            "tracking_capture_order",
        ),
    ],
)
def test_semantic_errors_are_inspectable_but_default_loading_rejects_them(
    hybrid_capture,
    mutation,
    expected,
):
    mutation(hybrid_capture)
    report = codes(hybrid_capture)
    assert expected in {e.code for e in report.errors}
    with pytest.raises(SessionValidationError):
        load_session(hybrid_capture.path)


def test_unavailable_and_interrupted_modes_preserve_explicit_failure_reason(hybrid_capture):
    hybrid_capture.manifest.update(
        state="unavailable", failure_reason="analysis_stream_unsupported"
    )
    hybrid_capture.manifest["counts"] = dict.fromkeys(hybrid_capture.manifest["counts"], 0)
    hybrid_capture.records.clear()
    session = load_session(hybrid_capture.write())
    assert session.tracking.frames == ()
    assert "tracking_unavailable" in {w.code for w in validate_session(session).warnings}
    hybrid_capture.manifest.update(state="interrupted", failure_reason=None)
    with pytest.raises(DatasetFormatError, match="failure_reason"):
        load_session(hybrid_capture.write())


@pytest.mark.parametrize("kind", ["manifest", "records", "frame", "tracking_directory"])
def test_symlinks_cannot_escape_session(hybrid_capture, tmp_path, kind):
    relative = {
        "manifest": "tracking.json",
        "records": "tracking/events.jsonl",
        "frame": frames(hybrid_capture)[0]["filename"],
        "tracking_directory": "tracking",
    }[kind]
    target = hybrid_capture.path / relative
    outside = tmp_path / "outside"
    if target.is_dir():
        target.rename(outside)
        target.symlink_to(outside, target_is_directory=True)
    else:
        outside.write_bytes(target.read_bytes())
        target.unlink()
        target.symlink_to(outside)
    with pytest.raises((DatasetFormatError, SessionValidationError), match="outside the session"):
        load_session(hybrid_capture.path)


@pytest.mark.parametrize(
    "mode,expected",
    [
        ("missing", "tracking_frame_file"),
        ("broken", "tracking_frame_decode"),
        ("color", "tracking_frame_decode"),
        ("sixteen_bit", "tracking_frame_decode"),
        ("size", "tracking_frame_dimensions"),
        ("jpeg", "tracking_frame_decode"),
    ],
)
def test_pixels_are_checked_without_auto_orienting_or_converting_formats(
    hybrid_capture, mode, expected
):
    path = hybrid_capture.path / frames(hybrid_capture)[0]["filename"]
    if mode == "missing":
        path.unlink()
    elif mode == "broken":
        path.write_bytes(b"not a PNG")
    elif mode == "jpeg":
        _, pixels = cv2.imencode(".jpg", np.zeros((24, 40), np.uint8))
        path.write_bytes(pixels.tobytes())
    else:
        pixels = {
            "color": np.zeros((24, 40, 3), np.uint8),
            "sixteen_bit": np.zeros((24, 40), np.uint16),
            "size": np.zeros((40, 24), np.uint8),
        }[mode]
        assert cv2.imwrite(str(path), pixels)
    assert expected in {e.code for e in codes(hybrid_capture).errors}


def test_missing_manifest_or_records_are_not_silently_legacy_capture(hybrid_capture):
    (hybrid_capture.path / "tracking/events.jsonl").unlink()
    with pytest.raises(DatasetFormatError, match="events.jsonl"):
        load_session(hybrid_capture.path)
    (hybrid_capture.path / "tracking.json").unlink()
    with pytest.raises(DatasetFormatError, match="missing manifest"):
        load_session(hybrid_capture.path)


def test_duplicate_json_keys_and_nonfinite_values_keep_jsonl_locations(hybrid_capture):
    path = hybrid_capture.path / "tracking/events.jsonl"
    for line in [
        '{"type":"reset","type":"drop"}',
        '{"value":NaN}',
        '{"diagnostics":{"runtime_s":1e999}}',
        "broken",
    ]:
        path.write_text(line + "\n")
        with pytest.raises(DatasetFormatError, match=r"events.jsonl:1.*invalid JSON"):
            load_session(hybrid_capture.path)


def test_geometry_change_and_missing_gyro_coverage_are_explicit_not_invented(hybrid_capture):
    frames(hybrid_capture)[1]["metadata"]["physical_camera_id"] = "other_lens"
    timestamp = 1_400_000_000
    hybrid_capture.capture.rows = [
        r
        for r in hybrid_capture.capture.rows
        if r["sensor_type"] != "gyroscope"
        or not timestamp - 20_000_000 <= r["timestamp_ns"] <= timestamp + 20_000_000
    ]
    hybrid_capture.capture.metadata["sensor_counts"]["gyroscope"] = sum(
        r["sensor_type"] == "gyroscope" for r in hybrid_capture.capture.rows
    )
    report = codes(hybrid_capture)
    assert report.ok
    assert "tracking_geometry_changed" in {w.code for w in report.warnings}
    # The remaining 60 ms bracket is supported with the normal 250 ms budget,
    # but explicitly unavailable with a tighter 30 ms budget.
    report = validate_session(
        load_session(hybrid_capture.path, validate=False), max_sensor_gap_s=0.03
    )
    assert "tracking_gyro_coverage" in {w.code for w in report.warnings}


def test_frame_metadata_appends_may_finish_after_session_end(hybrid_capture):
    hybrid_capture.records[-1]["recorded_timestamp_ns"] = 2_100_000_000
    assert (
        load_session(hybrid_capture.write()).tracking.frames[-1].received_timestamp_ns
        < 2_000_000_000
    )


def test_duplicate_automatic_request_and_ambiguous_core_request_are_rejected(hybrid_capture):
    hybrid_capture.capture.events.insert(4, deepcopy(hybrid_capture.capture.events[3]))
    assert "tracking_capture_request" in {e.code for e in codes(hybrid_capture).errors}
    hybrid_capture.capture.events.pop(4)
    duplicate = deepcopy(hybrid_capture.records[4])
    duplicate["scheduled_timestamp_ns"] = 1_495_000_000
    hybrid_capture.records.insert(5, duplicate)
    assert "tracking_capture_request" in {e.code for e in codes(hybrid_capture).errors}


@pytest.mark.parametrize(
    "status,changes",
    [
        ("requested", {"reason": "busy"}),
        ("requested", {"request_timestamp_ns": None}),
        ("skipped", {"image_index": 1}),
        ("skipped", {"reason": None}),
    ],
)
def test_request_and_skip_records_do_not_conflate_captured_images(hybrid_capture, status, changes):
    record = next(r for r in hybrid_capture.records if r.get("status") == status)
    record.update(changes)
    with pytest.raises(DatasetFormatError, match="tracking/events.jsonl"):
        load_session(hybrid_capture.write(), validate=False)


def test_unknown_mount_and_explicit_reset_remain_visible(hybrid_capture):
    frames(hybrid_capture)[1]["metadata"]["lens_facing"] = 0
    report = codes(hybrid_capture)
    assert report.ok
    assert "tracking_mount_unavailable" in {w.code for w in report.warnings}
    # A reset covers this geometry change without inventing a usable gyro mount.
    hybrid_capture.records[0]["next_frame_id"] = 2
    assert all(
        "Frame 2:" not in w.message
        for w in codes(hybrid_capture).warnings
        if w.code == "tracking_geometry_changed"
    )


def test_drop_ranges_do_not_expand_large_id_sets_in_memory(hybrid_capture):
    hybrid_capture.records = [
        {
            "type": "drop",
            "sequence": 0,
            "recorded_timestamp_ns": 1_500_000_000,
            "first_frame_id": 0,
            "count": 10**12,
            "reason": "synthetic_aggregated_loss",
        }
    ]
    hybrid_capture.manifest["counts"].update(
        frames=0, dropped_frames=10**12, resets=0, capture_events=0
    )
    session = load_session(hybrid_capture.write())
    assert session.tracking.counts["dropped_frames"] == 10**12


def test_delayed_capture_ranges_count_skipped_ticks_without_inventing_requests(hybrid_capture):
    event = hybrid_capture.records[5]
    event.update(
        missed_tick_count=2,
        last_skipped_timestamp_ns=1_850_000_000,
        recorded_timestamp_ns=1_860_000_000,
        reason="scheduler_delayed",
    )
    hybrid_capture.records[-1]["recorded_timestamp_ns"] = 1_880_000_000
    hybrid_capture.manifest["settings"].update(
        still_interval_s=0.25, automatic_stills=True, diagnostic_queue_capacity=32
    )
    session = load_session(hybrid_capture.write())
    summary = summarize_session(session, validate_session(session))["tracking"]
    assert summary["capture_status_counts"] == {"requested": 1, "skipped": 1}
    assert summary["automatic_skipped_tick_count"] == 2
    assert summary["scheduler_missed_tick_count"] == 2


@pytest.mark.parametrize(
    "changes",
    [
        {"missed_tick_count": 0, "last_skipped_timestamp_ns": 1_600_000_000},
        {"missed_tick_count": True, "last_skipped_timestamp_ns": 1_600_000_000},
        {"missed_tick_count": 2},
        {"last_skipped_timestamp_ns": 1_600_000_000},
    ],
)
def test_aggregate_skip_fields_are_strict(hybrid_capture, changes):
    hybrid_capture.records[5].update(changes)
    with pytest.raises(DatasetFormatError, match="tracking/events.jsonl"):
        load_session(hybrid_capture.write())


@pytest.mark.parametrize("last,count", [(1_500_000_000, 2), (1_700_000_000, 1), (2_000_000_000, 2)])
def test_invalid_skip_ranges_remain_inspectable_errors(hybrid_capture, last, count):
    hybrid_capture.records[5].update(missed_tick_count=count, last_skipped_timestamp_ns=last)
    assert "tracking_capture_range" in {e.code for e in codes(hybrid_capture).errors}


@pytest.mark.parametrize(
    "key,value", [("automatic_stills", "yes"), ("diagnostic_queue_capacity", 0)]
)
def test_capture_export_settings_do_not_silently_change_types(hybrid_capture, key, value):
    hybrid_capture.manifest["settings"][key] = value
    with pytest.raises(DatasetFormatError, match=key):
        load_session(hybrid_capture.write())
