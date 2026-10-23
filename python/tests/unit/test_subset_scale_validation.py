"""Metric trials must preserve visual geometry and expose scale ambiguities."""

import copy
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from numpy.testing import assert_allclose
from scipy.spatial.transform import Rotation

from distance_measuring.camera import CameraExtrinsics, CameraIntrinsics
from distance_measuring.inertial.experimental import orientation_interpolator
from distance_measuring.scene import Scene, SceneCamera


def module(name):
    path = Path(__file__).resolve().parents[3] / f"tools/validation/{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


@pytest.mark.parametrize("count", [2, 3])
def test_inertial_scale_uses_retained_photos_and_correct_reference_frame_without_moving_cameras(
    monkeypatch, count
):
    tool = module("estimate_scene_scale")
    Q = Rotation.from_euler("xyz", [20, -15, 40], degrees=True)
    orientation = orientation_interpolator([0, 6], [Q.as_quat(), Q.as_quat()])
    monkeypatch.setattr(
        tool,
        "prepare_visual_heading",
        lambda *args: (
            None,
            orientation,
            {
                "R_reference_visual": Q.as_matrix().tolist(),
                "continuous_orientation_coverage_s": [0, 6],
            },
        ),
    )
    a, v, scale = np.array([0.6, 0.1, -0.04]), np.array([0.2, -0.1, 0.05]), 0.25
    K = CameraIntrinsics(600, 600, 799.5, 599.5, 1600, 1200)
    photos = [(2, 1), (3, 2), (7, 4)][:count]
    scene = Scene(
        tuple(
            SceneCamera(
                i,
                K,
                CameraExtrinsics(
                    np.eye(3),
                    (v * t + 0.5 * a * t * t) / scale,
                ),
            )
            for i, t in photos
        ),
        (),
        (),
        {"quality_status": "visual_validation_passed"},
    )
    before = scene.to_dict()
    raw = a + Q.inv().apply([0, 0, 9.80665])
    session = SimpleNamespace(
        start_ns=0,
        metadata=SimpleNamespace(session_id="known_motion"),
        images=[
            SimpleNamespace(index=i, timestamp_ns=int(t * 1e9), timestamp_source="camera_frame")
            for i, t in [(0, 100), *photos]
        ],
        sensor_streams={
            "accelerometer": [
                SimpleNamespace(
                    timestamp_ns=k * 10_000_000,
                    x=raw[0],
                    y=raw[1],
                    z=raw[2],
                )
                for k in range(601)
            ]
        },
    )
    report = tool.estimate_scale(session, scene)
    assert report["image_ids"] == [i for i, _ in photos]
    assert scene.to_dict() == before
    assert not report["metric_scale_accepted"] and not report["camera_geometry_changed"]
    if count == 3:
        assert_allclose(report["fit"]["positive_scale_candidate_m_per_unit"], scale, atol=1e-12)
    else:
        assert report["fit"]["rejection_reason"] == "unobservable_scale"


def ruler_fixture(*, last_x=1.0):
    K = CameraIntrinsics(600, 600, 799.5, 599.5, 1600, 1200)
    ids = [2, 3, 7]
    scene = Scene(
        tuple(SceneCamera(i, K, CameraExtrinsics(np.eye(3), [n, 0, 0])) for n, i in enumerate(ids)),
        (),
        (),
        {
            "session_id": "ruler_test",
            "quality_status": "visual_validation_passed",
        },
    )
    world = np.column_stack([[0.4, 0.6, 0.8, last_x], np.zeros(4), np.full(4, 5)])
    annotation = {
        "session_id": "ruler_test",
        "coordinate_frame": "undistorted_working_pixels_ignore_exif",
        "working_image_size": [1600, 1200],
        "tick_cm": [8, 12, 16, 20],
        "reference_interval_indices": [0, 1],
        "held_out_interval_indices": [[1, 2], [2, 3]],
        "working_pixels": {
            str(c.image_id): K.project(world, c.extrinsics).tolist() for c in scene.cameras
        },
    }
    return scene, annotation


def test_ruler_excludes_bad_observations_by_geometry_before_using_lengths():
    scene, annotation = ruler_fixture()
    for xy in annotation["working_pixels"]["7"]:
        xy[1] += 20
    before = copy.deepcopy(annotation)
    tool = module("validate_ruler_reference")
    assert not tool.check_ruler(scene, annotation)["passes_10_percent_and_reprojection"]
    report = tool.check_ruler(scene, annotation, allow_unusable_observations=True)
    assert report["image_ids"] == [2, 3] and report["excluded_observation_image_ids"] == [7]
    assert not report["observation_selection_uses_known_lengths"]
    assert report["passes_10_percent_and_reprojection"]
    assert_allclose(report["diagnostic_reference_scale_m_per_unit"], 0.2, atol=1e-12)
    assert annotation == before


def test_ruler_10_percent_gate_rejects_wrong_spacing_despite_exact_reprojection():
    scene, annotation = ruler_fixture(last_x=0.975)
    report = module("validate_ruler_reference").check_ruler(scene, annotation)
    assert report["all_ticks_accepted"]
    assert_allclose(
        report["diagnostic_held_out_intervals"][1]["deviation_percent"], 12.5, atol=1e-10
    )
    assert not report["passes_10_percent_and_reprojection"]
    assert not report["diagnostic_values_are_accepted_measurements"]
    assert not report["scale_applied_to_scene"]


@pytest.mark.parametrize("problem", ["session_id", "coordinate_frame", "working_image_size"])
def test_ruler_refuses_incompatible_annotations(problem):
    scene, annotation = ruler_fixture()
    annotation[problem] = [1200, 1600] if problem == "working_image_size" else "wrong"
    with pytest.raises(ValueError):
        module("validate_ruler_reference").check_ruler(scene, annotation)
