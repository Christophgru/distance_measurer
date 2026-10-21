from dataclasses import replace

import cv2
import pytest

from distance_measuring.dataset import SessionValidationError, load_session, validate_session


def error_codes(capture):
    capture.write()
    report = validate_session(load_session(capture.path, validate=False))
    return {issue.code for issue in report.errors}


def test_valid_capture_loads_coherently_without_rotating_pixels(capture):
    session = load_session(capture.path)
    assert session.metadata.session_id == "synthetic_capture"
    assert session.duration_s == 1
    assert len(session.sensor_samples) == 297
    assert len(session.images) == 3
    assert len(session.sensor_streams["accelerometer"]) == 99
    assert cv2.imread(str(session.image_path(session.images[0]))).shape[:2] == (80, 48)
    assert session.images[0].metadata.width == 80
    assert session.images[0].metadata.height == 48
    assert validate_session(session).ok


def test_five_images_are_allowed(capture_factory):
    capture = capture_factory(image_count=5)
    assert len(load_session(capture.path).images) == 5


def test_rotation_vector_unavailable_is_warning(capture_factory):
    capture = capture_factory(rotation_vector=False)
    session = load_session(capture.path)
    assert not session.sensor_streams["rotation_vector"]
    assert "sensor_unavailable" in {issue.code for issue in validate_session(session).warnings}


@pytest.mark.parametrize("timestamp", [999_999_999, 1_000_000_000, 2_000_000_000, 2_000_000_001])
def test_image_must_be_strictly_inside_timeline(capture, timestamp):
    capture.events[2]["timestamp_ns"] = timestamp
    assert "event_outside_timeline" in error_codes(capture)


def test_start_must_precede_end(capture):
    capture.events[-1]["timestamp_ns"] = capture.events[0]["timestamp_ns"]
    assert "timeline_order" in error_codes(capture)


@pytest.mark.parametrize("kind", ["timeline_start", "timeline_end"])
def test_missing_and_duplicate_boundaries(capture, kind):
    original = next(event for event in capture.events if event["type"] == kind)
    capture.events.remove(original)
    assert "timeline_boundaries" in error_codes(capture)
    capture.events.extend([original, original])
    assert "timeline_boundaries" in error_codes(capture)


def test_metadata_boundaries_must_agree(capture):
    capture.metadata["timeline_end_ns"] += 1
    assert "timeline_metadata" in error_codes(capture)


def test_global_sensor_timestamp_decrease(capture):
    capture.rows[0], capture.rows[3] = capture.rows[3], capture.rows[0]
    assert "sensor_order" in error_codes(capture)


def test_equal_timestamps_allowed_across_streams_but_not_within_stream(capture):
    assert load_session(capture.path)
    capture.rows.insert(1, capture.rows[0].copy())
    assert "sensor_stream_order" in error_codes(capture)


@pytest.mark.parametrize("timestamp", [1_000_000_000, 2_000_000_000])
def test_sensor_must_be_inside_timeline(capture, timestamp):
    capture.rows[0]["timestamp_ns"] = timestamp
    assert "sensor_outside_timeline" in error_codes(capture)


@pytest.mark.parametrize("side", ["before", "after"])
def test_sensor_coverage_on_both_sides_of_image(capture, side):
    image_time = capture.events[2]["timestamp_ns"]
    capture.rows = [
        row
        for row in capture.rows
        if row["sensor_type"] != "gyroscope"
        or (
            row["timestamp_ns"] >= image_time
            if side == "before"
            else row["timestamp_ns"] <= image_time
        )
    ]
    assert "sensor_coverage" in error_codes(capture)


def test_sensor_gap_near_image_is_an_error(capture):
    capture.rows = [
        row
        for row in capture.rows
        if row["sensor_type"] != "gyroscope"
        or not 1_100_000_000 <= row["timestamp_ns"] <= 1_400_000_000
    ]
    assert "sensor_coverage_gap" in error_codes(capture)
    session = load_session(capture.path, validate=False)
    assert "sensor_coverage_gap" not in {
        issue.code for issue in validate_session(session, max_sensor_gap_s=0.5).errors
    }


def test_enabled_sensor_cannot_be_empty(capture):
    capture.rows = [row for row in capture.rows if row["sensor_type"] != "gyroscope"]
    assert "empty_sensor_stream" in error_codes(capture)


def test_disabled_sensor_cannot_have_samples(capture):
    capture.metadata["sensors"]["gyroscope"] = False
    assert "sensor_availability" in error_codes(capture)


def test_missing_image(capture):
    (capture.path / "images/image_000.jpg").unlink()
    assert "missing_image" in error_codes(capture)


def test_undecodable_image(capture):
    (capture.path / "images/image_000.jpg").write_bytes(b"not a JPEG")
    assert "image_decode" in error_codes(capture)


def test_stored_dimensions_must_match_metadata(capture):
    capture.events[2]["metadata"]["width"] = 900
    assert "image_dimensions" in error_codes(capture)


def test_symlink_cannot_escape_session(capture, tmp_path):
    path = capture.path / "images/image_000.jpg"
    outside = tmp_path / "outside.jpg"
    outside.write_bytes(path.read_bytes())
    path.unlink()
    path.symlink_to(outside)
    assert "image_path" in error_codes(capture)


def test_image_times_and_indices_must_increase(capture):
    capture.events[4]["timestamp_ns"] = capture.events[2]["timestamp_ns"]
    capture.events[4]["index"] = capture.events[2]["index"]
    assert {"image_order", "image_indices"} <= error_codes(capture)


def test_duplicate_image_filename(capture):
    capture.events[4]["filename"] = capture.events[2]["filename"]
    assert "duplicate_filename" in error_codes(capture)


@pytest.mark.parametrize("callback", [1_249_999_999, 2_000_000_000])
def test_image_callback_time(capture, callback):
    capture.events[2]["callback_timestamp_ns"] = callback
    assert "image_callback_time" in error_codes(capture)


def test_primary_timestamp_matches_request(capture):
    capture.events[2]["request_timestamp_ns"] += 1
    assert "image_request_time" in error_codes(capture)


@pytest.mark.parametrize("target", ["session", "image"])
def test_clock_domain_mismatch(capture, target):
    if target == "session":
        capture.metadata["clock_domain"] = "wall_clock"
    else:
        capture.events[2]["clock_domain"] = "wall_clock"
    assert ("clock_domain" if target == "session" else "image_clock_domain") in error_codes(capture)


@pytest.mark.parametrize("field", ["image_count", "sensor_counts"])
def test_declared_counts_must_match_files(capture, field):
    if field == "image_count":
        capture.metadata[field] = 500
    else:
        capture.metadata[field]["gyroscope"] = 500
    assert ("image_count" if field == "image_count" else "sensor_count") in error_codes(capture)


def test_unfinished_state_is_error_and_interruption_is_warning(capture):
    capture.metadata["state"] = "recording"
    assert "unfinished_session" in error_codes(capture)
    capture.metadata["state"] = "interrupted"
    capture.write()
    assert "interrupted_session" in {
        issue.code for issue in validate_session(load_session(capture.path)).warnings
    }


def test_missing_optional_metadata_is_visible(capture):
    capture.events[2]["metadata"] = {}
    capture.write()
    report = validate_session(load_session(capture.path))
    assert report.ok
    assert "missing_image_metadata" in {issue.code for issue in report.warnings}


def test_older_capture_with_unspecified_session_clock_warns(capture):
    del capture.metadata["clock_domain"]
    del capture.metadata["state"]
    capture.write()
    report = validate_session(load_session(capture.path))
    assert {"missing_clock_domain", "missing_state"} <= {issue.code for issue in report.warnings}


def test_load_validates_by_default_and_preserves_diagnostic_report(capture):
    capture.metadata["image_count"] = 10
    capture.write()
    with pytest.raises(SessionValidationError, match="image_count") as error:
        load_session(capture.path)
    assert error.value.report.errors
    assert not validate_session(load_session(capture.path, validate=False)).ok


def test_renamed_directory_keeps_original_session_identity(capture):
    session = load_session(capture.path)
    assert session.path.name != session.metadata.session_id


@pytest.mark.parametrize("gap", [0, -1, float("nan"), float("inf")])
def test_gap_threshold_must_be_positive(capture, gap):
    with pytest.raises(ValueError, match="positive finite"):
        validate_session(load_session(capture.path), max_sensor_gap_s=gap)


def test_original_session_is_not_sorted_or_modified(capture):
    session = load_session(capture.path)
    changed = replace(session, sensor_samples=tuple(reversed(session.sensor_samples)))
    report = validate_session(changed)
    assert not report.ok
    assert changed.sensor_samples[0] == session.sensor_samples[-1]
