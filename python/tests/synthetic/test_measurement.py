from copy import deepcopy

import numpy as np
import pytest

from distance_measuring.camera import CameraExtrinsics, CameraIntrinsics
from distance_measuring.measurement import create_annotations, evaluate_measurements
from distance_measuring.scene import Scene, SceneCamera
from distance_measuring.scene.model import COORDINATE_FRAME
from distance_measuring.visualization.measurement_review import display_rotation


@pytest.fixture
def measurement_case():
    k = CameraIntrinsics(1000, 1000, 799.5, 599.5, 1600, 1200)
    cameras = tuple(SceneCamera(i, k, CameraExtrinsics(np.eye(3), [i - 1, 0, 0])) for i in range(3))
    scene = Scene(
        cameras, (), (), {"session_id": "test", "quality_status": "visual_validation_passed"}
    )
    xyz = {"ref_a": [0, 0, 6], "ref_b": [1, 0, 6], "test_a": [0.2, 0.3, 6], "test_b": [0.7, 0.3, 6]}
    annotations = {
        "schema_version": 1,
        "session_id": "test",
        "coordinate_frame": COORDINATE_FRAME,
        "image_sizes": {str(c.image_id): list(k.image_size) for c in cameras},
        "points": {
            name: {
                "observations": {
                    str(c.image_id): {
                        "pixel": k.project(p, c.extrinsics).tolist(),
                        "reviewed": True,
                    }
                    for c in cameras
                }
            }
            for name, p in xyz.items()
        },
        "reference": {"endpoints": ["ref_a", "ref_b"], "length_m": 0.1},
        "measurements": [
            {"id": "held", "endpoints": ["test_a", "test_b"], "expected_length_m": 0.05}
        ],
        "view_groups": [[0, 1], [1, 2]],
        "localization_radius_px": 0.01,
    }
    return scene, annotations


def test_known_scale_and_held_out_length_repeat_without_mutating_cameras(measurement_case):
    scene, annotations = measurement_case
    before = deepcopy(scene.to_dict())
    report = evaluate_measurements(scene, annotations, sensitivity_trials=20)
    assert report["prototype_checks_passed"]
    assert report["blocking_reasons"] == []
    assert not report["metric_measurement_accepted"]
    for g in report["view_groups"]:
        assert g["reference_scale_m_per_unit"] == pytest.approx(0.1)
        assert g["measurements"][0]["length_m"] == pytest.approx(0.05)
        assert g["sensitivity_geometry_accepted_trials"] == 20
    assert report["repeatability"][0]["range_over_median_percent"] < 1e-9
    assert report["repeatability"][0]["shared_cameras_between_groups"] == [[1]]
    assert scene.to_dict() == before


def test_known_validation_length_cannot_change_scale_or_camera_fit(measurement_case):
    scene, annotations = measurement_case
    annotations["measurements"][0]["expected_length_m"] = 0.1
    report = evaluate_measurements(scene, annotations, sensitivity_trials=20)
    assert "held_out_error_over_10_percent" in report["blocking_reasons"]
    assert all(
        g["measurements"][0]["length_m"] == pytest.approx(0.05) for g in report["view_groups"]
    )


def test_secondary_views_cannot_hide_scale_drift_by_refitting_reference(measurement_case):
    scene, annotations = measurement_case
    cameras = (
        *scene.cameras[:2],
        SceneCamera(2, scene.cameras[2].intrinsics, CameraExtrinsics(np.eye(3), [2, 0, 0])),
    )
    shifted = Scene(cameras, (), (), scene.metadata)
    report = evaluate_measurements(shifted, annotations, sensitivity_trials=20)
    first, second = report["view_groups"]
    assert first["reference_scale_m_per_unit"] == pytest.approx(0.1)
    assert second["reference_scale_m_per_unit"] == pytest.approx(0.1)
    assert second["diagnostic_local_reference_scale_m_per_unit"] == pytest.approx(0.05)
    assert second["reference_remeasured_length_m"] == pytest.approx(0.2)
    assert second["measurements"][0]["length_m"] == pytest.approx(0.1)
    assert "reference_repeatability_over_10_percent" in report["blocking_reasons"]
    assert not report["scale_refit_per_group"]


def test_missing_primary_reference_never_falls_back_to_more_flattering_group(measurement_case):
    scene, annotations = measurement_case
    del annotations["points"]["ref_a"]["observations"]["0"]
    report = evaluate_measurements(scene, annotations, sensitivity_trials=20)
    assert report["fixed_scale_m_per_unit"] is None
    assert report["view_groups"][1]["geometry_passed"]
    assert report["view_groups"][1]["measurements"] == []
    assert "primary_reference_geometry_unavailable" in report["blocking_reasons"]


def test_inconsistent_third_view_is_rejected_and_never_silently_dropped(measurement_case):
    scene, annotations = measurement_case
    annotations["points"]["test_a"]["observations"]["2"]["pixel"][1] += 80
    report = evaluate_measurements(scene, annotations, sensitivity_trials=20)
    assert report["view_groups"][0]["geometry_passed"]
    assert not report["view_groups"][1]["geometry_passed"]
    assert report["view_groups"][1]["measurements"] == []
    assert "triangulation_rejected" in report["blocking_reasons"]
    assert "additional_view_repeatability_missing" in report["blocking_reasons"]


def test_missing_mark_and_unreviewed_mark_block_success(measurement_case):
    scene, annotations = measurement_case
    annotations["points"]["test_a"]["observations"]["0"]["reviewed"] = False
    del annotations["points"]["test_b"]["observations"]["2"]
    report = evaluate_measurements(scene, annotations, sensitivity_trials=20)
    assert {
        "observations_unreviewed",
        "missing_observations",
        "additional_view_repeatability_missing",
    } <= set(report["blocking_reasons"])
    assert report["view_groups"][1]["missing_observations"] == [["test_b", 2]]


def test_one_pair_and_unverified_scene_never_imply_accuracy(measurement_case):
    scene, annotations = measurement_case
    scene.metadata["quality_status"] = "locally_bundle_adjusted_unverified"
    annotations["view_groups"] = [[0, 1]]
    report = evaluate_measurements(scene, annotations, sensitivity_trials=20)
    assert set(report["blocking_reasons"]) == {
        "visual_cameras_unverified",
        "additional_view_repeatability_missing",
    }


def test_pixel_sensitivity_exposes_unstable_lengths(measurement_case):
    scene, annotations = measurement_case
    annotations["localization_radius_px"] = 20
    report = evaluate_measurements(scene, annotations, sensitivity_trials=40)
    assert "sensitivity_geometry_unstable" in report["blocking_reasons"]
    assert not report["prototype_checks_passed"]


@pytest.mark.parametrize(
    "change",
    [
        "overlap",
        "same_reference",
        "duplicate_group",
        "bad_size",
        "bad_session",
        "nan_pixel",
        "outside_pixel",
        "review_string",
    ],
)
def test_annotation_contract_prevents_invalid_or_circular_validation(measurement_case, change):
    scene, data = measurement_case
    if change == "overlap":
        data["measurements"][0]["endpoints"][0] = "ref_a"
    elif change == "same_reference":
        data["measurements"][0]["endpoints"] = ["ref_b", "ref_a"]
    elif change == "duplicate_group":
        data["view_groups"] = [[0, 1], [1, 0]]
    elif change == "bad_size":
        data["image_sizes"]["0"] = [1200, 1600]
    elif change == "bad_session":
        data["session_id"] = "another"
    else:
        obs = data["points"]["test_a"]["observations"]["0"]
        if change == "nan_pixel":
            obs["pixel"][0] = float("nan")
        elif change == "outside_pixel":
            obs["pixel"][0] = 1600
        else:
            obs["reviewed"] = "yes"
    with pytest.raises(ValueError):
        evaluate_measurements(scene, data, sensitivity_trials=20)


@pytest.mark.parametrize(
    "orientation,expected", [(1, [2, 3]), (3, [7, 4]), (6, [4, 2]), (8, [3, 7])]
)
def test_display_rotation_uses_integer_camera_pixel_centers(orientation, expected):
    matrix = display_rotation(10, 8, orientation)
    point = matrix @ [2, 3, 1]
    np.testing.assert_allclose(point[:2], expected)
    np.testing.assert_allclose(np.linalg.inv(matrix) @ point, [2, 3, 1])


@pytest.mark.parametrize(
    "change",
    [
        {"schema_version": True},
        {"image_sizes": None},
        {"image_sizes": [[1600, 1200]]},
        {"reference": None},
        {"reference": []},
        {"measurements": [None]},
        {"measurements": [[]]},
    ],
)
def test_malformed_annotation_objects_raise_value_error(measurement_case, change):
    scene, annotations = measurement_case
    annotations.update(change)
    with pytest.raises(ValueError):
        evaluate_measurements(scene, annotations, sensitivity_trials=20)


def test_new_annotations_are_empty_and_do_not_claim_scale_or_accuracy(measurement_case):
    scene, _ = measurement_case
    before = deepcopy(scene.to_dict())
    data = create_annotations(scene, "a" * 64, 0.12)
    report = evaluate_measurements(scene, data, sensitivity_trials=20)
    assert data["view_groups"] == [[0, 1], [0, 2]]
    assert data["reference"]["length_m"] == 0.12
    assert all(p["observations"] == {} for p in data["points"].values())
    assert "missing_observations" in report["blocking_reasons"]
    assert report["fixed_scale_m_per_unit"] is None
    assert not report["metric_measurement_accepted"]
    assert scene.to_dict() == before
