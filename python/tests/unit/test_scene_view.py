from dataclasses import replace

import numpy as np
import pytest
from numpy.testing import assert_allclose, assert_array_equal

from distance_measuring.scene import Scene
from distance_measuring.visualization.scene_view import (
    SELECTION_COLORS,
    build_scene_view,
    camera_drawing,
    select_points,
    selection_report,
)
from distance_measuring.visualization.viewer import open3d_geometries, show_open3d


def test_frustum_uses_K_and_camera_to_world_with_correct_forward_axis(bundle_scene_factory):
    scene, _, _ = bundle_scene_factory(count=12, perturb=False)
    for i, camera in enumerate(scene.cameras):
        depth = 0.4
        drawing = camera_drawing(camera, depth, i)
        assert drawing.vertices.shape == (8, 3)
        assert drawing.lines.shape == (11, 2)
        assert_array_equal(drawing.vertices[0], camera.extrinsics.C_w)
        projected = camera.intrinsics.project(
            drawing.vertices[1:5], camera.extrinsics, distort=False
        )
        width, height = camera.intrinsics.image_size
        assert_allclose(
            projected,
            [[-0.5, -0.5], [width - 0.5, -0.5], [width - 0.5, height - 0.5], [-0.5, height - 0.5]],
            atol=1e-10,
        )
        assert_allclose(
            camera.extrinsics.world_to_camera(drawing.vertices[5]), [0, 0, 1.3 * depth], atol=1e-12
        )
        assert_allclose(
            camera.extrinsics.world_to_camera(drawing.vertices[1:5])[:, 2], depth, atol=1e-12
        )


def test_ID_selection_survives_point_reordering_and_uses_IDs_not_rows(bundle_scene_factory):
    scene, _, _ = bundle_scene_factory(count=12)
    original = scene.to_dict()
    view = build_scene_view(scene, (5, 3))
    assert view.point_ids == tuple(point.point_id for point in scene.points)
    assert [point.point_id for point in view.selected] == [5, 3]
    assert_array_equal(view.point_colors[1], SELECTION_COLORS[0])
    assert_array_equal(view.point_colors[0], SELECTION_COLORS[1])
    reordered = replace(
        scene, points=tuple(reversed(scene.points)), cameras=tuple(reversed(scene.cameras))
    )
    changed = build_scene_view(reordered, (5, 3))
    assert changed.selected == view.selected
    assert [(camera.image_id, camera.color) for camera in changed.cameras] == [
        (camera.image_id, camera.color) for camera in view.cameras
    ]
    assert scene.to_dict() == original
    report = selection_report(scene, (5, 3), "a" * 64)
    assert [point["label"] for point in report["points"]] == ["A", "B"]
    assert [point["point_id"] for point in report["points"]] == [5, 3]
    assert report["points"][0]["track_id"] == scene.points[1].track_id
    assert report["warnings"] == scene.metadata["warnings"]
    assert report["units"] == "reconstruction_units"


@pytest.mark.parametrize("ids", [(0,), (3, 3), (3, 5, 7), (-1,), (True,)])
def test_invalid_selections_are_actionable(bundle_scene_factory, ids):
    scene, _, _ = bundle_scene_factory(count=12)
    with pytest.raises(ValueError):
        select_points(scene, ids)


@pytest.mark.parametrize("depth", [0, -1, float("nan"), float("inf")])
def test_invalid_frustum_depth(bundle_scene_factory, depth):
    scene, _, _ = bundle_scene_factory(count=12)
    with pytest.raises(ValueError, match="depth"):
        build_scene_view(scene, frustum_depth=depth)


def test_empty_point_cloud_can_show_camera_frames(bundle_scene_factory):
    scene, _, _ = bundle_scene_factory(count=12)
    cameras_only = replace(scene, tracks=(), points=())
    view = build_scene_view(cameras_only)
    assert view.points.shape == (0, 3)
    assert len(view.cameras) == 3
    with pytest.raises(ValueError, match="no points or cameras"):
        build_scene_view(Scene((), (), ()))
    with pytest.raises(ValueError, match="one or two"):
        selection_report(scene, (), "a" * 64)


def test_real_open3d_geometry_keeps_cloud_mapping_and_distinct_cameras(bundle_scene_factory):
    scene, _, _ = bundle_scene_factory(count=12)
    view = build_scene_view(scene, (5, 3))
    geometries, point_ids = open3d_geometries(view)
    assert len(geometries) == 1 + 2 * 3 + 2
    assert point_ids == view.point_ids
    assert_array_equal(np.asarray(geometries[0].points), view.points)
    assert_array_equal(np.asarray(geometries[0].colors), view.point_colors)
    for i, camera in enumerate(view.cameras):
        lines = geometries[1 + 2 * i]
        assert_allclose(np.asarray(lines.points), camera.vertices)
        assert_allclose(np.asarray(lines.colors), np.tile(camera.color, (len(camera.lines), 1)))
    assert len({camera.color for camera in view.cameras}) == 3


def test_missing_linux_display_has_headless_hint(bundle_scene_factory, monkeypatch):
    scene, _, _ = bundle_scene_factory(count=12)
    monkeypatch.setattr("distance_measuring.visualization.viewer.sys.platform", "linux")
    monkeypatch.delenv("DISPLAY", raising=False)
    with pytest.raises(RuntimeError, match="--no-window --preview"):
        show_open3d(build_scene_view(scene))
