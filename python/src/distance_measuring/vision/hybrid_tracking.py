"""Replay preview tracks and export image-verified JPEG observations as P2 inputs."""

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
from distance_measuring.dataset.schema import CLOCK_DOMAIN, capture_path
from distance_measuring.dataset.tracking_validation import _geometry_key
from distance_measuring.visualization.matches import draw_match_diagnostic
from distance_measuring.visualization.tracking import draw_tracking_seeds

from .hybrid import DEFAULT_HYBRID, PreviewSnapshot, associate_still
from .hybrid_diagnostics import capture_timing_diagnostics
from .pipeline import _write_image, working_camera
from .tracking import DEFAULT_CONFIG, TemporalTracker, grid_occupancy
from .tracking_pipeline import _median


def hybrid_input_paths(session):
    return [session.path / name for name in ("tracking.json", "tracking/events.jsonl")] + [
        capture_path(session.path, f.filename) for f in session.tracking.frames
    ]


def track_hybrid_session(
    session_path,
    output,
    *,
    image_indices=None,
    calibration_path=None,
    max_image_size=1600,
    config=DEFAULT_CONFIG,
    hybrid_config=DEFAULT_HYBRID,
    max_gyro_gap_s=0.25,
):
    start = perf_counter()
    session_path, output = Path(session_path).resolve(), Path(output).resolve()
    raw_root = Path(__file__).resolve().parents[4] / "data/raw"
    if output.is_relative_to(session_path) or output.is_relative_to(raw_root.resolve()):
        raise ValueError("Hybrid tracking outputs must be outside raw captures")
    if type(max_image_size) is not int or max_image_size < 32:
        raise ValueError("max_image_size must be an integer of at least 32")
    session = load_session(session_path, validate=False)
    validation = validate_session(session)
    validation.raise_for_errors()
    if session.tracking is None or not session.tracking.frames:
        raise ValueError("Hybrid replay requires a nonempty tracking-frame sidecar")
    events = {i.index: i for i in session.images}
    selected = sorted(events) if image_indices is None else list(image_indices)
    if (
        len(selected) < 2
        or selected != sorted(set(selected))
        or any(type(i) is not int or i not in events for i in selected)
    ):
        raise ValueError("Select at least two distinct still IDs in capture order")
    if calibration_path is not None:
        calibration_path = Path(calibration_path).resolve(strict=True)
        if calibration_path.is_relative_to(output):
            raise ValueError("Hybrid output cannot contain the calibration input")
    originals = {
        i: resolve_intrinsics(events[i].metadata, calibration_path=calibration_path)
        for i in selected
    }
    cameras, affines = {}, {}
    for i, camera in originals.items():
        scale = min(1.0, max_image_size / max(camera.image_size))
        size = tuple(max(1, round(v * scale)) for v in camera.image_size)
        cameras[i], affines[i] = working_camera(camera, size)
    paths = [session.path / p for p in ("session.json", "events.jsonl", "sensors.csv")]
    paths += [session.image_path(events[i]) for i in selected] + hybrid_input_paths(session)

    def fingerprints():
        return {
            str(p.relative_to(session.path)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in paths
        }

    hashes = fingerprints()
    calibration_sha = (
        hashlib.sha256(calibration_path.read_bytes()).hexdigest() if calibration_path else None
    )
    output.mkdir(parents=True, exist_ok=False)
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
        "quality_status": "preview_temporal_tracks_with_dual_JPEG_image_checks_no_pose_yet",
        "hybrid": {
            "schema_version": 1,
            "settings": asdict(hybrid_config),
            "capture_timing": capture_timing_diagnostics(
                session, max_preview_gap_s=hybrid_config.max_preview_gap_s
            ),
            "preview_frames": [],
            "still_associations": [],
            "image_segments": {},
            "eligible_image_indices": [],
            "reserved_track_id": -1,
            "timing_s": {
                k: 0.0
                for k in (
                    "preview_loading",
                    "preview_feature_selection",
                    "preview_forward_lk",
                    "preview_backward_lk",
                    "preview_motion_initializer",
                    "still_loading",
                    "cross_stream_forward_lk",
                    "cross_stream_backward_lk",
                    "cross_stream_gyro",
                    "cross_stream_motion_initializer",
                )
            },
        },
    }
    if calibration_sha:
        report["calibration_sha256"] = calibration_sha
    hybrid = report["hybrid"]
    timings = hybrid["timing_s"]
    tracker, previous, segment, global_next = None, None, -1, 0
    still_position = 0
    lengths, photo_ages = Counter(), Counter()
    last_jpeg = None
    source_resets = [r.raw["next_frame_id"] for r in session.tracking.records if r.type == "reset"]

    def still(i, before, after, observations_file):
        nonlocal last_jpeg
        loading = perf_counter()
        raw = cv2.imread(
            str(session.image_path(events[i])), cv2.IMREAD_GRAYSCALE | cv2.IMREAD_IGNORE_ORIENTATION
        )
        if raw is None:
            raise ValueError(f"Cannot decode still {i}")
        image = cv2.resize(raw, cameras[i].image_size, interpolation=cv2.INTER_AREA)
        if any(cameras[i].distortion):
            image = cameras[i].undistort_image(image)
        loading_s = perf_counter() - loading
        timings["still_loading"] += loading_s
        measured, association = associate_still(
            session,
            events[i],
            originals[i],
            affines[i],
            raw,
            before,
            after,
            config=hybrid_config,
            tracking_config=config,
            max_gyro_gap_s=max_gyro_gap_s,
        )
        active = {}
        for t, row in sorted(measured.items()):
            photo_ages[t] += 1
            lengths[t] += 1
            active[t] = {
                "image_id": i,
                "track_id": t,
                "pixel": row["pixel"].tolist(),
                "age": photo_ages[t],
                "response": row["response"],
                "status": "tracked" if photo_ages[t] > 1 else "seeded",
                "lk_error": row["lk_error"],
                "forward_backward_error_px": row["forward_backward_error_px"],
            }
            observations_file.write(json.dumps(active[t], allow_nan=False) + "\n")
        hybrid["still_associations"].append(association)
        supported = association["status"] == "verified_2d"
        hybrid["image_segments"][str(i)] = association["segment_id"] if supported else None
        if supported:
            hybrid["eligible_image_indices"].append(i)
        for branch in association["branches"]:
            timings["cross_stream_forward_lk"] += branch["forward_lk_runtime_s"]
            timings["cross_stream_backward_lk"] += branch["backward_lk_runtime_s"]
            timings["cross_stream_gyro"] += branch["gyro_runtime_s"]
            timings["cross_stream_motion_initializer"] += (
                branch["motion_initialization"] or {}
            ).get("runtime_s", 0)
        pixels = np.array([r["pixel"] for r in active.values()], float).reshape(-1, 2)
        occupancy = grid_occupancy(pixels, image.shape, config)
        name = f"image_{i:03d}_hybrid_tracks.jpg"
        _write_image(
            output / name,
            draw_tracking_seeds(
                image,
                pixels,
                grid_rows=config.grid_rows,
                grid_cols=config.grid_cols,
                title=f"JPEG {i}: {len(active)} dual-preview image checks; {association['reason']}",
            ),
        )
        report["images"].append(
            {
                "index": i,
                "filename": events[i].filename,
                "timestamp_ns": events[i].timestamp_ns,
                "timestamp_source": events[i].timestamp_source,
                "stored_intrinsics": originals[i].to_dict(),
                "working_intrinsics": cameras[i].without_distortion().to_dict(),
                "stored_ideal_to_working_ideal_affine": affines[i].tolist(),
                "active_count": len(active),
                "occupied_grid_cells": int((occupancy > 0).sum()),
                "grid_occupancy": occupancy.tolist(),
                "new_point_count": sum(r["status"] == "seeded" for r in active.values()),
                "detected_cells": [],
                "selection_runtime_s": 0.0,
                "loading_runtime_s": loading_s,
                "tracking_runtime_s": association["runtime_s"],
                "debug_image": name,
            }
        )
        if last_jpeg is not None:
            old_i, old_active, old_image = last_jpeg
            ids = sorted(old_active)
            n = len(ids)
            accepted = np.array([t in active for t in ids], bool)
            source = np.array([old_active[t]["pixel"] for t in ids], float).reshape(-1, 2)
            target = np.array(
                [active[t]["pixel"] if t in active else [np.nan, np.nan] for t in ids], float
            ).reshape(-1, 2)
            fb = np.array(
                [active[t]["forward_backward_error_px"] if t in active else np.nan for t in ids]
            )
            error = np.array([active[t]["lk_error"] if t in active else np.nan for t in ids])
            stem = f"pair_{old_i:03d}_{i:03d}"
            np.savez_compressed(
                output / (stem + "_flow.npz"),
                track_ids=np.array(ids, np.int64),
                source_pixels=source,
                target_pixels=target,
                accepted=accepted,
                forward_status=accepted,
                backward_status=accepted,
                lk_error=error,
                forward_backward_error_px=fb,
            )
            _write_image(
                output / (stem + "_flow.jpg"),
                draw_match_diagnostic(
                    cv2.cvtColor(old_image, cv2.COLOR_GRAY2BGR),
                    cv2.cvtColor(image, cv2.COLOR_GRAY2BGR),
                    source,
                    target,
                    accepted,
                    title=f"JPEG {old_i}–{i}: preview-carried, JPEG-verified tracks",
                    accepted_label="ACCEPTED by preview continuity and dual JPEG image checks",
                    rejected_label="REJECTED: no shared verified JPEG observation",
                ),
            )
            report["pairs"].append(
                {
                    "image_indices": [old_i, i],
                    "input_count": n,
                    "accepted_count": int(accepted.sum()),
                    "rejected_count": int((~accepted).sum()),
                    "rejection_counts": {
                        "no_verified_current_JPEG_observation": int((~accepted).sum())
                    }
                    if (~accepted).any()
                    else {},
                    "median_forward_backward_error_px": _median(fb),
                    "median_accepted_forward_backward_error_px": _median(fb[accepted]),
                    "flow_runtime_s": sum(b["flow_runtime_s"] for b in association["branches"]),
                    "forward_lk_runtime_s": sum(
                        b["forward_lk_runtime_s"] for b in association["branches"]
                    ),
                    "backward_lk_runtime_s": sum(
                        b["backward_lk_runtime_s"] for b in association["branches"]
                    ),
                    "motion_initialization": None,
                    "arrays_file": stem + "_flow.npz",
                    "debug_image": stem + "_flow.jpg",
                }
            )
        last_jpeg = (i, active, image)
        print(
            f"Hybrid JPEG {i}: {association['status']}; "
            f"{len(active)}/{association['candidate_track_count']} image-verified tracks; "
            f"segment={association['segment_id']}; reason={association['reason']}",
            flush=True,
        )

    def consume_stills(until, before, after, output_file):
        nonlocal still_position
        while (
            still_position < len(selected)
            and events[selected[still_position]].timestamp_ns <= until
        ):
            still(selected[still_position], before, after, output_file)
            still_position += 1

    with (output / "observations.jsonl").open("w", encoding="utf-8") as observation_file:
        for frame in session.tracking.frames:
            reason = (
                "camera_clock_unknown"
                if frame.clock_domain != CLOCK_DOMAIN
                else "intrinsics_unavailable"
                if frame.intrinsics is None
                else None
            )
            if reason is not None:
                hybrid["preview_frames"].append(
                    {"frame_id": frame.frame_id, "supported": False, "reason": reason}
                )
                if frame.clock_domain == CLOCK_DOMAIN:
                    consume_stills(frame.timestamp_ns, previous, None, observation_file)
                previous, tracker = None, None
                continue
            loading = perf_counter()
            gray = cv2.imread(str(capture_path(session.path, frame.filename)), cv2.IMREAD_UNCHANGED)
            if gray is None:
                raise ValueError(f"Cannot decode preview {frame.frame_id}")
            camera = frame.intrinsics
            if any(camera.distortion):
                gray = camera.undistort_image(gray)
            timings["preview_loading"] += perf_counter() - loading
            signature = _geometry_key(frame)
            old_signature = getattr(previous, "signature", None)
            gap = (frame.timestamp_ns - previous.frame.timestamp_ns) / 1e9 if previous else None
            reset_reason = (
                "initial_or_unsupported_previous_frame"
                if previous is None
                else "preview_geometry_changed"
                if signature != old_signature
                else "preview_gap_exceeds_limit"
                if gap > hybrid_config.max_preview_gap_s
                else "recorded_reset"
                if any(previous.frame.frame_id < r <= frame.frame_id for r in source_resets)
                else None
            )
            reset = reset_reason is not None
            if reset:
                tracker = TemporalTracker(config)
                tracker.next_track_id = global_next
                segment += 1
            step = tracker.process(gray, frame.frame_id)
            global_next = tracker.next_track_id
            if step.flow is not None:
                timings["preview_forward_lk"] += step.flow.forward_runtime_s
                timings["preview_backward_lk"] += step.flow.backward_runtime_s
                timings["preview_motion_initializer"] += (
                    step.flow.motion_initialization or {}
                ).get("runtime_s", 0)
                if not step.flow.accepted.any():
                    segment += 1
                    reset_reason = "all_preview_tracks_lost"
            if step.selection:
                timings["preview_feature_selection"] += step.selection.runtime_s
            current = PreviewSnapshot(
                frame,
                camera.without_distortion(),
                gray,
                {o.track_id: o for o in tracker.active},
                segment,
                signature,
            )
            hybrid["preview_frames"].append(
                {
                    "frame_id": frame.frame_id,
                    "timestamp_ns": frame.timestamp_ns,
                    "supported": True,
                    "segment_id": segment,
                    "gap_s": gap,
                    "reset": reset_reason is not None,
                    "reset_reason": reset_reason,
                    "active_count": len(tracker.active),
                    "surviving_count": int(step.flow.accepted.sum()) if step.flow else 0,
                    "median_fb_error_px": _median(
                        step.flow.forward_backward_error_px[step.flow.accepted]
                    )
                    if step.flow
                    else None,
                }
            )
            consume_stills(frame.timestamp_ns, previous, current, observation_file)
            previous = current
        consume_stills(float("inf"), previous, None, observation_file)
    hybrid["reserved_track_id"] = global_next - 1
    hybrid["preview_segment_count"] = segment + 1
    if fingerprints() != hashes or (
        calibration_path
        and hashlib.sha256(calibration_path.read_bytes()).hexdigest() != calibration_sha
    ):
        raise ValueError("Hybrid raw inputs/calibration changed during replay")
    report["summary"] = {
        "image_count": len(selected),
        "track_count": len(lengths),
        "persistent_track_count": sum(v >= 2 for v in lengths.values()),
        "tracks_with_three_observations": sum(v >= 3 for v in lengths.values()),
        "tracks_spanning_sequence": sum(v == len(selected) for v in lengths.values()),
        "track_length_histogram": dict(Counter(str(v) for v in lengths.values())),
        "tracking_runtime_s": sum(timings.values()),
        "total_runtime_s": perf_counter() - start,
    }
    report["success"] = report["summary"]["persistent_track_count"] > 0
    (output / "tracking.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    return report
