"""Bounded patch search and pose-derived epipolar descriptor corridors."""

from dataclasses import dataclass
from time import perf_counter

import cv2
import numpy as np
from scipy.spatial.transform import Rotation

from distance_measuring.camera import CameraExtrinsics, to_homogeneous

from .correlation import correlate_local_point
from .matching import match_descriptors


@dataclass(frozen=True)
class GuidedConfig:
    search_radius_px: int = 32
    patch_size: int = 21
    min_correlation: float = 0.75
    min_peak_margin: float = 0.05
    mutual: bool = True
    mutual_tolerance_px: float = 2.0
    corridor_px: float = 2.0
    descriptor_ratio: float = 0.75
    max_features: int = 1200
    max_epipolar_queries: int = 300
    merge_radius_px: float = 2.0
    max_reprojection_error_px: float = 3.0
    min_triangulation_angle_degrees: float = 1.0
    max_gyro_disagreement_degrees: float = 15.0

    def __post_init__(self):
        for name in ("search_radius_px", "max_features", "max_epipolar_queries"):
            if type(getattr(self, name)) is not int or getattr(self, name) < 1:
                raise ValueError(f"{name} must be a positive integer")
        if type(self.patch_size) is not int or self.patch_size < 3 or self.patch_size % 2 != 1:
            raise ValueError("patch_size must be an odd integer >=3")
        for name in (
            "mutual_tolerance_px",
            "corridor_px",
            "merge_radius_px",
            "max_reprojection_error_px",
            "min_triangulation_angle_degrees",
            "max_gyro_disagreement_degrees",
        ):
            if not np.isfinite(getattr(self, name)) or getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive and finite")
        if self.min_triangulation_angle_degrees >= 90 or self.max_gyro_disagreement_degrees > 180:
            raise ValueError("Invalid angle threshold")
        if type(self.mutual) is not bool:
            raise ValueError("mutual must be boolean")
        if not np.isfinite(self.min_correlation) or not -1 <= self.min_correlation <= 1:
            raise ValueError("min_correlation must be in [-1,1]")
        if not np.isfinite(self.min_peak_margin) or not 0 <= self.min_peak_margin <= 2:
            raise ValueError("min_peak_margin must be in [0,2]")
        if not np.isfinite(self.descriptor_ratio) or not 0 < self.descriptor_ratio < 1:
            raise ValueError("descriptor_ratio must be in (0,1)")


DEFAULT_GUIDED = GuidedConfig()


def predict_pose(previous, latest, fraction, *, gyro_rotation=None, config=DEFAULT_GUIDED):
    """Constant visual velocity; use an optional gyro prior only when consistent."""
    if not np.isfinite(fraction):
        raise ValueError("Pose prediction interval ratio must be finite")
    delta = latest.R_cw @ previous.R_cw.T
    predicted = Rotation.from_matrix(delta).__pow__(fraction).as_matrix() @ latest.R_cw
    report = {
        "method": "constant_visual_velocity",
        "gyro_used": False,
        "gyro_disagreement_degrees": None,
    }
    if gyro_rotation is not None:
        # Constructor validates SO(3), including finite/proper rotation.
        gyro = CameraExtrinsics(gyro_rotation, np.zeros(3)).R_cw @ latest.R_cw
        angle = float(np.degrees(Rotation.from_matrix(gyro @ predicted.T).magnitude()))
        report["gyro_disagreement_degrees"] = angle
        if angle <= config.max_gyro_disagreement_degrees:
            predicted = gyro
            report.update(method="consistent_gyro_rotation_visual_translation", gyro_used=True)
        else:
            report["gyro_rejection_reason"] = "inconsistent_with_visual_motion_prediction"
    return CameraExtrinsics(predicted, latest.C_w + fraction * (latest.C_w - previous.C_w)), report


def project_landmarks(scene, camera, pose, *, config=DEFAULT_GUIDED):
    tracks = {t.track_id: t for t in scene.tracks}
    rows = []
    for point in scene.points:
        depth = float(pose.world_to_camera(point.position)[2])
        pixel = camera.project(point.position, pose, distort=False) if depth > 0 else None
        visible = pixel is not None and bool(
            np.all(pixel >= 0) and np.all(pixel < camera.image_size)
        )
        rows.append(
            {
                "point_id": point.point_id,
                "track_id": point.track_id,
                "predicted_pixel": pixel.tolist() if pixel is not None else None,
                "positive_depth": depth > 0,
                "visible": visible,
                "observation_count": len(tracks[point.track_id].observations),
                "search_radius_px": config.search_radius_px,
            }
        )
    return rows


def search_landmark_patches(scene, images, target_image, projections, *, config=DEFAULT_GUIDED):
    """NCC over (2r+1)^2 centers, with ambiguity and optional reverse checks."""
    start = perf_counter()
    tracks = {t.track_id: t for t in scene.tracks}
    rows, occupied = [], []
    for projection in projections:
        row = {**projection, "accepted": False, "reason": "not_visible", "candidates_examined": 0}
        if projection["visible"]:
            # The latest available observation is the closest temporal template.
            source = max(tracks[row["track_id"]].observations, key=lambda o: o.image_id)
            result = correlate_local_point(
                images[source.image_id],
                target_image,
                source.pixel,
                projection["predicted_pixel"],
                patch_size=config.patch_size,
                search_radius=config.search_radius_px,
                min_score=config.min_correlation,
                min_margin=config.min_peak_margin,
            )
            row.update(result, source_image_id=source.image_id, source_pixel=list(source.pixel))
            if result["accepted"] and config.mutual:
                reverse = correlate_local_point(
                    target_image,
                    images[source.image_id],
                    result["second_pixel_refined"],
                    source.pixel,
                    patch_size=config.patch_size,
                    search_radius=config.search_radius_px,
                    min_score=config.min_correlation,
                    min_margin=config.min_peak_margin,
                )
                row["reverse_candidates_examined"] = reverse.get("candidates_examined", 0)
                if (
                    not reverse["accepted"]
                    or np.linalg.norm(np.asarray(reverse["second_pixel_refined"]) - source.pixel)
                    > config.mutual_tolerance_px
                ):
                    row.update(accepted=False, reason="mutual_patch_check_failed")
        rows.append(row)
    # One target patch cannot confirm two different landmarks.
    for row in sorted(rows, key=lambda r: (-(r.get("score") or -1), r["point_id"])):
        if row["accepted"]:
            pixel = np.asarray(row["second_pixel_refined"])
            if any(np.linalg.norm(pixel - other) <= config.merge_radius_px for other in occupied):
                row.update(accepted=False, reason="target_patch_already_used")
            else:
                occupied.append(pixel)
    exhaustive = max(0, target_image.shape[0] - config.patch_size + 1) * max(
        0, target_image.shape[1] - config.patch_size + 1
    )
    total = sum(r["candidates_examined"] for r in rows)
    evaluated = sum(r["candidates_examined"] > 0 for r in rows)
    return rows, {
        "query_count": len(rows),
        "accepted_count": sum(r["accepted"] for r in rows),
        "candidates_examined": total,
        "evaluated_query_count": evaluated,
        "average_candidates_per_landmark": total / len(rows) if rows else 0,
        "average_candidates_per_evaluated_landmark": total / evaluated if evaluated else 0,
        "exhaustive_candidates_per_landmark": exhaustive,
        "candidate_fraction_of_exhaustive": total / (len(rows) * exhaustive)
        if rows and exhaustive
        else 0,
        "reverse_candidates_examined": sum(r.get("reverse_candidates_examined", 0) for r in rows),
        "runtime_s": perf_counter() - start,
    }


def fundamental_from_cameras(first, second):
    rotation = second.extrinsics.R_cw @ first.extrinsics.R_cw.T
    translation = second.extrinsics.R_cw @ (first.extrinsics.C_w - second.extrinsics.C_w)
    x, y, z = translation
    cross = np.array([[0, -z, y], [z, 0, -x], [-y, x, 0]])
    matrix = (
        np.linalg.inv(second.intrinsics.K).T @ cross @ rotation @ np.linalg.inv(first.intrinsics.K)
    )
    norm = np.linalg.norm(matrix)
    return matrix / norm if norm > 1e-15 else None


def epipolar_search(
    first, second, first_camera, second_camera, *, query_indices=None, config=DEFAULT_GUIDED
):
    """Evaluate descriptors only within the pose-derived line corridor; no rectification."""
    start = perf_counter()
    matrix = fundamental_from_cameras(first_camera, second_camera)
    queries = np.arange(len(first)) if query_indices is None else np.asarray(query_indices, int)
    source_bounds = (first.points >= 0).all(axis=1) & (
        first.points < first_camera.intrinsics.image_size
    ).all(axis=1)
    target_bounds = (second.points >= 0).all(axis=1) & (
        second.points < second_camera.intrinsics.image_size
    ).all(axis=1)
    queries = queries[source_bounds[queries]]
    queries = queries[: config.max_epipolar_queries]
    rows, matches, used = [], [], set()
    if matrix is None:
        return [], {
            "reason": "coincident_camera_centers",
            "queries": [],
            "query_count": 0,
            "average_candidates_per_feature": 0,
            "runtime_s": perf_counter() - start,
        }
    targets = to_homogeneous(second.points)
    sources = to_homogeneous(first.points)
    reverse_total = 0
    for i in queries:
        line = matrix @ sources[i]
        length = np.linalg.norm(line[:2])
        distance = (
            np.abs(targets @ line) / length if length > 1e-15 else np.full(len(second), np.inf)
        )
        candidates = np.flatnonzero(target_bounds & (distance <= config.corridor_px))
        row = {
            "source_index": int(i),
            "line": line.tolist(),
            "candidate_count": len(candidates),
            "accepted": False,
            "reason": "no_unambiguous_descriptor",
            "candidate_pixels": second.points[candidates[:20]].tolist(),
        }
        result = match_descriptors(
            first.descriptors[i : i + 1],
            second.descriptors[candidates],
            ratio=config.descriptor_ratio,
            mutual=False,
        )
        if len(result):
            j = int(candidates[result.indices[0, 1]])
            reverse_line = matrix.T @ targets[j]
            reverse_length = np.linalg.norm(reverse_line[:2])
            reverse_distance = (
                np.abs(sources @ reverse_line) / reverse_length
                if reverse_length > 1e-15
                else np.full(len(first), np.inf)
            )
            allowed = np.flatnonzero(source_bounds & (reverse_distance <= config.corridor_px))
            consistent = reverse_distance[i] <= config.corridor_px
            if config.mutual and consistent:
                reverse_total += len(allowed)
                nearest = cv2.BFMatcher(cv2.NORM_L2).match(
                    second.descriptors[j : j + 1], first.descriptors[allowed]
                )
                consistent = bool(nearest) and allowed[nearest[0].trainIdx] == i
            row.update(
                target_index=j,
                target_pixel=second.points[j].tolist(),
                descriptor_ratio=float(result.ratios[0]),
                distance_px=float(distance[j]),
            )
            if consistent and j not in used:
                used.add(j)
                row.update(accepted=True, reason=None)
                matches.append((int(i), j, float(result.ratios[0])))
            else:
                row["reason"] = "mutual_epipolar_or_unique_target_check_failed"
        rows.append(row)
    total = sum(r["candidate_count"] for r in rows)
    return matches, {
        "reason": None,
        "F": matrix.tolist(),
        "queries": rows,
        "query_count": len(rows),
        "accepted_count": len(matches),
        "candidates_examined": total,
        "average_candidates_per_feature": total / len(rows) if rows else 0,
        "exhaustive_candidates_per_feature": len(second),
        "reverse_candidates_examined": reverse_total,
        "runtime_s": perf_counter() - start,
    }
