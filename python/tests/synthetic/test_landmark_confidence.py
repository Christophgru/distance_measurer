from dataclasses import replace

import numpy as np
import pytest

from distance_measuring.camera import CameraExtrinsics
from distance_measuring.vision.guided import GuidedConfig
from distance_measuring.vision.incremental import register_camera
from distance_measuring.vision.landmark_confidence import ConfidenceConfig, score_landmarks


def test_confidence_uses_geometry_and_excludes_bad_points_despite_cached_zero_error(
    bundle_scene_factory,
):
    truth, _, _ = bundle_scene_factory(count=12, noise_px=0, perturb=False)
    original = score_landmarks(truth)
    points = list(truth.points)
    index = next(i for i, p in enumerate(points) if p.point_id in original.trusted_point_ids)
    points[index] = replace(points[index], position=np.asarray(points[index].position) + [1, 0, 0])
    scene = replace(truth, points=tuple(points))
    before = scene.to_dict()
    scored = score_landmarks(scene)
    assert scene.to_dict() == before
    assert scored.report["excluded_point_count"] == original.report["excluded_point_count"] + 1
    assert points[index].point_id not in scored.trusted_point_ids
    assert original.trusted_point_ids - {points[index].point_id} <= scored.trusted_point_ids
    first = scored.report["landmarks"][index]
    assert first["score"] < 0.2 and first["diagnostics"]["reprojection_rmse_px"] > 20


def test_low_parallax_and_inconsistent_views_lower_confidence(bundle_scene_factory):
    scene, _, _ = bundle_scene_factory(count=12, noise_px=0, perturb=False)
    good = score_landmarks(scene)
    index = next(i for i, row in enumerate(good.report["landmarks"]) if row["trusted"])
    tracks = list(scene.tracks)
    track_index = next(
        i for i, t in enumerate(tracks) if t.point_id == scene.points[index].point_id
    )
    obs = list(tracks[track_index].observations)
    obs[-1] = replace(obs[-1], pixel=np.asarray(obs[-1].pixel) + [3, 0])
    tracks[track_index] = replace(tracks[track_index], observations=tuple(obs))
    changed = score_landmarks(replace(scene, tracks=tuple(tracks)))
    assert (
        changed.report["landmarks"][index]["score"] < good.report["landmarks"][index]["score"] / 4
    )
    assert changed.report["landmarks"][index]["factors"]["view_consistency"] < 0.3
    origin = scene.cameras[0].extrinsics.C_w
    cameras = tuple(
        replace(
            c,
            extrinsics=CameraExtrinsics(
                c.extrinsics.R_cw, origin + (c.extrinsics.C_w - origin) * 0.01
            ),
        )
        for c in scene.cameras
    )
    # Reproject so residuals remain perfect, isolating triangulation conditioning.
    by_id = {c.image_id: c for c in cameras}
    points = {p.point_id: p for p in scene.points}
    tracks = tuple(
        replace(
            t,
            observations=tuple(
                replace(
                    o,
                    pixel=by_id[o.image_id].intrinsics.project(
                        points[t.point_id].position, by_id[o.image_id].extrinsics
                    ),
                )
                for o in t.observations
            ),
        )
        if t.point_id is not None
        else t
        for t in scene.tracks
    )
    low = score_landmarks(replace(scene, cameras=cameras, tracks=tracks))
    assert low.report["trusted_point_count"] == 0


def test_nonphysical_points_are_never_trusted_even_with_zero_threshold(bundle_scene_factory):
    scene, _, _ = bundle_scene_factory(count=12, noise_px=0, perturb=False)
    points = list(scene.points)
    points[0] = replace(points[0], position=[0, 0, -10])
    score = score_landmarks(
        replace(scene, points=tuple(points)), config=ConfidenceConfig(min_score=0)
    )
    assert points[0].point_id not in score.trusted_point_ids
    assert score.report["landmarks"][0]["reason"] == "nonpositive_depth"


def test_pnp_can_exclude_every_low_confidence_landmark_without_falling_back(bundle_scene_factory):
    scene, _, _ = bundle_scene_factory(count=12, noise_px=0.25, perturb=False)
    target = scene.cameras[-1]
    temporal = {
        t.track_id: t.observations[-1].pixel for t in scene.tracks if t.point_id is not None
    }
    base = replace(
        scene,
        cameras=scene.cameras[:2],
        tracks=tuple(replace(t, observations=t.observations[:2]) for t in scene.tracks),
    )
    image = np.zeros((960, 1280), np.uint8)
    registered, report = register_camera(
        base,
        target.image_id,
        target.intrinsics,
        image,
        {c.image_id: image for c in base.cameras},
        temporal,
        target.extrinsics,
        config=GuidedConfig(),
        confidence_config=ConfidenceConfig(min_score=1),
    )
    assert registered is base and not report["success"]
    assert report["confidence"]["excluded_point_count"] == 12
    assert report["pnp"]["input_count"] == 0


@pytest.mark.parametrize(
    "options", [{"min_score": -1}, {"error_scale_px": 0}, {"target_observation_count": 1}]
)
def test_invalid_confidence_configuration_fails(options):
    with pytest.raises(ValueError):
        ConfidenceConfig(**options)
