import copy

import cv2
import numpy as np
import pytest
from numpy.testing import assert_allclose

from distance_measuring.camera import CameraExtrinsics, CameraIntrinsics
from distance_measuring.scene import Observation, Scene, SceneCamera, ScenePoint, SceneTrack
from distance_measuring.vision.densification import (
    densify_scene,
    stereo_candidates,
    triangulate_batch,
)
from distance_measuring.vision.triangulation import triangulate_point


@pytest.fixture
def stereo_plane():
    K = CameraIntrinsics(260, 260, 159.5, 119.5, 320, 240)
    cameras = [
        SceneCamera(i, K, CameraExtrinsics(np.eye(3), [x, 0, 0])) for i, x in [(2, 0), (7, 0.4)]
    ]
    rng = np.random.default_rng(56)
    first = cv2.GaussianBlur(rng.integers(0, 255, (240, 320), np.uint8), (3, 3), 0.6)
    second = cv2.warpAffine(first, np.array([[1, 0, -16], [0, 1, 0]], np.float32), (320, 240))
    xy = np.array([[x, y] for y in [60, 120, 180] for x in [80, 140, 200, 260]], float)
    world = np.column_stack([K.normalize_pixels(xy), np.ones(len(xy))]) * 6.5
    tracks = []
    points = []
    for n, point in enumerate(world):
        track_id, point_id = 10 + n * 3, 500 + n * 5
        obs = tuple(
            Observation(c.image_id, n + 20, tuple(K.project(point, c.extrinsics)), 0)
            for c in cameras
        )
        tracks.append(SceneTrack(track_id, obs, point_id))
        points.append(ScenePoint(point_id, track_id, point, 4))
    scene = Scene(
        tuple(cameras),
        tuple(tracks),
        tuple(points),
        {
            "session_id": "synthetic_plane",
            "quality_status": "visual_validation_passed",
            "scale_status": "arbitrary_visual_scale_unverified",
        },
    )
    return (
        scene,
        {2: first, 7: second},
        {2: np.full_like(first, 255), 7: np.full_like(first, 255)},
        (xy, xy - [16, 0]),
    )


def test_batch_svd_matches_existing_solver_and_rejects_backward_rays(stereo_plane):
    scene, _, _, (a, b) = stereo_plane
    first, second = scene.cameras
    xyz, valid, errors, angles = triangulate_batch(first, second, a, b)
    assert valid.all()
    assert_allclose(xyz, [p.position for p in scene.points], atol=1e-10)
    for n in range(len(a)):
        normal = triangulate_point(
            [a[n], b[n]],
            [first.intrinsics, second.intrinsics],
            [first.extrinsics, second.extrinsics],
        )
        assert_allclose(xyz[n], normal.position, atol=1e-10)
        assert_allclose(angles[n], normal.angle_degrees, atol=1e-10)
    assert errors.max() < 1e-9
    assert not triangulate_batch(first, second, a, a + [16, 0])[1].any()
    assert not triangulate_batch(first, second, a, a - [0.01, 0])[1].any()


def test_real_stereo_recovers_textured_plane_from_images_without_changing_cameras(stereo_plane):
    scene, images, masks, seed = stereo_plane
    first, second = scene.cameras
    result = stereo_candidates(
        first, second, images[2], images[7], masks[2], masks[7], seed_pixels=seed
    )
    assert len(result["positions"]) > 1000
    assert abs(np.median(result["positions"][:, 2]) - 6.5) < 0.1
    assert result["errors"].max() <= 3
    assert result["angles"].min() >= 1


@pytest.mark.parametrize("blank", [False, True])
def test_masked_or_textureless_images_do_not_produce_reconstruction_padding(stereo_plane, blank):
    scene, images, masks, seed = stereo_plane
    if blank:
        images = {i: np.zeros_like(im) for i, im in images.items()}
    else:
        masks = {i: np.zeros_like(m) for i, m in masks.items()}
    first, second = scene.cameras
    result = stereo_candidates(
        first, second, images[2], images[7], masks[2], masks[7], seed_pixels=seed
    )
    assert not len(result["positions"])
    dense, report = densify_scene(scene, images, masks, target_points=100)
    assert dense.points == scene.points and dense.cameras == scene.cameras
    assert not report["target_reached"] and not report["added_point_count"]


def test_append_preserves_original_ids_geometry_and_unique_observations(stereo_plane):
    scene, images, masks, _ = stereo_plane
    before = copy.deepcopy(scene.to_dict())
    dense, report = densify_scene(scene, images, masks, target_points=100)
    assert len(dense.points) == 100 and report["target_reached"]
    assert dense.cameras == scene.cameras
    assert dense.points[: len(scene.points)] == scene.points
    assert dense.tracks[: len(scene.tracks)] == scene.tracks
    assert scene.to_dict() == before
    assert {p.point_id for p in scene.points} < {p.point_id for p in dense.points}
    assert not report["synthetic_or_duplicate_padding_used"]
    assert not report["camera_geometry_changed"] and not report["metric_scale_applied"]
    assert dense.metadata["quality_status"] == "densified_inspection_unverified"
