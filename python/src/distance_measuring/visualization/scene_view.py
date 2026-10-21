"""Viewer geometry: lecture 002 pp. 65–76, 81–83, 133–134 (PPTX slide order).

Frustums invert the existing calibrated camera model; colors, stable-ID selection
and preview layout are application extensions. See docs/architecture/scene_viewer.md.
"""

from dataclasses import dataclass

import numpy as np

from distance_measuring.scene import Scene, SceneCamera, ScenePoint

PALETTE = (
    ("red", (0.90, 0.22, 0.15)),
    ("blue", (0.15, 0.45, 0.90)),
    ("green", (0.12, 0.65, 0.30)),
    ("purple", (0.6, 0.3, 0.8)),
    ("orange", (0.95, 0.5, 0.1)),
    ("cyan", (0.1, 0.7, 0.75)),
)
SELECTION_COLORS = ((1.0, 0.65, 0.0), (0.9, 0.15, 0.8))
SELECTION_NAMES = ("gold", "magenta")


def select_points(scene: Scene, point_ids: tuple[int, ...]) -> tuple[ScenePoint, ...]:
    if len(point_ids) > 2 or len(set(point_ids)) != len(point_ids):
        raise ValueError("Select at most two distinct point IDs")
    if any(type(value) is not int or value < 0 for value in point_ids):
        raise ValueError("Point IDs must be nonnegative integers")
    by_id = {point.point_id: point for point in scene.points}
    missing = [value for value in point_ids if value not in by_id]
    if missing:
        raise ValueError(f"Unknown point IDs {missing}; use --list-points to see accepted IDs")
    return tuple(by_id[value] for value in point_ids)


@dataclass(frozen=True)
class CameraDrawing:
    image_id: int
    color_name: str
    color: tuple[float, float, float]
    vertices: np.ndarray
    lines: np.ndarray


def camera_drawing(camera: SceneCamera, depth: float, palette_index: int) -> CameraDrawing:
    if not np.isfinite(depth) or depth <= 0:
        raise ValueError("Frustum depth must be positive and finite")
    K, pose = camera.intrinsics, camera.extrinsics
    # Lecture 002 pp. 70–76, 133–134: K^-1 m gives an optical ray. Pixel edges
    # surround the stored zero-based pixel centers; no EXIF rotation is applied.
    pixels = np.array(
        [
            [-0.5, -0.5],
            [K.image_width - 0.5, -0.5],
            [K.image_width - 0.5, K.image_height - 0.5],
            [-0.5, K.image_height - 0.5],
        ]
    )
    rays = np.column_stack((K.normalize_pixels(pixels), np.ones(4)))
    # Lecture 002 pp. 81–83: P_c=R(P_w-C), so P_w=R.T P_c+C.
    # Display depth is a chosen drawing length, not a recovered scene distance.
    corners = pose.camera_to_world(depth * rays)
    # pp. 133–134: optical axis is forward camera +Z, expressed in the world.
    forward, right = pose.R_cw[2], pose.R_cw[0]
    tip = pose.C_w + 1.3 * depth * forward
    wings = [tip - 0.18 * depth * forward + sign * 0.07 * depth * right for sign in (-1, 1)]
    vertices = np.vstack((pose.C_w, corners, tip, wings))
    lines = np.array(
        [(0, i) for i in range(1, 5)] + [(1, 2), (2, 3), (3, 4), (4, 1), (0, 5), (5, 6), (5, 7)],
        dtype=np.int32,
    )
    name, color = PALETTE[palette_index % len(PALETTE)]
    return CameraDrawing(camera.image_id, name, color, vertices, lines)


@dataclass(frozen=True)
class SceneView:
    point_ids: tuple[int, ...]
    points: np.ndarray
    point_colors: np.ndarray
    cameras: tuple[CameraDrawing, ...]
    selected: tuple[ScenePoint, ...]
    frustum_depth: float


def build_scene_view(
    scene: Scene, point_ids: tuple[int, ...] = (), *, frustum_depth: float | None = None
) -> SceneView:
    selected = select_points(scene, point_ids)
    points = np.array([point.position for point in scene.points], dtype=float).reshape(-1, 3)
    centers = np.array([camera.extrinsics.C_w for camera in scene.cameras], dtype=float).reshape(
        -1, 3
    )
    geometry = np.vstack((points, centers))
    if not len(geometry):
        raise ValueError("Scene has no points or cameras to display")
    if frustum_depth is None:
        frustum_depth = 0.12 * max(float(np.ptp(geometry, axis=0).max()), 1e-6)
    cameras = tuple(
        camera_drawing(camera, frustum_depth, i)
        for i, camera in enumerate(sorted(scene.cameras, key=lambda camera: camera.image_id))
    )
    ids = tuple(point.point_id for point in scene.points)
    colors = np.tile([0.45, 0.53, 0.62], (len(points), 1))
    rows = {point_id: row for row, point_id in enumerate(ids)}
    for i, point in enumerate(selected):
        colors[rows[point.point_id]] = SELECTION_COLORS[i]
    return SceneView(ids, points, colors, cameras, selected, frustum_depth)


def selection_report(scene: Scene, point_ids: tuple[int, ...], source_sha256: str) -> dict:
    selected = select_points(scene, point_ids)
    if not selected:
        raise ValueError("A selection report requires one or two point IDs")
    tracks = {track.track_id: track for track in scene.tracks}
    return {
        "schema_version": 1,
        "source_scene_sha256": source_sha256,
        "coordinate_frame": "world",
        "units": "reconstruction_units",
        "scale_status": scene.metadata.get("scale_status", "unspecified"),
        "quality_status": scene.metadata.get("quality_status", "unspecified"),
        "warnings": scene.metadata.get("warnings", []),
        "points": [
            {
                "label": "AB"[i],
                "highlight_color": SELECTION_NAMES[i],
                "point_id": point.point_id,
                "track_id": point.track_id,
                "position_world": list(point.position),
                "observations": [
                    {
                        "image_id": observation.image_id,
                        "feature_id": observation.feature_id,
                        "pixel": list(observation.pixel),
                    }
                    for observation in tracks[point.track_id].observations
                ],
            }
            for i, point in enumerate(selected)
        ],
    }
