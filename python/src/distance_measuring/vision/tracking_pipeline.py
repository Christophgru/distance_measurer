"""Run temporal tracking on ordered session images without changing raw captures."""

import hashlib
import json
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from time import perf_counter

import cv2
import numpy as np

from distance_measuring.camera import resolve_intrinsics
from distance_measuring.dataset import load_session, validate_session
from distance_measuring.visualization.matches import draw_match_diagnostic
from distance_measuring.visualization.tracking import draw_tracking_seeds

from .pipeline import _write_image, working_camera
from .tracking import DEFAULT_CONFIG, TemporalTracker, TrackingConfig, grid_occupancy


def _median(values):
    values = values[np.isfinite(values)]
    return float(np.median(values)) if len(values) else None


def track_session(
    session_path,
    output,
    *,
    image_indices=None,
    calibration_path=None,
    max_image_size=1600,
    config: TrackingConfig = DEFAULT_CONFIG,
):
    start = perf_counter()
    session_path, output = Path(session_path).resolve(), Path(output).resolve()
    raw_root = Path(__file__).resolve().parents[4] / "data/raw"
    if output.is_relative_to(session_path) or output.is_relative_to(raw_root.resolve()):
        raise ValueError("Tracking outputs must be outside data/raw and the raw session")
    if type(max_image_size) is not int or max_image_size < 32:
        raise ValueError("max_image_size must be an integer of at least 32")
    session = load_session(session_path, validate=False)
    validation = validate_session(session)
    validation.raise_for_errors()
    by_id = {e.index: e for e in session.images}
    selected = list(by_id) if image_indices is None else list(image_indices)
    if (
        len(selected) < 2
        or len(set(selected)) != len(selected)
        or any(i not in by_id for i in selected)
    ):
        raise ValueError("Select at least two distinct recorded image IDs")
    if selected != sorted(selected):
        raise ValueError("Selected images must follow capture order")
    paths = [session_path / name for name in ("session.json", "events.jsonl", "sensors.csv")]
    paths += [session.image_path(by_id[i]) for i in selected]

    def fingerprints():
        return {
            str(p.relative_to(session_path)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in paths
        }

    hashes = fingerprints()
    if calibration_path is not None:
        calibration_path = Path(calibration_path).resolve()
        if calibration_path == output or calibration_path.is_relative_to(output):
            raise ValueError("Tracking output must not contain the input calibration")
    output.mkdir(parents=True, exist_ok=False)
    tracker = TemporalTracker(config)
    report = {
        "schema_version": 1,
        "session_id": session.metadata.session_id,
        "selected_image_indices": selected,
        "coordinate_frame": "undistorted_working_pixels_ignore_exif",
        "settings": {
            **asdict(config),
            "max_image_size": max_image_size,
            "calibration_path": str(calibration_path) if calibration_path else None,
        },
        "software": {
            "opencv": cv2.__version__,
            "numpy": np.__version__,
            "opencv_threads": cv2.getNumThreads(),
        },
        "input_sha256": hashes,
        "images": [],
        "pairs": [],
        "observations_file": "observations.jsonl",
        "warnings": [w.message for w in validation.warnings],
        "quality_status": "optical_flow_unverified_by_geometry",
    }
    if calibration_path:
        report["calibration_sha256"] = hashlib.sha256(calibration_path.read_bytes()).hexdigest()
    lengths = Counter()
    previous_image, previous_id = None, None
    tracking_time = 0.0
    with (output / "observations.jsonl").open("w", encoding="utf-8") as observations:
        for index in selected:
            load_start = perf_counter()
            event = by_id[index]
            original = resolve_intrinsics(event.metadata, calibration_path=calibration_path)
            scale = min(1.0, max_image_size / max(original.image_size))
            size = tuple(max(1, round(v * scale)) for v in original.image_size)
            camera, affine = working_camera(original, size)
            image = cv2.imread(
                str(session.image_path(event)), cv2.IMREAD_COLOR | cv2.IMREAD_IGNORE_ORIENTATION
            )
            if image is None:
                raise ValueError(f"Cannot decode image {index}")
            image = cv2.resize(image, size, interpolation=cv2.INTER_AREA)
            image = camera.undistort_image(image) if any(camera.distortion) else image
            loading_s = perf_counter() - load_start
            source = tracker.active
            step_start = perf_counter()
            frame = tracker.process(image, index)
            step_s = perf_counter() - step_start
            tracking_time += step_s
            for observation in frame.observations:
                observations.write(json.dumps(asdict(observation), allow_nan=False) + "\n")
                if observation.status in ("seeded", "tracked"):
                    lengths[observation.track_id] += 1
            active_points = np.asarray([o.pixel for o in tracker.active], float).reshape(-1, 2)
            occupancy = grid_occupancy(active_points, image.shape, config)
            name = f"image_{index:03d}_tracks.jpg"
            _write_image(
                output / name,
                draw_tracking_seeds(
                    image,
                    active_points,
                    grid_rows=config.grid_rows,
                    grid_cols=config.grid_cols,
                    title=f"Image {index} active tracks",
                ),
            )
            report["images"].append(
                {
                    "index": index,
                    "filename": event.filename,
                    "timestamp_ns": event.timestamp_ns,
                    "timestamp_source": event.timestamp_source,
                    "stored_intrinsics": original.to_dict(),
                    "working_intrinsics": camera.without_distortion().to_dict(),
                    "stored_ideal_to_working_ideal_affine": affine.tolist(),
                    "active_count": len(tracker.active),
                    "occupied_grid_cells": int((occupancy > 0).sum()),
                    "grid_occupancy": occupancy.tolist(),
                    "new_point_count": len(frame.selection.points) if frame.selection else 0,
                    "detected_cells": list(frame.selection.visited_cells)
                    if frame.selection
                    else [],
                    "selection_runtime_s": frame.selection.runtime_s if frame.selection else 0.0,
                    "loading_runtime_s": loading_s,
                    "tracking_runtime_s": step_s,
                    "debug_image": name,
                }
            )
            if frame.flow is not None:
                flow = frame.flow
                stem = f"pair_{previous_id:03d}_{index:03d}"
                points = np.asarray([o.pixel for o in source], float).reshape(-1, 2)
                np.savez_compressed(
                    output / f"{stem}_flow.npz",
                    track_ids=np.array([o.track_id for o in source], dtype=np.int64),
                    source_pixels=points,
                    target_pixels=flow.points,
                    forward_status=flow.forward_status,
                    backward_status=flow.backward_status,
                    lk_error=flow.lk_error,
                    forward_backward_error_px=flow.forward_backward_error_px,
                    accepted=flow.accepted,
                )
                pair = {
                    "image_indices": [previous_id, index],
                    "input_count": len(points),
                    "accepted_count": int(flow.accepted.sum()),
                    "rejected_count": int((~flow.accepted).sum()),
                    "rejection_counts": dict(Counter(r for r in flow.rejection_reason if r)),
                    "median_forward_backward_error_px": _median(flow.forward_backward_error_px),
                    "median_accepted_forward_backward_error_px": _median(
                        flow.forward_backward_error_px[flow.accepted]
                    ),
                    "flow_runtime_s": flow.runtime_s,
                    "forward_lk_runtime_s": flow.forward_runtime_s,
                    "backward_lk_runtime_s": flow.backward_runtime_s,
                    "motion_initialization": flow.motion_initialization,
                    "arrays_file": f"{stem}_flow.npz",
                    "debug_image": f"{stem}_flow.jpg",
                }
                _write_image(
                    output / pair["debug_image"],
                    draw_match_diagnostic(
                        previous_image,
                        image,
                        points,
                        flow.points,
                        flow.accepted,
                        title=f"Images {previous_id}-{index}: LK + forward/backward check",
                        accepted_label="ACCEPTED by tracking checks",
                        rejected_label="REJECTED by tracking checks",
                    ),
                )
                report["pairs"].append(pair)
                if not pair["accepted_count"]:
                    report["warnings"].append(
                        f"No tracks survived pair {previous_id}-{index}; "
                        "temporal continuity is broken"
                    )
                print(
                    f"Pair {previous_id}-{index}: accepted={pair['accepted_count']}/{len(points)}, "
                    f"rejected={pair['rejected_count']}, "
                    f"median FB={pair['median_forward_backward_error_px']} px, "
                    f"flow={flow.runtime_s:.3f} s",
                    flush=True,
                )
                if flow.motion_initialization is not None:
                    initialization = flow.motion_initialization
                    print(
                        f"  SIFT initialization: features={initialization['feature_counts']}, "
                        f"matches={initialization['retained_match_count']}, "
                        f"H inliers={initialization['homography_inlier_count']}, "
                        f"selected={initialization['guided_retry_selected']}, "
                        f"runtime={initialization['runtime_s']:.3f} s",
                        flush=True,
                    )
            previous_image, previous_id = image, index
    if fingerprints() != hashes:
        raise ValueError("Raw tracking inputs changed during the run")
    report["summary"] = {
        "image_count": len(selected),
        "track_count": len(lengths),
        "persistent_track_count": sum(v >= 2 for v in lengths.values()),
        "tracks_with_three_observations": sum(v >= 3 for v in lengths.values()),
        "tracks_spanning_sequence": sum(v == len(selected) for v in lengths.values()),
        "track_length_histogram": dict(Counter(str(v) for v in lengths.values())),
        "tracking_runtime_s": tracking_time,
        "total_runtime_s": perf_counter() - start,
    }
    report["success"] = report["summary"]["persistent_track_count"] > 0
    (output / "tracking.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    return report
