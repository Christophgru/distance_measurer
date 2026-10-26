from dataclasses import replace

import numpy as np
import pytest
from numpy.testing import assert_allclose

from distance_measuring.camera import CameraExtrinsics
from distance_measuring.optimization.bundle import BundleResult, _Problem, bundle_adjust
from distance_measuring.optimization.local_bundle import LocalBundleConfig, local_bundle_adjust
from distance_measuring.scene import Observation, Scene, SceneCamera, SceneTrack


def five_view_scene(bundle_scene_factory):
    truth, _, _ = bundle_scene_factory(count=24, noise_px=0, perturb=False)
    rng = np.random.default_rng(8)
    cameras = list(truth.cameras)
    for k in range(2):
        cameras.append(
            SceneCamera(
                20 + k,
                cameras[0].intrinsics,
                CameraExtrinsics(np.eye(3), cameras[0].extrinsics.C_w + [3 + k, 0.1, 0]),
            )
        )
    tracks = []
    points = []
    for k, p in enumerate(truth.points):
        observations = tuple(
            Observation(c.image_id, k, tuple(c.intrinsics.project(p.position, c.extrinsics)), 0)
            for c in cameras
        )
        tracks.append(SceneTrack(p.track_id, observations, p.point_id))
        points.append(replace(p, position=np.asarray(p.position) + rng.normal(0, 0.015, 3)))
    cameras[-1] = replace(
        cameras[-1],
        extrinsics=CameraExtrinsics(
            cameras[-1].extrinsics.R_cw, cameras[-1].extrinsics.C_w + [0.03, -0.015, 0.01]
        ),
    )
    return Scene(tuple(cameras), tuple(tracks), tuple(points), truth.metadata)


def test_local_window_lowers_rmse_and_keeps_original_gauge_and_outside_cameras(
    bundle_scene_factory,
):
    scene = five_view_scene(bundle_scene_factory)
    before = scene.to_dict()
    result = local_bundle_adjust(scene, 21, config=LocalBundleConfig(window_size=2))
    assert result.report["accepted"], result.report
    assert result.report["after_rmse_px"] < result.report["before_rmse_px"] / 10
    assert result.report["variable_camera_ids"] == [20, 21]
    assert all(a is b for a, b in zip(result.scene.cameras[:3], scene.cameras[:3], strict=True))
    assert scene.to_dict() == before
    assert [p.point_id for p in result.scene.points] == [p.point_id for p in scene.points]
    assert_allclose(result.scene.cameras[1].extrinsics.C_w, scene.cameras[1].extrinsics.C_w, atol=0)


def test_selective_parameter_sparsity_and_fixed_landmark_coordinates(bundle_scene_factory):
    scene = five_view_scene(bundle_scene_factory)
    points = [p.point_id for p in scene.points[:12]]
    problem = _Problem(
        scene, variable_camera_ids=[21], variable_point_ids=points, observation_point_ids=points
    )
    assert len(problem.initial) == 6 + 3 * 12
    base = problem.residuals(problem.initial)
    columns = []
    for i in range(len(problem.initial)):
        values = problem.initial.copy()
        values[i] += 1e-7
        columns.append((problem.residuals(values) - base) / 1e-7)
    jacobian = np.column_stack(columns)
    assert not np.any((np.abs(jacobian) > 1e-5) & (problem.sparsity.toarray() == 0))
    assert np.linalg.matrix_rank(jacobian, tol=1e-5) == len(problem.initial)
    result = bundle_adjust(
        scene, variable_camera_ids=[21], variable_point_ids=points, observation_point_ids=points
    )
    assert result.report["accepted"]
    assert all(a is b for a, b in zip(result.scene.points[12:], scene.points[12:], strict=True))


def test_original_first_baseline_is_fixed_when_second_camera_is_local(bundle_scene_factory):
    _, scene, _ = bundle_scene_factory(count=24)
    result = local_bundle_adjust(scene, 9)
    assert result.report["accepted"]
    assert result.scene.cameras[0] is scene.cameras[0]
    before = np.linalg.norm(scene.cameras[1].extrinsics.C_w - scene.cameras[0].extrinsics.C_w)
    after = np.linalg.norm(
        result.scene.cameras[1].extrinsics.C_w - result.scene.cameras[0].extrinsics.C_w
    )
    assert after == pytest.approx(before, abs=1e-12)


def test_unsupported_window_and_invalid_selection_preserve_geometry(bundle_scene_factory):
    scene = five_view_scene(bundle_scene_factory)
    result = local_bundle_adjust(scene, 21, trusted_point_ids=[])
    assert result.scene is scene and not result.report["accepted"]
    with pytest.raises(ValueError, match="remain fixed"):
        bundle_adjust(scene, variable_camera_ids=[scene.cameras[0].image_id])
    with pytest.raises(ValueError, match="unique known"):
        bundle_adjust(scene, variable_point_ids=[999999])
    with pytest.raises(ValueError):
        LocalBundleConfig(window_size=0)


def test_single_fixed_boundary_camera_does_not_introduce_a_free_local_scale(bundle_scene_factory):
    scene = five_view_scene(bundle_scene_factory)
    # The local component sees camera 9 (fixed) and 20/21 (variable), but neither
    # original gauge camera. One fixed center cannot anchor its structure scale.
    tracks = tuple(
        replace(t, observations=tuple(o for o in t.observations if o.image_id in [9, 20, 21]))
        for t in scene.tracks
    )
    scene = replace(scene, tracks=tracks)
    result = local_bundle_adjust(scene, 21, config=LocalBundleConfig(window_size=2))
    assert result.scene is scene and not result.report["accepted"]
    assert "scale/pose anchors" in result.report["reason"]


def test_selected_robust_cost_improvement_cannot_hide_full_scene_rmse_increase(
    bundle_scene_factory,
    monkeypatch,
):
    from distance_measuring.optimization import local_bundle

    scene = five_view_scene(bundle_scene_factory)
    cameras = list(scene.cameras)
    cameras[-1] = replace(
        cameras[-1],
        extrinsics=CameraExtrinsics(
            cameras[-1].extrinsics.R_cw, cameras[-1].extrinsics.C_w + [1, 0, 0]
        ),
    )
    candidate = replace(scene, cameras=tuple(cameras))
    monkeypatch.setattr(
        local_bundle,
        "bundle_adjust",
        lambda *args, **kwargs: BundleResult(
            candidate,
            {
                "accepted": True,
                "before": {"reprojection_rmse_px": 10},
                "after": {"reprojection_rmse_px": 1},
            },
        ),
    )
    result = local_bundle_adjust(scene, 21)
    assert result.scene is scene and not result.report["accepted"]
    assert result.report["reason"] == "full_scene_rmse_increased"


def test_weak_newest_camera_cannot_claim_refinement_using_only_its_neighbors(bundle_scene_factory):
    truth, _, _ = bundle_scene_factory(count=12, noise_px=0, perturb=False)
    tracks = tuple(
        replace(t, observations=tuple(o for o in t.observations if o.image_id != 9 or index < 5))
        if t.point_id is not None
        else t
        for index, t in enumerate(truth.tracks)
    )
    scene = replace(truth, tracks=tracks)
    result = local_bundle_adjust(scene, 9)
    assert result.scene is scene and not result.report["accepted"]
    assert result.report["trusted_observation_count_by_camera"]["9"] == 5
    assert 9 not in result.report["variable_camera_ids"]
