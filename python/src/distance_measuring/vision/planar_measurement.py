"""Experimental measurements on one explicitly supported flat surface.

The plane is fitted from image correspondences and frozen K/R/C, without known
lengths. Query points use that same plane. Conditional repeatability does not
validate the plane: held-out texture transfers must be checked separately.
"""

from dataclasses import dataclass

import numpy as np
from scipy.optimize import least_squares


@dataclass(frozen=True)
class MeasurementPlane:
    """World plane n dot X = d, with unit n; scale stays in scene units."""

    normal_w: tuple[float, float, float]
    offset_w: float

    def __post_init__(self):
        normal = np.asarray(self.normal_w, float)
        if normal.shape != (3,) or not np.isfinite(normal).all():
            raise ValueError("Plane normal must have three finite components")
        norm = np.linalg.norm(normal)
        if not np.isfinite(norm) or norm <= 0 or not np.isfinite(self.offset_w):
            raise ValueError("Plane normal must be nonzero and offset finite")
        object.__setattr__(self, "normal_w", tuple(float(v) for v in normal / norm))
        object.__setattr__(self, "offset_w", float(self.offset_w / norm))


@dataclass(frozen=True)
class PlaneFit:
    plane: MeasurementPlane | None
    condition_number: float | None = None
    symmetric_error_rmse_px: float | None = None
    source_match_count: int = 0
    rejection_reason: str | None = None


@dataclass(frozen=True)
class PlanarPoint:
    position: tuple[float, float, float] | None
    reprojection_errors_px: tuple[float, ...] = ()
    incidence_angles_degrees: tuple[float, ...] = ()
    rejection_reason: str | None = None


def _pixels(values, camera):
    xy = np.asarray(values, float)
    if xy.ndim != 2 or xy.shape[1] != 2 or not np.isfinite(xy).all():
        raise ValueError("Pixels must be a finite Nx2 array")
    if any(camera.intrinsics.distortion):
        raise ValueError("Plane measurement requires undistorted working pixels")
    if np.any(xy < 0) or np.any(xy >= camera.intrinsics.image_size):
        raise ValueError("Surface observation is outside its working image")
    return xy


def fit_measurement_plane(source, target, source_pixels, target_pixels):
    """Fit a plane from >=12 preselected texture matches; never fit camera poses.

    Calibrated transfer is H(q) = Kt (R + t q.T) Ks^-1, q dot Xs = 1.
    Homography consensus/holdout selection belongs to the caller. This function
    uses every supplied correspondence with a soft-L1 pixel objective.
    """
    xy, uv = _pixels(source_pixels, source), _pixels(target_pixels, target)
    if len(xy) != len(uv) or len(xy) < 12:
        raise ValueError("Supply at least 12 aligned surface texture matches")
    rotation = target.extrinsics.R_cw @ source.extrinsics.R_cw.T
    translation = target.extrinsics.R_cw @ (source.extrinsics.C_w - target.extrinsics.C_w)
    if np.linalg.norm(translation) == 0:
        return PlaneFit(None, source_match_count=len(xy), rejection_reason="no_translation")
    rays = np.c_[source.intrinsics.normalize_pixels(xy), np.ones(len(xy))]
    other = np.c_[target.intrinsics.normalize_pixels(uv), np.ones(len(uv))]
    a, b = np.cross(other, translation), -np.cross(other, rays @ rotation.T)
    matrix = (a[:, :, None] * rays[:, None, :]).reshape(-1, 3)
    q, _, rank, singular = np.linalg.lstsq(matrix, b.ravel(), rcond=None)
    condition = float(singular[0] / singular[-1]) if singular[-1] > 0 else None
    if rank < 3 or condition is None or condition > 1e4:
        return PlaneFit(
            None,
            condition,
            source_match_count=len(xy),
            rejection_reason="surface_support_ill_conditioned",
        )
    homogeneous_xy, homogeneous_uv = np.c_[xy, np.ones(len(xy))], np.c_[uv, np.ones(len(uv))]

    def residual(parameters):
        model = target.intrinsics.K @ (rotation + np.outer(translation, parameters))
        model = model @ np.linalg.inv(source.intrinsics.K)
        try:
            forward = homogeneous_xy @ model.T
            reverse = homogeneous_uv @ np.linalg.inv(model).T
            if np.any(np.abs(forward[:, 2]) < 1e-12) or np.any(np.abs(reverse[:, 2]) < 1e-12):
                return np.full(len(xy) * 4, 1e6)
            return np.r_[
                (forward[:, :2] / forward[:, 2, None] - uv).ravel(),
                (reverse[:, :2] / reverse[:, 2, None] - xy).ravel(),
            ]
        except np.linalg.LinAlgError:
            return np.full(len(xy) * 4, 1e6)

    fit = least_squares(residual, q, loss="soft_l1", f_scale=1.0, max_nfev=100)
    error = float(np.sqrt(np.mean(residual(fit.x) ** 2)))
    if not fit.success or not np.isfinite(fit.x).all() or np.linalg.norm(fit.x) == 0:
        return PlaneFit(None, condition, error, len(xy), "surface_fit_failed")
    if np.any(rays @ fit.x <= 0):
        return PlaneFit(None, condition, error, len(xy), "surface_behind_source")
    source_xyz = rays / (rays @ fit.x)[:, None]
    if np.any((source_xyz @ rotation.T + translation)[:, 2] <= 0):
        return PlaneFit(None, condition, error, len(xy), "surface_behind_target")
    world_normal = source.extrinsics.R_cw.T @ fit.x
    plane = MeasurementPlane(tuple(world_normal), 1 + world_normal @ source.extrinsics.C_w)
    return PlaneFit(plane, condition, error, len(xy))


def intersect_measurement_plane(pixel, camera, plane, *, min_incidence_degrees=5.0):
    """Intersect a single camera ray with a fixed plane; reject grazing rays."""
    pixel = _pixels([pixel], camera)[0]
    if not np.isfinite(min_incidence_degrees) or not 0 < min_incidence_degrees < 90:
        raise ValueError("Minimum incidence angle must be in (0, 90)")
    ray = camera.extrinsics.R_cw.T @ np.r_[camera.intrinsics.normalize_pixels(pixel), 1.0]
    normal, center = np.array(plane.normal_w), camera.extrinsics.C_w
    denominator = float(normal @ ray)
    incidence = float(np.degrees(np.arcsin(np.clip(abs(denominator) / np.linalg.norm(ray), 0, 1))))
    if incidence < min_incidence_degrees:
        return PlanarPoint(
            None, incidence_angles_degrees=(incidence,), rejection_reason="grazing_plane_ray"
        )
    depth = (plane.offset_w - normal @ center) / denominator
    if depth <= 0:
        return PlanarPoint(
            None,
            incidence_angles_degrees=(incidence,),
            rejection_reason="plane_intersection_behind_camera",
        )
    xyz = center + depth * ray
    return PlanarPoint(tuple(float(v) for v in xyz), (0.0,), (incidence,))


def estimate_planar_point(pixels, cameras, plane, *, max_reprojection_error_px=3.0):
    """Fit a point on a frozen plane using all supplied views, without dropping any."""
    pixels = np.asarray(pixels, float)
    if pixels.shape != (len(cameras), 2) or not cameras:
        raise ValueError("Supply at least one aligned camera/pixel observation")
    if not np.isfinite(max_reprojection_error_px) or max_reprojection_error_px <= 0:
        raise ValueError("Maximum reprojection error must be positive and finite")
    rays = [intersect_measurement_plane(p, c, plane) for p, c in zip(pixels, cameras, strict=True)]
    incidence = tuple(r.incidence_angles_degrees[0] for r in rays)
    rejected = next((r.rejection_reason for r in rays if r.position is None), None)
    if rejected:
        return PlanarPoint(None, incidence_angles_degrees=incidence, rejection_reason=rejected)
    normal = np.array(plane.normal_w)
    first = np.cross(normal, np.eye(3)[np.argmin(np.abs(normal))])
    first /= np.linalg.norm(first)
    basis = np.c_[first, np.cross(normal, first)]
    origin = plane.offset_w * normal
    initial = (np.mean([r.position for r in rays], axis=0) - origin) @ basis

    def residual(parameters):
        xyz = origin + basis @ parameters
        projected = []
        for camera in cameras:
            position = camera.extrinsics.world_to_camera(xyz)
            if position[2] <= 1e-12:
                return np.full(len(cameras) * 2, 1e6)
            homogeneous = camera.intrinsics.K @ position
            projected.append(homogeneous[:2] / homogeneous[2])
        return (np.array(projected) - pixels).ravel()

    fit = least_squares(residual, initial, max_nfev=50)
    xyz = origin + basis @ fit.x
    errors = tuple(float(v) for v in np.linalg.norm(residual(fit.x).reshape(-1, 2), axis=1))
    if not fit.success or not np.isfinite(xyz).all():
        return PlanarPoint(None, errors, incidence, "planar_point_fit_failed")
    if any(c.extrinsics.world_to_camera(xyz)[2] <= 0 for c in cameras):
        return PlanarPoint(None, errors, incidence, "plane_intersection_behind_camera")
    if max(errors) > max_reprojection_error_px:
        return PlanarPoint(None, errors, incidence, "high_reprojection_error")
    return PlanarPoint(tuple(float(v) for v in xyz), errors, incidence)


def check_plane_transfer(source, target, source_pixels, target_pixels, plane):
    """Evaluate held-out texture, independently of measurement endpoint lengths."""
    xy, uv = _pixels(source_pixels, source), _pixels(target_pixels, target)
    if len(xy) != len(uv) or len(xy) < 8:
        raise ValueError("Supply at least eight aligned held-out texture observations")
    errors = []
    for a, b in zip(xy, uv, strict=True):
        point = intersect_measurement_plane(a, source, plane)
        if point.position is None or target.extrinsics.world_to_camera(point.position)[2] <= 0:
            errors.append(None)
        else:
            predicted = target.intrinsics.project(point.position, target.extrinsics, distort=False)
            errors.append(float(np.linalg.norm(predicted - b)))
    finite = [e for e in errors if e is not None]
    passed = sum(e is not None and e <= 3.0 for e in errors)
    return {
        "image_ids": [source.image_id, target.image_id],
        "count": len(errors),
        "errors_working_px": errors,
        "median_error_working_px": float(np.median(finite)) if finite else None,
        "fraction_within_3px": passed / len(errors),
        "passes_80_percent_within_3px": passed / len(errors) >= 0.8,
    }
