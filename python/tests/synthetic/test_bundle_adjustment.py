from dataclasses import replace

import numpy as np
import pytest
from numpy.testing import assert_allclose
from scipy.spatial.transform import Rotation

from distance_measuring.camera import CameraExtrinsics
from distance_measuring.optimization import bundle_adjust, reprojection_residuals
from distance_measuring.optimization.bundle import _Problem, project_observations
from distance_measuring.scene import SceneCamera


def test_perfect_reprojection_residuals_are_zero_in_observation_order(bundle_scene_factory):
    truth, _, _ = bundle_scene_factory(count=12, noise_px=0, perturb=False)
    residuals = reprojection_residuals(truth)
    assert residuals.shape == (12 * 3 * 2,)
    assert_allclose(residuals, 0, atol=1e-12)
    tracks = list(truth.tracks)
    observations = list(tracks[0].observations)
    observations[0] = replace(observations[0], pixel=np.array(observations[0].pixel) + [2, -3])
    tracks[0] = replace(tracks[0], observations=tuple(observations))
    changed = reprojection_residuals(replace(truth, tracks=tuple(tracks)))
    assert_allclose(changed[:2], [-2, 3], atol=1e-12)
    assert_allclose(changed[2:], 0, atol=1e-12)


def test_batched_observation_geometry_preserves_order_and_exposes_invalid_depth(
    bundle_scene_factory,
):
    truth, _, _ = bundle_scene_factory(count=12, noise_px=0, perturb=False)
    points = list(truth.points)
    points[0] = replace(points[0], position=(0, 0, -100))
    scene = replace(truth, points=tuple(points))
    rows, depth, errors = project_observations(scene)
    assert [(t, o.image_id) for t, o in rows] == [
        (t.track_id, o.image_id)
        for t in scene.tracks
        if t.point_id is not None
        for o in t.observations
    ]
    assert (depth[:3] < 0).all() and np.isnan(errors[:3]).all()
    assert (depth[3:] > 0).all()
    assert_allclose(errors[3:], 0, atol=1e-12)
    with pytest.raises(ValueError, match="front"):
        reprojection_residuals(scene)


def test_sparse_point_selection_decodes_coordinates_in_the_original_world_gauge(
    bundle_scene_factory,
):
    scene, _, _ = bundle_scene_factory(count=12, noise_px=0, perturb=False)
    selected = [scene.points[i].point_id for i in [1, 5, 10]]
    problem = _Problem(scene, variable_camera_ids=[], variable_point_ids=selected)
    parameters = problem.initial.copy()
    shift = np.array([0.03, -0.02, 0.01])
    parameters[problem.point_start :] += np.tile(shift, len(selected))
    _, _, points = problem.decode(parameters)
    for i, point in enumerate(scene.points):
        expected = problem.positions[i] + (shift if point.point_id in selected else 0)
        assert_allclose(points[i], expected, atol=1e-15)


def test_noisy_refinement_lowers_RMSE_and_preserves_exact_gauge_and_ids(bundle_scene_factory):
    truth, initial, _ = bundle_scene_factory()
    before = initial.to_dict()
    result = bundle_adjust(initial)
    assert result.report["accepted"] and result.report["converged"]
    assert result.report["after"]["reprojection_rmse_px"] < 0.4
    assert (
        result.report["after"]["reprojection_rmse_px"]
        < result.report["before"]["reprojection_rmse_px"] / 10
    )
    assert initial.to_dict() == before
    scene = result.scene
    assert scene.cameras[0] is initial.cameras[0]

    def length(model):
        return np.linalg.norm(model.cameras[1].extrinsics.C_w - model.cameras[0].extrinsics.C_w)

    assert length(scene) == pytest.approx(length(initial), abs=1e-12)
    assert [point.point_id for point in scene.points] == [
        point.point_id for point in initial.points
    ]
    assert [track.track_id for track in scene.tracks] == [
        track.track_id for track in initial.tracks
    ]
    assert scene.tracks[-1] == initial.tracks[-1]
    for actual, expected in zip(scene.cameras, truth.cameras, strict=True):
        assert (
            Rotation.from_matrix(actual.extrinsics.R_cw @ expected.extrinsics.R_cw.T).magnitude()
            < 0.006
        )
        assert np.linalg.norm(actual.extrinsics.C_w - expected.extrinsics.C_w) < 0.04
        assert_allclose(actual.intrinsics.K, expected.intrinsics.K, atol=0)
    public = reprojection_residuals(scene).reshape(-1, 2)
    stored = [
        observation.reprojection_error_px
        for track in scene.tracks
        if track.point_id is not None
        for observation in track.observations
    ]
    assert_allclose(stored, np.linalg.norm(public, axis=1), atol=1e-9)


def test_sparse_dependencies_and_gauge_reduction_have_full_rank_on_clean_geometry(
    bundle_scene_factory,
):
    _, scene, _ = bundle_scene_factory(count=12)
    problem = _Problem(scene)
    base = problem.residuals(problem.initial)
    columns = []
    for j in range(len(problem.initial)):
        step = problem.initial.copy()
        step[j] += 1e-7
        columns.append((problem.residuals(step) - base) / 1e-7)
    jacobian = np.column_stack(columns)
    assert not np.any((np.abs(jacobian) > 1e-5) & (problem.sparsity.toarray() == 0))
    assert np.linalg.matrix_rank(jacobian, tol=1e-5) == len(problem.initial)
    assert len(problem.initial) == 11 + 3 * 12


def test_robust_loss_limits_outlier_effect_on_cameras_and_clean_points(bundle_scene_factory):
    truth, initial, bad = bundle_scene_factory(count=70, outliers=6, seed=15)
    robust = bundle_adjust(initial, loss="soft_l1", f_scale=0.5)
    linear = bundle_adjust(initial, loss="linear")
    assert robust.report["accepted"]
    assert robust.report["after"]["robust_cost"] < robust.report["before"]["robust_cost"]

    def camera_error(scene):
        return sum(
            np.linalg.norm(actual.extrinsics.C_w - expected.extrinsics.C_w)
            for actual, expected in zip(scene.cameras, truth.cameras, strict=True)
        )

    assert camera_error(robust.scene) < 0.12
    assert camera_error(robust.scene) < camera_error(linear.scene)
    good_ids = {track.track_id for track in truth.tracks if track.point_id is not None} - {
        track for track, _ in bad
    }
    actual = {point.track_id: np.array(point.position) for point in robust.scene.points}
    errors = [
        np.linalg.norm(actual[point.track_id] - point.position)
        for point in truth.points
        if point.track_id in good_ids
    ]
    # Absolute bound is under 2.5% of this fixture's nearest depth; the relative
    # check ensures robustness improves structure, not just the chosen objective.
    assert np.median(errors) < 0.1
    linear_points = {point.track_id: np.array(point.position) for point in linear.scene.points}
    linear_errors = [
        np.linalg.norm(linear_points[point.track_id] - point.position)
        for point in truth.points
        if point.track_id in good_ids
    ]
    assert np.median(errors) < 0.2 * np.median(linear_errors)


@pytest.mark.parametrize("loss", ["linear", "huber", "cauchy"])
def test_supported_losses_preserve_perfect_geometry(bundle_scene_factory, loss):
    scene, _, _ = bundle_scene_factory(count=12, noise_px=0, perturb=False)
    result = bundle_adjust(scene, loss=loss)
    assert result.report["accepted"] and result.report["converged"]
    assert result.report["after"]["reprojection_rmse_px"] < 1e-9


def test_iteration_limit_is_reported_without_claiming_convergence(bundle_scene_factory):
    _, scene, _ = bundle_scene_factory(count=12)
    result = bundle_adjust(scene, max_nfev=1)
    assert result.report["accepted"]
    assert not result.report["converged"] and result.report["solver_status"] == 0
    assert any(
        "without solver convergence" in warning for warning in result.scene.metadata["warnings"]
    )


@pytest.mark.parametrize(
    "options",
    [
        {"loss": "unknown"},
        {"f_scale": 0},
        {"f_scale": float("nan")},
        {"max_nfev": 0},
        {"max_nfev": True},
    ],
)
def test_invalid_configuration(bundle_scene_factory, options):
    _, scene, _ = bundle_scene_factory(count=12)
    with pytest.raises(ValueError):
        bundle_adjust(scene, **options)


def test_empty_disconnected_or_behind_camera_scenes_are_rejected(bundle_scene_factory):
    truth, initial, _ = bundle_scene_factory(count=12)
    with pytest.raises(ValueError, match="accepted 3D points"):
        bundle_adjust(replace(truth, tracks=(), points=()))
    extra = SceneCamera(
        20, truth.cameras[0].intrinsics, CameraExtrinsics(np.eye(3), np.array([5, 0, 0]))
    )
    with pytest.raises(ValueError, match="connect every camera"):
        bundle_adjust(replace(truth, cameras=truth.cameras + (extra,)))
    bad_points = tuple(
        replace(point, position=(point.position[0], point.position[1], -5))
        for point in initial.points
    )
    with pytest.raises(ValueError, match="in front"):
        bundle_adjust(replace(initial, points=bad_points))


def test_input_geometry_diagnostics_are_retained_as_provenance(bundle_scene_factory):
    _, scene, _ = bundle_scene_factory(count=12)
    metadata = {
        **scene.metadata,
        "quality_status": "rotation_loop_inconsistent",
        "scale_status": "independent_unit_baselines_relative_scales_unresolved",
        "pose_closure": {"rotation_error_degrees": 17},
    }
    result = bundle_adjust(replace(scene, metadata=metadata))
    assert result.scene.metadata["quality_status"] == "bundle_adjusted_unverified"
    assert result.scene.metadata["scale_status"] == "arbitrary_visual_scale_unverified"
    assert result.report["input_quality_status"] == "rotation_loop_inconsistent"
    assert result.report["input_pose_closure"]["rotation_error_degrees"] == 17
    assert result.scene.metadata["input_pose_closure"]["rotation_error_degrees"] == 17
    assert result.scene.metadata["timestamp_ns"] == 2**60 + 3
