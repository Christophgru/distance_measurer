"""Confidence from current visual ray angles, residuals and observation support."""

from dataclasses import asdict, dataclass, replace

import numpy as np

from .incremental import with_landmark_quality


@dataclass(frozen=True)
class ConfidenceConfig:
    min_score: float = 0.2
    target_angle_degrees: float = 3.0
    error_scale_px: float = 1.5
    target_observation_count: int = 3

    def __post_init__(self):
        if not np.isfinite(self.min_score) or not 0 <= self.min_score <= 1:
            raise ValueError("Confidence threshold must be in [0,1]")
        if not np.isfinite(self.target_angle_degrees) or not 0 < self.target_angle_degrees < 90:
            raise ValueError("Confidence angle must be in (0,90)")
        if not np.isfinite(self.error_scale_px) or self.error_scale_px <= 0:
            raise ValueError("Confidence error scale must be positive and finite")
        if type(self.target_observation_count) is not int or self.target_observation_count < 2:
            raise ValueError("Confidence observation target must be an integer >=2")


DEFAULT_CONFIDENCE = ConfidenceConfig()


@dataclass(frozen=True)
class LandmarkConfidence:
    trusted_point_ids: frozenset[int]
    report: dict


def score_landmarks(scene, *, config=DEFAULT_CONFIDENCE):
    """Recompute diagnostics from geometry; never trust cached error/angle fields."""
    cameras = {c.image_id: c for c in scene.cameras}
    tracks = {t.track_id: t for t in scene.tracks}
    valid_points, valid_tracks, invalid = [], [], {}
    for point in scene.points:
        fresh, reason = [], None
        for observation in tracks[point.track_id].observations:
            camera = cameras[observation.image_id]
            if camera.extrinsics.world_to_camera(point.position)[2] <= 0:
                reason = "nonpositive_depth"
                break
            if not (
                np.all(np.asarray(observation.pixel) >= 0)
                and np.all(np.asarray(observation.pixel) < camera.intrinsics.image_size)
            ):
                reason = "observation_outside_image"
                break
            error = float(
                np.linalg.norm(
                    camera.intrinsics.project(point.position, camera.extrinsics, distort=False)
                    - observation.pixel
                )
            )
            if not np.isfinite(error):
                reason = "nonfinite_reprojection_error"
                break
            fresh.append(replace(observation, reprojection_error_px=error))
        if reason:
            invalid[point.point_id] = reason
        else:
            valid_points.append(point)
            valid_tracks.append(replace(tracks[point.track_id], observations=tuple(fresh)))
    measured = with_landmark_quality(
        replace(scene, points=tuple(valid_points), tracks=tuple(valid_tracks))
    )
    quality = measured.metadata["landmark_quality"]
    trusted, rows = set(), []
    for point in scene.points:
        row = {
            "point_id": point.point_id,
            "track_id": point.track_id,
            "score": 0.0,
            "trusted": False,
            "reason": invalid.get(point.point_id),
        }
        if row["reason"] is None:
            q = quality[str(point.point_id)]
            spread = max(q["reprojection_errors_px"]) - min(q["reprojection_errors_px"])
            factors = {
                "angle": min(
                    1.0, q["median_triangulation_angle_degrees"] / config.target_angle_degrees
                ),
                "observations": min(1.0, q["observation_count"] / config.target_observation_count),
                "error": 1 / (1 + (q["reprojection_rmse_px"] / config.error_scale_px) ** 2),
                "view_consistency": 1 / (1 + (spread / config.error_scale_px) ** 2),
            }
            score = float(np.prod(list(factors.values())))
            accepted = score >= config.min_score
            row.update(
                score=score,
                trusted=accepted,
                factors=factors,
                diagnostics=q,
                reason=None if accepted else "below_confidence_threshold",
            )
            if accepted:
                trusted.add(point.point_id)
        rows.append(row)
    return LandmarkConfidence(
        frozenset(trusted),
        {
            "settings": asdict(config),
            "diagnostics_source": "current_scene_geometry",
            "point_count": len(rows),
            "trusted_point_count": len(trusted),
            "excluded_point_count": len(rows) - len(trusted),
            "landmarks": rows,
        },
    )
