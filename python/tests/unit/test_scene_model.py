import copy
from dataclasses import replace

import numpy as np
import pytest
from numpy.testing import assert_allclose

from distance_measuring.camera import CameraExtrinsics, CameraIntrinsics
from distance_measuring.scene import (
    Observation,
    Scene,
    SceneCamera,
    ScenePoint,
    SceneTrack,
    load_scene,
    save_scene,
)


def scene_example():
    camera = CameraIntrinsics(
        900,
        870,
        639.5,
        479.5,
        1280,
        960,
        skew=2,
        source="metadata-derived",
        assumptions=("test estimate",),
    )
    cameras = (
        SceneCamera(4, camera, CameraExtrinsics(np.eye(3), np.zeros(3))),
        SceneCamera(7, camera, CameraExtrinsics(np.eye(3), np.array([1, 0, 0]))),
    )
    point = ScenePoint(3, 3, (0.2, 0.1, 6), 9.1)
    track = SceneTrack(
        3,
        (
            Observation(4, 12, (669.5333333333333, 494), 0.125),
            Observation(7, 8, (519.5333333333333, 494), 0.25),
        ),
        3,
    )
    rejected = SceneTrack(
        5,
        (Observation(4, 13, (300, 200)), Observation(7, 9, (100, 50))),
        rejection_reason="behind_camera",
    )
    return Scene(
        cameras,
        (track, rejected),
        (point,),
        {
            "timestamp_ns": 2**60 + 3,
            "scale_status": "arbitrary_global_scale",
            "warnings": ["approximate K"],
            "nested": {"value": 0.12345678901234567},
        },
    )


def test_scene_round_trip_preserves_parameters_observations_and_metadata(tmp_path):
    original = scene_example()
    path = save_scene(tmp_path / "scene.json", original)
    loaded = load_scene(path)
    assert loaded.to_dict() == original.to_dict()
    assert loaded.metadata["timestamp_ns"] == 2**60 + 3
    for before, after in zip(original.cameras, loaded.cameras, strict=True):
        assert_allclose(before.M, after.M, atol=0, rtol=0)
        assert before.intrinsics.assumptions == after.intrinsics.assumptions
    assert loaded.tracks[1].point_id is None
    assert loaded.tracks[1].rejection_reason == "behind_camera"


@pytest.mark.parametrize(
    "change",
    ["version", "frame", "camera", "point", "track", "duplicate_observation", "nan", "intrinsics"],
)
def test_rejects_broken_scene_references_and_nonfinite_data(change):
    data = copy.deepcopy(scene_example().to_dict())
    if change == "version":
        data["schema_version"] = 99
    elif change == "frame":
        data["coordinate_frame"] = "rectified_pixels"
    elif change == "camera":
        data["cameras"] = data["cameras"][:1]
    elif change == "point":
        data["points"][0]["track_id"] = 123
    elif change == "track":
        data["tracks"][0]["point_id"] = 999
    elif change == "duplicate_observation":
        data["tracks"][1]["observations"][0]["feature_id"] = 12
    elif change == "nan":
        data["points"][0]["position"] = [np.nan, 0, 1]
    else:
        data["cameras"][0]["intrinsics"]["approximate"] = False
    with pytest.raises(ValueError):
        Scene.from_dict(data)


def test_tracks_require_distinct_images_and_reciprocal_point_references():
    scene = scene_example()
    with pytest.raises(ValueError, match="distinct images"):
        SceneTrack(1, (Observation(4, 1, (1, 2)), Observation(4, 2, (3, 4))))
    with pytest.raises(ValueError, match="unique"):
        replace(scene, cameras=scene.cameras * 2)
    with pytest.raises(ValueError, match="finite JSON"):
        replace(scene, metadata={"error": float("inf")})
    with pytest.raises(ValueError, match="reprojection errors"):
        replace(
            scene,
            tracks=(
                replace(
                    scene.tracks[0],
                    observations=tuple(
                        replace(obs, reprojection_error_px=None)
                        for obs in scene.tracks[0].observations
                    ),
                ),
            ),
        )


def test_empty_scene_round_trip():
    scene = Scene((), (), (), {"status": "empty"})
    assert Scene.from_dict(scene.to_dict()) == scene
