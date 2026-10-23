"""Persistent sparse scene in lecture 002's K/R/C conventions (pp. 70–83, 136).

The JSON schema and track identities are project-specific extensions. Geometric
equations and page references remain in docs/math/sparse_reconstruction.md.
"""

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from distance_measuring.camera import CameraExtrinsics, CameraIntrinsics

COORDINATE_FRAME = "undistorted_working_pixels_ignore_exif"


def _identifier(value, name):
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a nonnegative integer")


def _coordinates(value, dimension):
    array = np.asarray(value, dtype=float)
    if array.shape != (dimension,) or not np.isfinite(array).all():
        raise ValueError(f"Expected {dimension} finite coordinates")
    return tuple(float(v) for v in array)


def intrinsics_from_dict(data: dict) -> CameraIntrinsics:
    camera = CameraIntrinsics.from_K(
        data["camera_matrix"],
        (data["image_width"], data["image_height"]),
        distortion=data["distortion"],
        source=data["source"],
        camera_id=data.get("camera_id"),
        assumptions=data.get("assumptions", ()),
    )
    if data.get("approximate", camera.approximate) is not camera.approximate:
        raise ValueError("Intrinsic approximate flag disagrees with its source")
    return camera


@dataclass(frozen=True)
class SceneCamera:
    image_id: int
    intrinsics: CameraIntrinsics
    extrinsics: CameraExtrinsics

    def __post_init__(self):
        _identifier(self.image_id, "image_id")
        if any(self.intrinsics.distortion):
            raise ValueError("Scene cameras describe undistorted working pixels")

    @property
    def M(self) -> np.ndarray:
        # Lecture 002 pp. 82–83: full 3x4 M=K[R|O]=K[R|-R*C].
        return self.extrinsics.projection_matrix(self.intrinsics.K)

    def to_dict(self) -> dict:
        return {
            "image_id": self.image_id,
            "intrinsics": self.intrinsics.to_dict(),
            "R_cw": self.extrinsics.R_cw.tolist(),
            "C_w": self.extrinsics.C_w.tolist(),
        }


@dataclass(frozen=True)
class Observation:
    image_id: int
    feature_id: int
    pixel: tuple[float, float]
    reprojection_error_px: float | None = None

    def __post_init__(self):
        _identifier(self.image_id, "image_id")
        _identifier(self.feature_id, "feature_id")
        object.__setattr__(self, "pixel", _coordinates(self.pixel, 2))
        error = self.reprojection_error_px
        if error is not None and (not np.isfinite(error) or error < 0):
            raise ValueError("Reprojection error must be nonnegative and finite, or null")


@dataclass(frozen=True)
class SceneTrack:
    track_id: int
    observations: tuple[Observation, ...]
    point_id: int | None = None
    rejection_reason: str | None = None

    def __post_init__(self):
        _identifier(self.track_id, "track_id")
        if self.point_id is not None:
            _identifier(self.point_id, "point_id")
        object.__setattr__(self, "observations", tuple(self.observations))
        ids = [observation.image_id for observation in self.observations]
        if len(ids) < 2 or len(set(ids)) != len(ids):
            raise ValueError("A track needs observations from at least two distinct images")
        if self.rejection_reason is not None and (
            not isinstance(self.rejection_reason, str)
            or not self.rejection_reason
            or self.point_id is not None
        ):
            raise ValueError("Rejected tracks require a reason and cannot reference a 3D point")


@dataclass(frozen=True)
class ScenePoint:
    point_id: int
    track_id: int
    position: tuple[float, float, float]
    triangulation_angle_degrees: float

    def __post_init__(self):
        _identifier(self.point_id, "point_id")
        _identifier(self.track_id, "track_id")
        object.__setattr__(self, "position", _coordinates(self.position, 3))
        if (
            not np.isfinite(self.triangulation_angle_degrees)
            or not 0 <= self.triangulation_angle_degrees <= 90
        ):
            raise ValueError("Triangulation angle must be finite and in [0, 90]")


@dataclass(frozen=True)
class Scene:
    cameras: tuple[SceneCamera, ...]
    tracks: tuple[SceneTrack, ...]
    points: tuple[ScenePoint, ...]
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        for name in ("cameras", "tracks", "points"):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        cameras = {camera.image_id: camera for camera in self.cameras}
        tracks = {track.track_id: track for track in self.tracks}
        points = {point.point_id: point for point in self.points}
        if (
            len(cameras) != len(self.cameras)
            or len(tracks) != len(self.tracks)
            or len(points) != len(self.points)
        ):
            raise ValueError("Camera, track and point IDs must each be unique")
        used_observations = set()
        for track in self.tracks:
            for observation in track.observations:
                if observation.image_id not in cameras:
                    raise ValueError("Observation references a missing camera")
                key = observation.image_id, observation.feature_id
                if key in used_observations:
                    raise ValueError("A feature observation cannot belong to two tracks")
                used_observations.add(key)
            if track.point_id is not None:
                if (
                    track.point_id not in points
                    or points[track.point_id].track_id != track.track_id
                ):
                    raise ValueError("Track/point references must be reciprocal")
                if any(
                    observation.reprojection_error_px is None for observation in track.observations
                ):
                    raise ValueError("Reconstructed observations need reprojection errors")
        for point in self.points:
            if point.track_id not in tracks or tracks[point.track_id].point_id != point.point_id:
                raise ValueError("Point/track references must be reciprocal")
        if not isinstance(self.metadata, dict):
            raise ValueError("Scene metadata must be a JSON object")
        try:
            json.dumps(self.metadata, allow_nan=False)
        except (ValueError, TypeError) as error:
            raise ValueError("Scene metadata must be finite JSON data") from error

    def to_dict(self) -> dict:
        return {
            "schema_version": 1,
            "coordinate_frame": COORDINATE_FRAME,
            "cameras": [camera.to_dict() for camera in self.cameras],
            "tracks": [asdict(track) for track in self.tracks],
            "points": [asdict(point) for point in self.points],
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Scene":
        try:
            if data["schema_version"] != 1 or data["coordinate_frame"] != COORDINATE_FRAME:
                raise ValueError("Unsupported scene schema or coordinate frame")
            cameras = tuple(
                SceneCamera(
                    item["image_id"],
                    intrinsics_from_dict(item["intrinsics"]),
                    CameraExtrinsics(item["R_cw"], item["C_w"]),
                )
                for item in data["cameras"]
            )
            tracks = tuple(
                SceneTrack(
                    item["track_id"],
                    tuple(Observation(**observation) for observation in item["observations"]),
                    item["point_id"],
                    item["rejection_reason"],
                )
                for item in data["tracks"]
            )
            return cls(
                cameras,
                tracks,
                tuple(ScenePoint(**item) for item in data["points"]),
                data["metadata"],
            )
        except (KeyError, TypeError, AttributeError) as error:
            raise ValueError(f"Malformed scene: {error}") from error


def save_scene(path: str | Path, scene: Scene) -> Path:
    from .storage import write_document

    return write_document(path, scene.to_dict(), point_count=len(scene.points))


def load_scene(path: str | Path) -> Scene:
    from .storage import read_document

    return Scene.from_dict(read_document(path))
