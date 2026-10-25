import pytest

from distance_measuring.dataset import load_session, validate_session
from distance_measuring.dataset.capture_geometry import summarize_capture_geometry
from distance_measuring.dataset.inspection import summarize_session


def settings(capture, **values):
    for image in capture.events:
        if image["type"] == "image":
            image["metadata"].update(
                physical_camera_id="wide",
                focus_distance_diopters=0.0,
                crop_region=[0, 0, 4000, 3000],
                ae_mode=1,
                ae_lock=False,
                awb_mode=1,
                awb_lock=True,
                af_mode=0,
                af_state=0,
                **values,
            )


def geometry(capture):
    capture.write()
    return summarize_capture_geometry(load_session(capture.path))


def test_constant_observed_lens_focus_and_crop_establish_stability(capture):
    settings(capture)
    report = geometry(capture)
    assert set(report["stability"].values()) == {"stable"}
    assert report["images"][0]["settings"]["focus_distance_diopters"] == 0
    assert report["images"][0]["settings"]["awb_lock"] is True
    assert not report["warnings"]


def test_changes_across_missing_observation_are_still_detected(capture):
    settings(capture)
    images = [e for e in capture.events if e["type"] == "image"]
    images[1]["metadata"].update(physical_camera_id=None, focus_distance_diopters=None)
    images[2]["metadata"].update(physical_camera_id="tele", focus_distance_diopters=2.0)
    report = geometry(capture)
    assert report["stability"]["lens"] == "changed"
    assert report["stability"]["focus"] == "changed"
    assert report["fields"]["physical_camera_id"]["known_image_ids"] == [0, 2]
    assert {w["message"].split()[0] for w in report["warnings"]} == {
        "physical_camera_id",
        "focus_distance_diopters",
    }


def test_missing_or_partial_metadata_does_not_claim_stability(capture):
    report = geometry(capture)
    assert report["stability"] == {
        "lens": "unknown",
        "focus": "unknown",
        "crop": "unknown",
        "focal_length": "stable",
    }
    settings(capture)
    capture.events[2]["metadata"]["crop_region"] = None
    assert geometry(capture)["stability"]["crop"] == "unknown"


def test_one_image_cannot_establish_sequence_stability(capture_factory):
    capture = capture_factory(image_count=1)
    settings(capture)
    assert set(geometry(capture)["stability"].values()) == {"unknown"}


def test_crop_and_zoom_are_both_checked_when_available(capture):
    settings(capture, zoom_ratio=1.0)
    capture.events[4]["metadata"]["zoom_ratio"] = 1.2
    assert geometry(capture)["stability"]["crop"] == "changed"


def test_changes_warn_without_invalidating_a_capture_and_preserve_clock_precision(capture):
    settings(capture)
    capture.events[4]["metadata"].update(
        ae_lock=True,
        iso=200,
        sensor_timestamp_ns=1_500_000_001,
        capture_result_source="exact_sensor_timestamp",
        capture_stability={"phase_at_request": "applied"},
    )
    capture.write()
    session = load_session(capture.path)
    report = summarize_session(session, validate_session(session))
    assert report["valid"]
    assert "camera_settings_changed" in {w["code"] for w in report["warnings"]}
    frame = report["capture_geometry"]["images"][1]
    assert frame["settings"]["sensor_timestamp_ns"] == 1_500_000_001
    assert frame["capture_stability"] == {"phase_at_request": "applied"}


@pytest.mark.parametrize("value", [True, -0.1, float("inf"), "0"])
def test_invalid_focus_distance_is_rejected(value):
    from distance_measuring.dataset.schema import DatasetFormatError, ImageMetadata

    with pytest.raises(DatasetFormatError, match="focus_distance_diopters"):
        ImageMetadata.from_dict({"focus_distance_diopters": value})


@pytest.mark.parametrize("value", [[0, 0, 0, 8], [-1, 0, 8, 8], [0, 0, 8], [0, 0, True, 8]])
def test_invalid_sensor_crop_is_rejected(value):
    from distance_measuring.dataset.schema import DatasetFormatError, ImageMetadata

    with pytest.raises(DatasetFormatError, match="crop_region"):
        ImageMetadata.from_dict({"crop_region": value})


@pytest.mark.parametrize("field,value", [("zoom_ratio", 0), ("ae_lock", 1), ("af_state", True)])
def test_camera2_fields_have_strict_types(field, value):
    from distance_measuring.dataset.schema import DatasetFormatError, ImageMetadata

    with pytest.raises(DatasetFormatError, match=field):
        ImageMetadata.from_dict({field: value})
