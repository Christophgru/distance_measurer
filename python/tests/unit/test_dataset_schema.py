import pytest

from distance_measuring.dataset import DatasetFormatError, load_session
from distance_measuring.dataset.schema import ImageEvent, ImageMetadata, SessionMetadata


@pytest.mark.parametrize("field", ["schema_version", "session_id", "device", "sensors"])
def test_missing_session_field_has_source_location(capture, field):
    del capture.metadata[field]
    capture.write()
    with pytest.raises(DatasetFormatError, match=rf"session.json.*{field}"):
        load_session(capture.path)


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema_version", True),
        ("schema_version", 2),
        ("session_id", ""),
        ("device", []),
        ("image_count", -1),
        ("timeline_start_ns", 1.5),
        ("dropped_sensor_samples", "5"),
        ("state", "unknown"),
    ],
)
def test_rejects_invalid_session_fields(capture, field, value):
    capture.metadata[field] = value
    with pytest.raises(DatasetFormatError, match=field):
        SessionMetadata.from_dict(capture.metadata)


def test_sensor_availability_must_be_boolean(capture):
    capture.metadata["sensors"]["accelerometer"] = "true"
    capture.write()
    with pytest.raises(DatasetFormatError, match="sensors.accelerometer.*boolean"):
        load_session(capture.path)


@pytest.mark.parametrize(
    "field,value",
    [
        ("index", True),
        ("index", -1),
        ("filename", ""),
        ("timestamp_ns", 1.5),
        ("callback_timestamp_ns", "2000000000"),
        ("metadata", []),
    ],
)
def test_invalid_image_fields(capture, field, value):
    event = capture.events[2].copy()
    event[field] = value
    with pytest.raises(DatasetFormatError, match=field):
        ImageEvent.from_dict(event)


@pytest.mark.parametrize(
    "filename",
    [
        "../outside.jpg",
        "/outside.jpg",
        "images/../outside.jpg",
        "C:/outside.jpg",
        "images\\outside.jpg",
        "images//x.jpg",
        "images/\x00bad.jpg",
    ],
)
def test_image_paths_cannot_escape_session(capture, filename):
    capture.events[2]["filename"] = filename
    capture.write()
    with pytest.raises(DatasetFormatError, match="safe relative path"):
        load_session(capture.path)


@pytest.mark.parametrize(
    "field,value",
    [
        ("width", 0),
        ("height", True),
        ("focal_length_mm", float("nan")),
        ("exposure_time_s", -1),
        ("iso", float("inf")),
        ("camera_id", 0),
        ("rotation_degrees", 45),
        ("exif_orientation", 9),
        ("is_flipped", 1),
        ("focal_length_35mm", -1),
        ("target_rotation", 4),
        ("lens_facing", 3),
        ("sensor_orientation_degrees", 45),
        ("metadata_source", 7),
    ],
)
def test_invalid_image_metadata(field, value):
    with pytest.raises(DatasetFormatError, match=field):
        ImageMetadata.from_dict({field: value})


@pytest.mark.parametrize(
    "field,value",
    [
        ("timestamp_ns", "1.5"),
        ("timestamp_ns", "-1"),
        ("sensor_type", "unknown"),
        ("x", "nan"),
        ("y", "inf"),
        ("z", "bad"),
        ("accuracy", "4"),
        ("w", "1"),
    ],
)
def test_malformed_csv_fields_include_line_number(capture, field, value):
    capture.rows[0][field] = value
    capture.write()
    with pytest.raises(DatasetFormatError, match=rf"sensors.csv:2.*{field}"):
        load_session(capture.path)


def test_rotation_vector_requires_w(capture):
    next(row for row in capture.rows if row["sensor_type"] == "rotation_vector")["w"] = ""
    capture.write()
    with pytest.raises(DatasetFormatError, match="w: required"):
        load_session(capture.path)


def test_optional_heading_streams_load_alongside_legacy_capture(capture):
    for name in ("magnetometer", "game_rotation_vector"):
        capture.metadata["sensors"][name] = True
        capture.metadata["sensor_counts"][name] = 99
        for row in list(capture.rows):
            if row["sensor_type"] == "rotation_vector":
                capture.rows.append(
                    dict(
                        row,
                        sensor_type=name,
                        x=0,
                        y=30 if name == "magnetometer" else 0,
                        z=-40 if name == "magnetometer" else 0,
                        w="" if name == "magnetometer" else 1,
                    )
                )
    capture.rows.sort(key=lambda r: r["timestamp_ns"])
    capture.write()
    session = load_session(capture.path)
    assert len(session.sensor_streams["magnetometer"]) == 99
    assert session.sensor_streams["game_rotation_vector"][0].w == 1


def test_undeclared_optional_sensor_is_rejected_by_validation(capture):
    from distance_measuring.dataset import SessionValidationError

    row = next(r for r in capture.rows if r["sensor_type"] == "accelerometer")
    capture.rows.insert(0, dict(row, sensor_type="magnetometer"))
    capture.write()
    with pytest.raises(SessionValidationError, match="declared unavailable"):
        load_session(capture.path)


@pytest.mark.parametrize(
    "content",
    [
        '{"schema_version": 1, "schema_version": 1}',
        '{"value": NaN}',
        '{"value": 1e999}',
        "{invalid}",
        "[]",
    ],
)
def test_invalid_json_is_a_format_error(capture, content):
    (capture.path / "session.json").write_text(content)
    with pytest.raises(DatasetFormatError, match="session.json"):
        load_session(capture.path)


def test_bad_jsonl_reports_line(capture):
    with (capture.path / "events.jsonl").open("a") as output:
        output.write("bad json\n")
    with pytest.raises(DatasetFormatError, match=r"events.jsonl:9.*invalid JSON"):
        load_session(capture.path)


@pytest.mark.parametrize("filename", ["session.json", "sensors.csv", "events.jsonl"])
def test_missing_required_file(capture, filename):
    (capture.path / filename).unlink()
    with pytest.raises(DatasetFormatError, match=filename):
        load_session(capture.path)


def test_missing_session_directory(tmp_path):
    with pytest.raises(DatasetFormatError, match="directory does not exist"):
        load_session(tmp_path / "missing")


@pytest.mark.parametrize(
    "header,row",
    [
        ("timestamp_ns,x", "1,2"),
        ("timestamp_ns,sensor_type,x,y,z,w,accuracy", "1,accelerometer,0,0,9.81,,3,extra"),
        ("timestamp_ns,sensor_type,x,y,z,w,accuracy", "1,accelerometer,0"),
    ],
)
def test_invalid_csv_structure(capture, header, row):
    (capture.path / "sensors.csv").write_text(header + "\n" + row + "\n")
    with pytest.raises(DatasetFormatError, match="sensors.csv"):
        load_session(capture.path)
