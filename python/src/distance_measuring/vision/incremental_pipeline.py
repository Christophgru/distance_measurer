"""Extend a tracked bootstrap using temporal PnP and bounded image searches."""

import hashlib
import json
from dataclasses import asdict, replace
from pathlib import Path
from time import perf_counter

import cv2
import numpy as np

from distance_measuring.dataset import load_session, validate_session
from distance_measuring.optimization.local_bundle import DEFAULT_LOCAL_BUNDLE, scene_rmse
from distance_measuring.scene import load_scene, save_scene
from distance_measuring.scene.model import COORDINATE_FRAME, intrinsics_from_dict
from distance_measuring.visualization.guided import draw_corridors, draw_projections
from distance_measuring.visualization.scene_view import build_scene_view
from distance_measuring.visualization.viewer import save_preview

from .bootstrap import BootstrapConfig, BootstrapPair
from .features import SiftBackend
from .guided import DEFAULT_GUIDED, predict_pose
from .incremental import grow_landmarks, register_camera, with_landmark_quality
from .landmark_confidence import DEFAULT_CONFIDENCE, score_landmarks
from .pipeline import _write_image, working_camera
from .pnp import DEFAULT_PNP
from .pose_pipeline import _artifact
from .refinement import refine_registered_camera


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def extend_tracked_scene(
    session_path,
    tracking_directory,
    bootstrap_directory,
    output,
    *,
    config=DEFAULT_GUIDED,
    pnp_config=DEFAULT_PNP,
    parallax_directory=None,
    seed_growth=True,
    refinement_config=None,
    local_bundle_config=DEFAULT_LOCAL_BUNDLE,
    confidence_config=DEFAULT_CONFIDENCE,
):
    start = perf_counter()
    session_path, root, bootstrap, output = [
        Path(p).resolve() for p in (session_path, tracking_directory, bootstrap_directory, output)
    ]
    parallax_root = Path(parallax_directory).resolve() if parallax_directory else None
    inputs = [session_path, root, bootstrap, *([parallax_root] if parallax_root else [])]
    raw_root = Path(__file__).resolve().parents[4] / "data/raw"
    if output.is_relative_to(raw_root.resolve()) or any(output.is_relative_to(p) for p in inputs):
        raise ValueError("Incremental output must be outside data/raw and all input directories")
    session = load_session(session_path, validate=False)
    validate_session(session).raise_for_errors()
    manifest_path, scene_path, bootstrap_path = (
        root / "tracking.json",
        bootstrap / "scene.json",
        bootstrap / "bootstrap.json",
    )
    tracking = json.loads(manifest_path.read_text())
    boot = json.loads(bootstrap_path.read_text())
    scene = load_scene(scene_path)
    if (
        tracking["schema_version"] != 1
        or tracking["coordinate_frame"] != COORDINATE_FRAME
        or boot["schema_version"] != 1
        or not boot["success"]
        or tracking["session_id"] != session.metadata.session_id
        or boot["session_id"] != tracking["session_id"]
        or scene.metadata.get("session_id") != tracking["session_id"]
        or len(scene.cameras) != 2
        or not scene.points
        or scene.metadata.get("feature_id_source") != "P2_temporal_track_id"
    ):
        raise ValueError(
            "P5 requires matching P2/P4 session exports and a nonempty two-camera bootstrap"
        )
    selected = tracking["selected_image_indices"]
    records = {r["index"]: r for r in tracking["images"]}
    events = {e.index: e for e in session.images}
    if (
        any(type(i) is not int or i < 0 for i in selected)
        or selected != sorted(set(selected))
        or set(records) != set(selected)
        or len(records) != len(tracking["images"])
        or any(
            i not in events or records[i]["timestamp_ns"] != events[i].timestamp_ns
            for i in selected
        )
    ):
        raise ValueError("P2 image IDs/timestamps must match ordered recorded frames")
    if boot["bootstrap_pair"] != [c.image_id for c in scene.cameras] or any(
        c.image_id not in records for c in scene.cameras
    ):
        raise ValueError("P4 camera IDs disagree with the bootstrap report or P2 images")
    cameras = {i: intrinsics_from_dict(records[i]["working_intrinsics"]) for i in selected}
    if any(any(c.distortion) for c in cameras.values()):
        raise ValueError("P2 working intrinsics must be distortion-free")
    for camera in scene.cameras:
        if camera.intrinsics.to_dict() != cameras[camera.image_id].to_dict():
            raise ValueError("P4 and P2 working intrinsics disagree")
    fingerprints = {p: _sha(p) for p in (manifest_path, scene_path, bootstrap_path)}
    required_raw = {"session.json", "events.jsonl", "sensors.csv"} | {
        events[i].filename for i in selected
    }
    if (
        not required_raw <= tracking["input_sha256"].keys()
        or boot["raw_input_sha256"] != tracking["input_sha256"]
    ):
        raise ValueError("P2/P4 raw provenance must agree and include all selected recording files")
    for name, expected in tracking["input_sha256"].items():
        path = _artifact(session_path, name)
        if _sha(path) != expected:
            raise ValueError(f"Recording changed since P2 tracking: {name}")
        fingerprints[path] = expected
    history_path = _artifact(root, tracking["observations_file"])
    fingerprints[history_path] = _sha(history_path)
    history = {}
    reserved_id = -1
    for line in history_path.read_text().splitlines():
        row = json.loads(line)
        track_id, image_id = row["track_id"], row["image_id"]
        if type(track_id) is not int or track_id < 0 or image_id not in cameras:
            raise ValueError("Invalid P2 observation identity")
        reserved_id = max(reserved_id, track_id)
        if row["status"] in ("seeded", "tracked"):
            pixel = np.asarray(row["pixel"], float)
            if (
                (image_id, track_id) in history
                or pixel.shape != (2,)
                or not np.isfinite(pixel).all()
                or (pixel < 0).any()
                or (pixel >= cameras[image_id].image_size).any()
            ):
                raise ValueError("Duplicate, invalid or out-of-bounds P2 observation")
            history[image_id, track_id] = pixel
    hybrid = tracking.get("hybrid")
    if hybrid is not None:
        reserved = hybrid["reserved_track_id"]
        if type(reserved) is not int or reserved < reserved_id:
            raise ValueError("Hybrid preview track namespace must include all JPEG track IDs")
        reserved_id = reserved
        seed_segment = hybrid["image_segments"].get(str(boot["bootstrap_pair"][0]))
        if seed_segment is None or any(
            hybrid["image_segments"].get(str(i)) != seed_segment for i in boot["bootstrap_pair"]
        ):
            raise ValueError("Hybrid bootstrap must belong to one supported preview segment")
    for track in scene.tracks:
        for observation in track.observations:
            pixel = history.get((observation.image_id, track.track_id))
            if (
                observation.feature_id != track.track_id
                or pixel is None
                or not np.array_equal(pixel, observation.pixel)
            ):
                raise ValueError("P4 observations disagree with P2 temporal history")
    gyro_pairs = {}
    motion_config = BootstrapConfig(**boot.get("settings", {}))
    if parallax_root:
        path = parallax_root / "parallax.json"
        parallax = json.loads(path.read_text())
        fingerprints[path] = _sha(path)
        if (
            parallax["schema_version"] != 1
            or parallax["coordinate_frame"] != COORDINATE_FRAME
            or parallax["session_id"] != tracking["session_id"]
            or parallax["tracking_manifest_sha256"] != fingerprints[manifest_path]
        ):
            raise ValueError("Optional gyro prior must reference this P2 export")
        gyro_pairs = {tuple(p["image_indices"]): p for p in parallax["pairs"] if p["available"]}
    output.mkdir(parents=True, exist_ok=False)
    images, gray, features, loading_times, feature_times = {}, {}, {}, {}, {}

    def image(i):
        if i not in images:
            loading_start = perf_counter()
            record = records[i]
            stored = intrinsics_from_dict(record["stored_intrinsics"])
            resized, affine = working_camera(stored, cameras[i].image_size)
            if not np.allclose(resized.K, cameras[i].K, rtol=0, atol=1e-10) or not np.array_equal(
                affine, record["stored_ideal_to_working_ideal_affine"]
            ):
                raise ValueError("P2 resize transform and working intrinsics disagree")
            value = cv2.imread(
                str(session.image_path(events[i])), cv2.IMREAD_COLOR | cv2.IMREAD_IGNORE_ORIENTATION
            )
            if value is None or value.shape[:2][::-1] != stored.image_size:
                raise ValueError(f"Cannot decode image {i} in the stored-pixel frame")
            value = cv2.resize(value, cameras[i].image_size, interpolation=cv2.INTER_AREA)
            images[i] = resized.undistort_image(value) if any(resized.distortion) else value
            gray[i] = cv2.cvtColor(images[i], cv2.COLOR_BGR2GRAY)
            loading_times[str(i)] = perf_counter() - loading_start
        return images[i]

    def feature(i):
        if i not in features:
            image(i)
            feature_start = perf_counter()
            features[i] = SiftBackend(max_features=config.max_features).extract(gray[i])
            feature_times[str(i)] = {
                "count": len(features[i]),
                "runtime_s": perf_counter() - feature_start,
            }
        return features[i]

    def grow(source_id, target_id):
        nonlocal scene
        scene, growth = grow_landmarks(
            scene,
            source_id,
            target_id,
            feature(source_id),
            feature(target_id),
            reserved_track_id=reserved_id,
            # P4 owns the seed's ranked support/budget. Grow temporal structure
            # only after an additional camera has passed PnP verification.
            temporal_pixels=history if target_id not in seed_ids else None,
            config=config,
        )
        filename = f"pair_{source_id:03d}_{target_id:03d}_corridors.jpg"
        _write_image(
            output / filename,
            draw_corridors(image(target_id), growth["epipolar_search"], config.corridor_px),
        )
        growth["debug_image"] = filename
        search = growth["epipolar_search"]
        print(
            f"Guided {source_id}-{target_id}: +{growth['new_landmark_count']} landmarks; "
            f"descriptor candidates/query={search['average_candidates_per_feature']:.2f}, "
            f"runtime={growth['runtime_s']:.3f} s",
            flush=True,
        )
        return growth

    def refine(source_id, target_id):
        nonlocal scene
        scene, refined = refine_registered_camera(
            scene,
            source_id,
            target_id,
            gray,
            feature(source_id),
            feature(target_id),
            reserved_track_id=reserved_id,
            config=refinement_config,
            guided_config=config,
            bundle_config=local_bundle_config,
            confidence_config=confidence_config,
        )
        for record in refined["rounds"]:
            stem = f"image_{target_id:03d}_round_{record['round']:02d}"
            retry, growth = record["observation_retry"], record["growth"]
            if retry is not None:
                filename = f"{stem}_projections.jpg"
                _write_image(
                    output / filename,
                    draw_projections(
                        images[target_id],
                        retry["projections"],
                        retry["patch_matches"],
                        title=f"Image {target_id}: after local BA, round {record['round']}",
                    ),
                )
                retry["debug_image"] = filename
                filename = f"{stem}_corridors.jpg"
                _write_image(
                    output / filename,
                    draw_corridors(
                        images[target_id], growth["epipolar_search"], config.corridor_px
                    ),
                )
                growth["debug_image"] = filename
            ba = record["bundle_adjustment"]
            print(
                f"Refine {target_id} round {record['round']}: BA accepted={ba['accepted']}, "
                f"RMSE={ba['before_rmse_px']}->{ba['after_rmse_px']}, "
                f"BA runtime={ba['runtime_s']:.3f} s, radius={record['search_radius_px']} px, "
                f"confidence excluded={record['confidence']['excluded_point_count']}, "
                f"+observations={retry['added_observation_count'] if retry else 0}, "
                f"+landmarks={growth['new_landmark_count'] if growth else 0}",
                flush=True,
            )
        print(f"Refine {target_id}: {refined['stop_reason']}", flush=True)
        return refined

    print(
        "Guided thresholds: "
        + json.dumps({**asdict(config), "pnp": asdict(pnp_config)}, sort_keys=True),
        flush=True,
    )
    if refinement_config is not None:
        print(
            "Refinement thresholds: "
            + json.dumps(
                {
                    "refinement": asdict(refinement_config),
                    "local_bundle": asdict(local_bundle_config),
                    "confidence": asdict(confidence_config),
                },
                sort_keys=True,
            ),
            flush=True,
        )
    initial_count = len(scene.points)
    seed_ids = sorted(c.image_id for c in scene.cameras)
    for i in seed_ids:
        image(i)
    report = {
        "schema_version": 1,
        "session_id": tracking["session_id"],
        "coordinate_frame": COORDINATE_FRAME,
        "settings": {
            **asdict(config),
            "pnp": asdict(pnp_config),
            "seed_growth": seed_growth,
            "gyro_prior_enabled": bool(parallax_root),
            "refinement": asdict(refinement_config) if refinement_config is not None else None,
            "local_bundle": asdict(local_bundle_config) if refinement_config is not None else None,
            "confidence": asdict(confidence_config) if refinement_config is not None else None,
        },
        "initial_camera_ids": seed_ids,
        "initial_point_count": initial_count,
        "images": [],
        "seed_growth": grow(*seed_ids) if seed_growth else None,
        "warnings": list(scene.metadata.get("warnings", [])),
    }
    report["seed_refinement"] = refine(*seed_ids) if refinement_config is not None else None
    for i in selected:
        if i <= seed_ids[-1]:
            continue
        if hybrid is not None and (
            i not in hybrid["eligible_image_indices"]
            or hybrid["image_segments"].get(str(i)) != seed_segment
        ):
            reason = (
                "hybrid_still_association_unavailable"
                if i not in hybrid["eligible_image_indices"]
                else "hybrid_preview_segment_not_bootstrapped"
            )
            report["images"].append(
                {
                    "image_id": i,
                    "success": False,
                    "failure_reason": reason,
                    "pnp": {
                        "success": False,
                        "reason": reason,
                        "input_count": 0,
                        "inlier_count": 0,
                        "runtime_s": 0,
                    },
                    "pnp_attempts": [],
                    "pnp_runtime_s": 0,
                    "runtime_s": 0,
                }
            )
            print(f"Image {i}: unsupported: {reason}", flush=True)
            continue
        registered = sorted(scene.cameras, key=lambda c: records[c.image_id]["timestamp_ns"])
        previous, latest = registered[-2:]
        a, b = previous.image_id, latest.image_id
        fraction = (records[i]["timestamp_ns"] - records[b]["timestamp_ns"]) / (
            records[b]["timestamp_ns"] - records[a]["timestamp_ns"]
        )
        prior = gyro_pairs.get((b, i))
        coarse, prediction = predict_pose(
            previous.extrinsics,
            latest.extrinsics,
            fraction,
            gyro_rotation=prior["R_second_first"] if prior else None,
            config=config,
        )
        image(i)
        temporal = {t: pixel for (k, t), pixel in history.items() if k == i}
        common = sorted(t for k, t in history if k == b and (i, t) in history)
        motion_pair = BootstrapPair(
            (b, i),
            np.asarray(common, int),
            np.asarray([history[b, t] for t in common]).reshape(-1, 2),
            np.asarray([history[i, t] for t in common]).reshape(-1, 2),
            cameras[b],
            cameras[i],
            # The pose-only retry uses visual cheirality/parallax gates; it
            # does not run bootstrap selection or invent a gyro residual.
            np.zeros(len(common)),
        )
        scene, registration = register_camera(
            scene,
            i,
            cameras[i],
            gray[i],
            gray,
            temporal,
            coarse,
            config=config,
            pnp_config=pnp_config,
            confidence_config=confidence_config if refinement_config is not None else None,
            motion_pair=motion_pair,
            motion_config=motion_config,
        )
        filename = f"image_{i:03d}_projections.jpg"
        _write_image(
            output / filename,
            draw_projections(
                images[i],
                registration["projections"],
                registration["patch_matches"],
                title=f"Image {i}: {registration['projection_pose_source']} (geometry unverified)",
            ),
        )
        registration.update(prediction=prediction, debug_image=filename)
        if (registration["two_view_prior"] or {}).get("available"):
            fallback = registration["two_view_prior"]
            filename = f"image_{i:03d}_two_view_prior.jpg"
            _write_image(
                output / filename,
                draw_projections(
                    images[i],
                    fallback["projections"],
                    fallback["patch_matches"],
                    title=f"Image {i}: tracked two-view prior (PnP verification required)",
                ),
            )
            fallback["debug_image"] = filename
        if registration["success"]:
            registration["growth"] = grow(b, i)
            if refinement_config is not None:
                registration["refinement"] = refine(b, i)
        report["images"].append(registration)
        search = registration["local_search"]
        print(
            f"Image {i}: registered={registration['success']}, "
            f"PnP={registration['pnp']['inlier_count']}/{registration['pnp']['input_count']}, "
            f"patch candidates/landmark={search['average_candidates_per_landmark']:.1f} "
            f"vs exhaustive={search['exhaustive_candidates_per_landmark']}, "
            f"runtime={registration['runtime_s']:.3f} s",
            flush=True,
        )
    scene = with_landmark_quality(scene)
    if refinement_config is not None:
        scene = replace(
            scene,
            metadata={
                **scene.metadata,
                "landmark_confidence": score_landmarks(scene, config=confidence_config).report,
            },
        )
    scene = replace(
        scene,
        metadata={
            **scene.metadata,
            "incremental_settings": report["settings"],
            "source_tracking_manifest_sha256": fingerprints[manifest_path],
        },
    )
    destination = save_scene(output / "scene.json", scene)
    preview = save_preview(scene, build_scene_view(scene), output / "incremental_preview.png")
    if any(_sha(p) != expected for p, expected in fingerprints.items()):
        raise ValueError("Incremental inputs changed during processing")
    registered_ids = {c.image_id for c in scene.cameras}
    refinements = [
        r
        for r in [report["seed_refinement"], *(i.get("refinement") for i in report["images"])]
        if r is not None
    ]
    registration_success = any(r["success"] for r in report["images"])
    refinement_success = any(
        r["bundle_adjustment"]["accepted"] for f in refinements for r in f["rounds"]
    )
    report.update(
        success=registration_success or refinement_success,
        registration_success=registration_success,
        refinement_success=refinement_success,
        bundle_adjustment_runtime_s=sum(r["bundle_adjustment_runtime_s"] for r in refinements),
        refinement_matching_runtime_s=sum(r["matching_runtime_s"] for r in refinements),
        output_scene=str(destination),
        preview_file=preview.name,
        summary={
            "camera_count": len(scene.cameras),
            "added_camera_count": len(scene.cameras) - 2,
            "point_count": len(scene.points),
            "added_landmark_count": len(scene.points) - initial_count,
            "all_selected_cameras_registered": all(i in registered_ids for i in selected),
            "reprojection_rmse_px": scene_rmse(scene),
        },
        unregistered_image_indices=[i for i in selected if i not in registered_ids],
        not_attempted_before_bootstrap=[i for i in selected if i < seed_ids[0]],
        image_loading_runtime_s=loading_times,
        feature_extraction=feature_times,
        input_files_sha256={str(p): sha for p, sha in fingerprints.items()},
        total_runtime_s=perf_counter() - start,
    )
    report["failure_reason"] = (
        None if report["success"] else "No post-bootstrap camera had enough verified 3D-2D support"
    )
    (output / "incremental.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    return report
