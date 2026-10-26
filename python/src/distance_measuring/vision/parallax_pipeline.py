"""Analyze immutable P2 tracks against recorded gyro rotation and rank candidates."""

import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from time import perf_counter

import cv2
import numpy as np
import scipy
from scipy.spatial.transform import Rotation

from distance_measuring.dataset import load_session, validate_session
from distance_measuring.dataset.schema import CLOCK_DOMAIN
from distance_measuring.inertial.rotation import camera_ray_rotation, integrate_gyro_interval
from distance_measuring.scene.model import intrinsics_from_dict
from distance_measuring.visualization.tracking import draw_rotation_parallax

from .parallax import rotation_compensated_parallax
from .pipeline import _write_image, working_camera
from .pose_pipeline import _artifact
from .track_ranking import DEFAULT_RANKING, RankingConfig, rank_tracks


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _median(values):
    finite = values[np.isfinite(values)]
    return float(np.median(finite)) if len(finite) else None


def _load_pair(root, pair, observations):
    path = _artifact(root, pair["arrays_file"])
    with np.load(path, allow_pickle=False) as arrays:
        ids, first, second, mask, fb = [
            arrays[k]
            for k in (
                "track_ids",
                "source_pixels",
                "target_pixels",
                "accepted",
                "forward_backward_error_px",
            )
        ]
    n = len(ids)
    if (
        ids.shape != (n,)
        or ids.dtype.kind not in "iu"
        or np.any(ids < 0)
        or len(set(ids)) != n
        or first.shape != (n, 2)
        or second.shape != (n, 2)
        or fb.shape != (n,)
        or mask.shape != (n,)
        or mask.dtype != bool
        or pair["input_count"] != n
        or pair["accepted_count"] != int(mask.sum())
    ):
        raise ValueError("P2 pair IDs, counts, coordinates and boolean masks must align")
    i, j = pair["image_indices"]
    rows = np.flatnonzero(mask)
    ages, responses = [], []
    for row in rows:
        a, b = observations[i, int(ids[row])], observations[j, int(ids[row])]
        if (
            not np.array_equal(first[row], a["pixel"])
            or not np.array_equal(second[row], b["pixel"])
            or b["age"] != a["age"] + 1
            or b["response"] != a["response"]
            or b["forward_backward_error_px"] != fb[row]
        ):
            raise ValueError("P2 pair pixels/ages/responses disagree with observation history")
        ages.append(b["age"])
        responses.append(b["response"])
    return (
        ids[rows],
        first[rows],
        second[rows],
        fb[rows],
        np.array(ages, int),
        np.array(responses, float),
        rows,
    )


def analyze_track_parallax(
    session_path,
    tracking_directory,
    output,
    *,
    best_n=50,
    config: RankingConfig = DEFAULT_RANKING,
    max_gyro_gap_s=0.25,
):
    start = perf_counter()
    session_path, root, output = [
        Path(p).resolve() for p in (session_path, tracking_directory, output)
    ]
    raw_root = Path(__file__).resolve().parents[4] / "data/raw"
    if output.is_relative_to(raw_root.resolve()) or any(
        output.is_relative_to(p) for p in (session_path, root)
    ):
        raise ValueError("Parallax outputs must be outside data/raw, the raw session and P2 inputs")
    if type(best_n) is not int or best_n < 1:
        raise ValueError("best_n must be a positive integer")
    if not np.isfinite(max_gyro_gap_s) or max_gyro_gap_s <= 0:
        raise ValueError("max_gyro_gap_s must be positive and finite")
    session = load_session(session_path, validate=False)
    validation = validate_session(session)
    validation.raise_for_errors()
    manifest_path = root / "tracking.json"
    tracking = json.loads(manifest_path.read_text())
    if (
        tracking["schema_version"] != 1
        or tracking["coordinate_frame"] != "undistorted_working_pixels_ignore_exif"
    ):
        raise ValueError("P3 requires P2 schema 1 in ideal working pixels")
    if tracking["session_id"] != session.metadata.session_id:
        raise ValueError("P2 tracks and recording describe different sessions")
    selected = tracking["selected_image_indices"]
    events = {e.index: e for e in session.images}
    images = {r["index"]: r for r in tracking["images"]}
    if (
        len(selected) < 2
        or selected != sorted(set(selected))
        or set(selected) != set(images)
        or any(
            i not in events or images[i]["timestamp_ns"] != events[i].timestamp_ns for i in selected
        )
    ):
        raise ValueError("P2 selected IDs/timestamps must agree with the recording")
    if [p["image_indices"] for p in tracking["pairs"]] != [
        list(p) for p in zip(selected[:-1], selected[1:], strict=True)
    ]:
        raise ValueError("P2 must contain the consecutive selected pairs in order")
    required_raw = {"session.json", "events.jsonl", "sensors.csv"} | {
        events[i].filename for i in selected
    }
    if not required_raw <= tracking["input_sha256"].keys():
        raise ValueError("P2 source hashes must include metadata, gyro data and selected images")
    fingerprints = {manifest_path: _sha(manifest_path)}
    for name, expected in tracking["input_sha256"].items():
        path = _artifact(session.path, name)
        if _sha(path) != expected:
            raise ValueError(f"Recording changed since P2 tracking: {name}")
        fingerprints[path] = expected
    history_path = _artifact(root, tracking["observations_file"])
    fingerprints[history_path] = _sha(history_path)
    observations = {}
    for line in history_path.read_text().splitlines():
        row = json.loads(line)
        if row["status"] in ("seeded", "tracked"):
            key = row["image_id"], row["track_id"]
            if key in observations:
                raise ValueError("P2 contains duplicate accepted track observations")
            observations[key] = row
    output.mkdir(parents=True, exist_ok=False)
    report = {
        "schema_version": 1,
        "session_id": session.metadata.session_id,
        "selected_image_indices": selected,
        "tracking_directory": str(root),
        "images": [
            {
                "index": k,
                "timestamp_ns": events[k].timestamp_ns,
                "working_intrinsics": images[k]["working_intrinsics"],
            }
            for k in selected
        ],
        "coordinate_frame": tracking["coordinate_frame"],
        "software": {
            "numpy": np.__version__,
            "scipy": scipy.__version__,
            "opencv": cv2.__version__,
        },
        "rotation_convention": "R_second_first maps first-camera rays into second-camera axes",
        "settings": {**asdict(config), "best_n": best_n, "max_gyro_gap_s": max_gyro_gap_s},
        "assumptions": {
            "gyro_bias_estimated": False,
            "mounting_calibrated": False,
            "metric_translation_estimated": False,
            "metric_scale_estimated": False,
        },
        "tracking_manifest_sha256": fingerprints[manifest_path],
        "raw_input_sha256": tracking["input_sha256"],
        "warnings": [w.message for w in validation.warnings],
        "pairs": [],
        "quality_status": "nominal_gyro_rotation_and_pixel_parallax_unverified",
    }
    for pair in tracking["pairs"]:
        pair_start = perf_counter()
        i, j = pair["image_indices"]
        arrays_path = _artifact(root, pair["arrays_file"])
        fingerprints[arrays_path] = _sha(arrays_path)
        ids, first, second, fb, ages, response, p2_rows = _load_pair(root, pair, observations)
        row = {"image_indices": [i, j], "input_track_count": len(ids)}
        try:
            if session.metadata.clock_domain != CLOCK_DOMAIN or any(
                events[k].clock_domain != CLOCK_DOMAIN for k in (i, j)
            ):
                raise ValueError(
                    "Image and gyro clock domains must explicitly share Android elapsed realtime"
                )
            interval = integrate_gyro_interval(
                session, events[i].timestamp_ns, events[j].timestamp_ns, max_gap_s=max_gyro_gap_s
            )
            rotation = camera_ray_rotation(
                interval.R_device_second_first, events[i].metadata, events[j].metadata
            )
        except ValueError as error:
            row.update(available=False, reason=str(error), runtime_s=perf_counter() - pair_start)
            report["pairs"].append(row)
            print(f"Pair {i}-{j}: rotation unavailable: {error}", flush=True)
            continue
        a, b = [intrinsics_from_dict(images[k]["working_intrinsics"]) for k in (i, j)]
        geometry_start = perf_counter()
        parallax = rotation_compensated_parallax(first, second, a, b, rotation)
        ranking = rank_tracks(
            first,
            second,
            track_ids=ids,
            ages=ages,
            responses=response,
            fb_errors_px=fb,
            parallax_px=parallax.parallax_px,
            first_size=a.image_size,
            second_size=b.image_size,
            best_n=best_n,
            config=config,
        )
        geometry_s = perf_counter() - geometry_start
        chosen = ranking.selected_indices
        raw_motion = _median(np.linalg.norm(second - first, axis=1))
        compensated_motion = _median(parallax.parallax_px)
        stem = f"pair_{i:03d}_{j:03d}"
        np.savez_compressed(
            output / f"{stem}_parallax.npz",
            track_ids=ids,
            p2_row_indices=p2_rows,
            source_pixels=first,
            target_pixels=second,
            rotation_only_pixels=parallax.predicted_pixels,
            residual_vectors_px=parallax.residual_vectors_px,
            parallax_px=parallax.parallax_px,
            prediction_valid=parallax.valid,
            prediction_in_bounds=parallax.predicted_in_bounds,
            eligible=ranking.eligible,
            scores=ranking.scores,
            selected_indices=chosen,
            ages=ages,
            response=response,
            fb_error_px=fb,
            R_second_first=rotation,
        )
        stored = intrinsics_from_dict(images[j]["stored_intrinsics"])
        display_camera, _ = working_camera(stored, b.image_size)
        image = cv2.imread(
            str(session.image_path(events[j])), cv2.IMREAD_COLOR | cv2.IMREAD_IGNORE_ORIENTATION
        )
        image = cv2.resize(image, b.image_size, interpolation=cv2.INTER_AREA)
        image = display_camera.undistort_image(image) if any(display_camera.distortion) else image
        _write_image(
            output / f"{stem}_parallax.jpg",
            draw_rotation_parallax(
                image,
                parallax.predicted_pixels,
                second,
                parallax.predicted_in_bounds,
                chosen,
                grid_rows=config.grid_rows,
                grid_cols=config.grid_cols,
            ),
        )
        row.update(
            available=True,
            gyro=interval.to_dict(),
            R_second_first=rotation.tolist(),
            device_rotation_angle_deg=float(
                np.degrees(Rotation.from_matrix(interval.R_device_second_first).magnitude())
            ),
            timestamp_sources=[events[k].timestamp_source for k in (i, j)],
            sensor_orientation_degrees=[
                events[k].metadata.sensor_orientation_degrees for k in (i, j)
            ],
            approximate_image_timestamps=any(
                events[k].timestamp_source != "camera_frame" for k in (i, j)
            ),
            valid_prediction_count=int(parallax.valid.sum()),
            in_bounds_prediction_count=int(parallax.predicted_in_bounds.sum()),
            median_observed_motion_px=raw_motion,
            median_parallax_px=compensated_motion,
            compensation_increases_median_motion=(
                compensated_motion > raw_motion + max(1e-6, 0.01 * raw_motion)
            )
            if compensated_motion is not None and raw_motion is not None
            else None,
            selected_median_parallax_px=_median(parallax.parallax_px[chosen]),
            eligible_count=int(ranking.eligible.sum()),
            selected_count=len(chosen),
            selection_shortfall=max(0, best_n - len(chosen)),
            available_source_cells=len(set(ranking.source_cells[ranking.eligible])),
            available_target_cells=len(set(ranking.target_cells[ranking.eligible])),
            selected_source_cells=len(set(ranking.source_cells[chosen])),
            selected_target_cells=len(set(ranking.target_cells[chosen])),
            geometry_ranking_runtime_s=geometry_s,
            runtime_s=perf_counter() - pair_start,
            arrays_file=f"{stem}_parallax.npz",
            debug_image=f"{stem}_parallax.jpg",
            selected_tracks=[
                {
                    "track_id": int(ids[k]),
                    "source_pixel": first[k].tolist(),
                    "target_pixel": second[k].tolist(),
                    "rotation_only_pixel": parallax.predicted_pixels[k].tolist(),
                    "age": int(ages[k]),
                    "response": float(response[k]),
                    "fb_error_px": float(fb[k]),
                    "parallax_px": float(parallax.parallax_px[k]),
                    "score": float(ranking.scores[k]),
                    "score_components": {
                        name: float(v[k]) for name, v in ranking.components.items()
                    },
                    "source_cell": int(ranking.source_cells[k]),
                    "target_cell": int(ranking.target_cells[k]),
                }
                for k in chosen
            ],
        )
        if row["compensation_increases_median_motion"]:
            message = (
                f"Pair {i}-{j}: nominal gyro compensation increases median image displacement; "
                "residual may include timing, mounting and calibration error as well as translation"
            )
            report["warnings"].append(message)
            print(f"Warning: {message}", flush=True)
        report["pairs"].append(row)
        print(
            f"Pair {i}-{j}: gyro={interval.sample_count} samples/{interval.sample_span_s:.3f} s, "
            f"rotation={row['device_rotation_angle_deg']:.3f} deg, "
            f"median parallax={row['median_parallax_px']} px, selected={len(chosen)}/{best_n}",
            flush=True,
        )
    if any(_sha(path) != fingerprint for path, fingerprint in fingerprints.items()):
        raise ValueError("P3 source inputs changed during analysis")
    report["success"] = all(p["available"] for p in report["pairs"])
    report["total_runtime_s"] = perf_counter() - start
    (output / "parallax.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    return report
