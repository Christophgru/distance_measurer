"""Offline endpoint measurement with frozen cameras and one explicit reference.

Distances are conditional on the supplied visual reconstruction. No fitting of
cameras, dropping of observations, or selection by expected lengths occurs here.
"""

from dataclasses import asdict
from itertools import combinations

import numpy as np

from distance_measuring.scene.model import COORDINATE_FRAME
from distance_measuring.vision.triangulation import triangulate_point


def _number(value, name, *, zero=False):
    if (
        type(value) not in (int, float)
        or not np.isfinite(value)
        or (value < 0 if zero else value <= 0)
    ):
        raise ValueError(f"{name} must be a {'nonnegative' if zero else 'positive'} finite number")
    return float(value)


def validate_annotations(scene, data):
    """Validate the editable annotation contract without requiring complete marks."""
    if data.get("schema_version") != 1 or data.get("coordinate_frame") != COORDINATE_FRAME:
        raise ValueError("Unsupported measurement schema or coordinate frame")
    if not data.get("session_id") or data["session_id"] != scene.metadata.get("session_id"):
        raise ValueError("Annotations and scene must describe the same recording")
    cameras = {str(c.image_id): c for c in scene.cameras}
    sizes = data.get("image_sizes", {})
    if set(sizes) != set(cameras) or any(
        list(c.intrinsics.image_size) != sizes[i] for i, c in cameras.items()
    ):
        raise ValueError("Annotation image sizes must match all scene cameras")
    points = data.get("points")
    if not isinstance(points, dict) or len(points) < 4:
        raise ValueError("Supply at least four named endpoints")
    for name, point in points.items():
        if not isinstance(name, str) or not name or not isinstance(point, dict):
            raise ValueError("Each point needs a nonempty name and observation object")
        observations = point.get("observations")
        if not isinstance(observations, dict):
            raise ValueError("Point observations must be an image-ID object")
        for image_id, observation in observations.items():
            if image_id not in cameras or not isinstance(observation, dict):
                raise ValueError("Observation references an unknown camera or invalid object")
            pixel = np.asarray(observation.get("pixel"), float)
            if pixel.shape != (2,) or not np.isfinite(pixel).all():
                raise ValueError("Observation pixel must contain two finite coordinates")
            width, height = sizes[image_id]
            if not (0 <= pixel[0] < width and 0 <= pixel[1] < height):
                raise ValueError("Observation pixel is outside its working image")
            if type(observation.get("reviewed")) is not bool:
                raise ValueError("Every observation needs an explicit reviewed boolean")
    reference = data.get("reference", {})
    _number(reference.get("length_m"), "Reference length_m")

    def endpoints(item):
        pair = item.get("endpoints")
        if (
            not isinstance(pair, list)
            or len(pair) != 2
            or any(not isinstance(p, str) or p not in points for p in pair)
            or pair[0] == pair[1]
        ):
            raise ValueError("Intervals need two distinct named endpoints")
        return set(pair)

    reference_points = endpoints(reference)
    measurements = data.get("measurements")
    if not isinstance(measurements, list) or not measurements:
        raise ValueError("Supply at least one measurement interval")
    names = []
    for measurement in measurements:
        pair = endpoints(measurement)
        names.append(measurement.get("id"))
        if pair == reference_points:
            raise ValueError("A measurement cannot reuse the scale-setting interval")
        if "expected_length_m" in measurement:
            _number(measurement["expected_length_m"], "Expected length_m")
            if pair & reference_points:
                raise ValueError("Held-out validation endpoints must exclude reference endpoints")
    if any(not isinstance(n, str) or not n for n in names) or len(set(names)) != len(names):
        raise ValueError("Measurement IDs must be unique nonempty strings")
    groups = data.get("view_groups")
    if not isinstance(groups, list) or not groups:
        raise ValueError("Predeclare at least one view group")
    seen = set()
    for group in groups:
        if (
            not isinstance(group, list)
            or len(group) < 2
            or any(type(i) is not int or str(i) not in cameras for i in group)
            or len(set(group)) != len(group)
        ):
            raise ValueError("View groups need at least two distinct retained camera IDs")
        key = tuple(sorted(group))
        if key in seen:
            raise ValueError("Duplicate view groups do not establish repeatability")
        seen.add(key)
    _number(data.get("localization_radius_px"), "Localization radius", zero=True)


def evaluate_measurements(scene, data, *, sensitivity_trials=200):
    """Report geometry, lengths, repeatability and illustrative pixel sensitivity.

    'prototype_checks_passed' is a diagnostic with explicit 10% thresholds, not
    a physical accuracy certification. Formal ruler validation stays separate.
    """
    validate_annotations(scene, data)
    if type(sensitivity_trials) is not int or sensitivity_trials < 20:
        raise ValueError("Use at least 20 sensitivity trials")
    cameras = {c.image_id: c for c in scene.cameras}
    reference = data["reference"]
    measurements = data["measurements"]
    used = sorted(set(reference["endpoints"]).union(*(set(m["endpoints"]) for m in measurements)))
    reasons, groups = [], []
    if scene.metadata.get("quality_status") != "visual_validation_passed":
        reasons.append("visual_cameras_unverified")
    requested_ids = sorted(set(i for group in data["view_groups"] for i in group))
    base = {
        (name, i): np.array(data["points"][name]["observations"][str(i)]["pixel"])
        for name in used
        for i in requested_ids
        if str(i) in data["points"][name]["observations"]
    }

    def triangulate(ids, coordinates):
        return [
            triangulate_point(
                [coordinates[name, i] for i in ids],
                [cameras[i].intrinsics for i in ids],
                [cameras[i].extrinsics for i in ids],
            )
            for name in used
        ]

    def lengths(results):
        xyz = {name: np.array(p.position) for name, p in zip(used, results, strict=True)}
        a, b = reference["endpoints"]
        baseline = float(np.linalg.norm(xyz[b] - xyz[a]))
        distances = [
            float(np.linalg.norm(xyz[m["endpoints"][1]] - xyz[m["endpoints"][0]]))
            for m in measurements
        ]
        return baseline, distances

    nominal = []
    for ids in data["view_groups"]:
        missing = [[name, i] for name in used for i in ids if (name, i) not in base]
        row = {
            "image_ids": ids,
            "missing_observations": missing,
            "measurements": [],
            "geometry_passed": False,
        }
        groups.append(row)
        nominal.append(None)
        if missing:
            reasons.append("missing_observations")
            continue
        if any(
            not data["points"][name]["observations"][str(i)]["reviewed"]
            for name in used
            for i in ids
        ):
            reasons.append("observations_unreviewed")
        geometry = triangulate(ids, base)
        row["points"] = {name: asdict(p) for name, p in zip(used, geometry, strict=True)}
        row["geometry_passed"] = all(p.position is not None for p in geometry)
        if not row["geometry_passed"]:
            reasons.append("triangulation_rejected")
            continue
        baseline, distances = lengths(geometry)
        if baseline <= 0:
            reasons.append("zero_reference_length")
            row["geometry_passed"] = False
            continue
        nominal[-1] = (baseline, distances)
        row["diagnostic_local_reference_scale_m_per_unit"] = reference["length_m"] / baseline

    # One declared primary group establishes the only scale. Other groups
    # must retain it: independently rescaling each would conceal scale drift.
    scale = reference["length_m"] / nominal[0][0] if nominal[0] else None
    if scale is None:
        reasons.append("primary_reference_geometry_unavailable")
    samples = [[] for _ in groups]
    rng = np.random.default_rng(0)
    if scale is not None:
        for _ in range(sensitivity_trials):
            # Jitter each physical annotation once per trial, sharing that same
            # perturbation between overlapping groups and the primary reference.
            noisy = {
                key: pixel
                + rng.uniform(-data["localization_radius_px"], data["localization_radius_px"], 2)
                for key, pixel in base.items()
            }
            primary_fit = triangulate(data["view_groups"][0], noisy)
            if any(p.position is None for p in primary_fit):
                continue
            primary_baseline, primary_distances = lengths(primary_fit)
            if primary_baseline <= 0:
                continue
            trial_scale = reference["length_m"] / primary_baseline
            for n, row in enumerate(groups):
                if nominal[n] is None:
                    continue
                if n == 0:
                    distances = primary_distances
                else:
                    fit = triangulate(row["image_ids"], noisy)
                    if any(p.position is None for p in fit):
                        continue
                    _, distances = lengths(fit)
                samples[n].append([d * trial_scale for d in distances])

    for n, row in enumerate(groups):
        if nominal[n] is None or scale is None:
            continue
        baseline, distances = nominal[n]
        row["reference_scale_m_per_unit"] = scale
        row["reference_remeasured_length_m"] = baseline * scale
        row["reference_remeasurement_error_percent"] = 100 * (
            baseline * scale / reference["length_m"] - 1
        )
        if abs(row["reference_remeasurement_error_percent"]) > 10:
            reasons.append("reference_repeatability_over_10_percent")
        trial_values = samples[n]
        row["sensitivity_geometry_accepted_trials"] = len(trial_values)
        if len(trial_values) < 0.95 * sensitivity_trials:
            reasons.append("sensitivity_geometry_unstable")
        for k, (m, units) in enumerate(zip(measurements, distances, strict=True)):
            distance = units * scale
            result = {"id": m["id"], "length_m": distance}
            if "expected_length_m" in m:
                expected = m["expected_length_m"]
                result.update(
                    expected_length_m=expected, signed_error_percent=100 * (distance / expected - 1)
                )
                if abs(result["signed_error_percent"]) > 10:
                    reasons.append("held_out_error_over_10_percent")
            if trial_values and distance > 0:
                low, median, high = np.percentile(np.asarray(trial_values)[:, k], [5, 50, 95])
                relative = max(abs(low / distance - 1), abs(high / distance - 1))
                result["sensitivity"] = {
                    "p05_m": float(low),
                    "median_m": float(median),
                    "p95_m": float(high),
                    "max_relative_shift_percent": float(100 * relative),
                }
                if relative > 0.1:
                    reasons.append("localization_sensitivity_over_10_percent")
            else:
                reasons.append("measurement_sensitivity_unavailable")
            row["measurements"].append(result)
    repeatability = []
    # Distinct camera sets, with at least one new camera, are required. Report
    # shared cameras explicitly: these are repeat checks, not independent data.
    for m in measurements:
        supported = [
            (g["image_ids"], r["length_m"])
            for g in groups
            for r in g["measurements"]
            if r["id"] == m["id"]
        ]
        values = [v for _, v in supported]
        relative = None
        if len(values) >= 2 and np.median(values) > 0:
            relative = float(100 * (max(values) - min(values)) / np.median(values))
        repeatability.append(
            {
                "id": m["id"],
                "supported_groups": [i for i, _ in supported],
                "shared_cameras_between_groups": [
                    sorted(set(a) & set(b)) for (a, _), (b, _) in combinations(supported, 2)
                ],
                "range_over_median_percent": relative,
            }
        )
        if relative is None:
            reasons.append("additional_view_repeatability_missing")
        elif relative > 10:
            reasons.append("repeatability_over_10_percent")
    if not any("expected_length_m" in m for m in measurements):
        reasons.append("held_out_known_length_missing")
    return {
        "schema_version": 1,
        "session_id": data["session_id"],
        "scene_quality_status": scene.metadata.get("quality_status"),
        "coordinate_frame": data["coordinate_frame"],
        "reference": reference,
        "scale_reference_image_ids": data["view_groups"][0],
        "fixed_scale_m_per_unit": scale,
        "scale_refit_per_group": False,
        "view_groups": groups,
        "repeatability": repeatability,
        "localization_radius_working_px": data["localization_radius_px"],
        "sensitivity_trials": sensitivity_trials,
        "sensitivity_interpretation": (
            "Illustrative pixel perturbations; not confidence or accuracy bounds"
        ),
        "diagnostic_threshold_percent": 10,
        "blocking_reasons": sorted(set(reasons)),
        "prototype_checks_passed": not reasons,
        "metric_measurement_accepted": False,
        "camera_poses_modified": False,
        "scale_applied_to_scene": False,
    }
