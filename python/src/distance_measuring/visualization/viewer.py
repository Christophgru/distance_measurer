"""Open3D desktop display and a headless preview of lecture-derived frustums."""

import os
import sys
from pathlib import Path

import numpy as np
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

from distance_measuring.scene import Scene

from .scene_view import SELECTION_COLORS, SELECTION_NAMES, SceneView


def open3d_geometries(view: SceneView):
    """Geometry order never changes point IDs; return the explicit row-to-ID mapping."""
    import open3d as o3d

    cloud = o3d.geometry.PointCloud()
    cloud.points = o3d.utility.Vector3dVector(view.points)
    cloud.colors = o3d.utility.Vector3dVector(view.point_colors)
    geometries = [cloud] if len(view.points) else []
    for camera in view.cameras:
        lines = o3d.geometry.LineSet()
        lines.points = o3d.utility.Vector3dVector(camera.vertices)
        lines.lines = o3d.utility.Vector2iVector(camera.lines)
        lines.paint_uniform_color(camera.color)
        center = o3d.geometry.TriangleMesh.create_sphere(radius=0.045 * view.frustum_depth)
        center.translate(camera.vertices[0])
        center.paint_uniform_color(camera.color)
        center.compute_vertex_normals()
        geometries.extend((lines, center))
    for point, color in zip(view.selected, SELECTION_COLORS, strict=False):
        marker = o3d.geometry.TriangleMesh.create_sphere(radius=0.06 * view.frustum_depth)
        marker.translate(point.position)
        marker.paint_uniform_color(color)
        marker.compute_vertex_normals()
        geometries.append(marker)
    return geometries, view.point_ids


def show_open3d(
    view: SceneView, *, max_frames: int | None = None, screenshot: Path | None = None
) -> None:
    """Interactive by default; a bounded frame loop supports actual renderer smoke tests."""
    if sys.platform.startswith("linux") and not os.environ.get("DISPLAY"):
        raise RuntimeError(
            "No X display detected; run on a graphical desktop or use --no-window --preview PATH"
        )
    import open3d as o3d

    geometries, _ = open3d_geometries(view)
    legend = ", ".join(f"{camera.image_id}={camera.color_name}" for camera in view.cameras)
    title = f"Distance Measuring | cameras {legend} | reconstruction units, geometry unverified"
    if view.selected:
        title += " | " + ", ".join(
            f"{'AB'[i]}={point.point_id}({SELECTION_NAMES[i]})"
            for i, point in enumerate(view.selected)
        )
    visualizer = o3d.visualization.Visualizer()
    try:
        if not visualizer.create_window(window_name=title, width=1280, height=900):
            raise RuntimeError(
                "Open3D could not create a graphics context; use --no-window --preview PATH"
            )
        for geometry in geometries:
            if not visualizer.add_geometry(geometry):
                raise RuntimeError("Open3D could not add scene geometry")
        options = visualizer.get_render_option()
        options.background_color = np.array([0.96, 0.97, 0.98])
        options.point_size = 5
        options.line_width = 2
        visualizer.reset_view_point(True)
        if max_frames is None:
            visualizer.run()
        else:
            if type(max_frames) is not int or max_frames < 1:
                raise ValueError("max_frames must be a positive integer")
            for _ in range(max_frames):
                if not visualizer.poll_events():
                    break
                visualizer.update_renderer()
        if screenshot is not None:
            screenshot.parent.mkdir(parents=True, exist_ok=True)
            visualizer.capture_screen_image(str(screenshot), do_render=True)
            if not screenshot.is_file():
                raise RuntimeError("Open3D did not save the requested screenshot")
    finally:
        visualizer.destroy_window()


def _equal_bounds(axes, vertices):
    low, high = vertices.min(axis=0), vertices.max(axis=0)
    middle = (low + high) / 2
    radius = max(float((high - low).max()) * 0.57, 1e-6)
    axes.set(
        xlim=(middle[0] - radius, middle[0] + radius),
        ylim=(middle[1] - radius, middle[1] + radius),
        zlim=(middle[2] - radius, middle[2] + radius),
        xlabel="world X",
        ylabel="world Y",
        zlabel="world Z",
    )
    axes.set_box_aspect((1, 1, 1))


def save_preview(scene: Scene, view: SceneView, path: Path) -> Path:
    figure = Figure(figsize=(15, 8))
    FigureCanvasAgg(figure)
    for panel in (1, 2):
        axes = figure.add_subplot(1, 2, panel, projection="3d")
        if panel == 1 and len(view.points):
            axes.scatter(*view.points.T, c=view.point_colors, s=9, alpha=0.75)
        for camera in view.cameras:
            for a, b in camera.lines:
                segment = camera.vertices[[a, b]]
                axes.plot(*segment.T, color=camera.color, linewidth=1.2)
            C = camera.vertices[0]
            axes.scatter(
                *C,
                color=camera.color,
                s=35,
                label=f"camera {camera.image_id} ({camera.color_name})",
            )
            axes.text(*C, f"  camera {camera.image_id}", color=camera.color, fontsize=9)
        if panel == 1:
            for i, point in enumerate(view.selected):
                axes.scatter(*point.position, color=SELECTION_COLORS[i], marker="*", s=130)
                label = np.array(point.position) + [
                    0,
                    0,
                    (0.3 if i == 0 else -0.3) * view.frustum_depth,
                ]
                axes.plot(*np.array([point.position, label]).T, color=SELECTION_COLORS[i])
                axes.text(*label, f"  {'AB'[i]}: point {point.point_id}", fontsize=9)
        pieces = [camera.vertices for camera in view.cameras]
        if panel == 1 and len(view.points):
            pieces.append(view.points)
        if pieces:
            _equal_bounds(axes, np.vstack(pieces))
        axes.set_title(
            "Sparse points and camera frustums"
            if panel == 1
            else "Camera detail: centers and forward axes"
        )
        if view.cameras:
            axes.legend(loc="upper left", fontsize=8)
    figure.suptitle(
        "Scene inspection — reconstruction units; geometry and scale unverified", fontsize=13
    )
    selected_ids = ", ".join(str(point.point_id) for point in view.selected) or "none selected"
    figure.text(
        0.5,
        0.06,
        f"Scale: {scene.metadata.get('scale_status', 'unspecified')} | "
        f"Quality: {scene.metadata.get('quality_status', 'unspecified')}",
        ha="center",
        fontsize=9,
    )
    figure.text(
        0.5,
        0.03,
        f"Point IDs: {selected_ids} | Frustum depth {view.frustum_depth:.4g} is a drawing length",
        ha="center",
        fontsize=9,
    )
    figure.tight_layout(rect=(0.03, 0.1, 0.97, 0.93))
    path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(path, dpi=130)
    return path
