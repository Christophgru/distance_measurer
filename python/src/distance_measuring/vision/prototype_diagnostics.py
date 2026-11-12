"""Compact P8 diagnostics from existing stage reports; no reconstruction logic."""

import numpy as np

from distance_measuring.optimization.bundle import reprojection_residuals


def statistics(values):
    values = np.asarray(values, float)
    values = values[np.isfinite(values)]
    return {
        "count": len(values),
        **{
            name: float(operation(values)) if len(values) else None
            for name, operation in (
                ("min", np.min),
                ("median", np.median),
                ("p95", lambda v: np.percentile(v, 95)),
                ("max", np.max),
            )
        },
    }


def _camera_work(registration=None, growth=None, refinement=None):
    """Collect each matching operation once, including failed fallback attempts."""
    registration, refinement = registration or {}, refinement or {}
    growths = [growth] if growth else []
    local = [registration["local_search"]] if registration.get("local_search") else []
    prior = registration.get("two_view_prior") or {}
    if prior.get("local_search"):
        local.append(prior["local_search"])
    rounds = refinement.get("rounds", [])
    for row in rounds:
        if row.get("growth"):
            growths.append(row["growth"])
        retry = row.get("observation_retry") or {}
        if retry.get("local_search"):
            local.append(retry["local_search"])
    epipolar = [g["epipolar_search"] for g in growths]
    return {
        "pnp_attempts": registration.get("pnp_attempts", []),
        "pnp_runtime_s": registration.get("pnp_runtime_s", 0),
        "local_patch_search_runtime_s": sum(r.get("runtime_s", 0) for r in local),
        "epipolar_search_runtime_s": sum(r.get("runtime_s", 0) for r in epipolar),
        "track_merging_runtime_s": sum(g.get("track_merging_runtime_s", 0) for g in growths),
        "triangulation_runtime_s": sum(g.get("triangulation_runtime_s", 0) for g in growths),
        "local_ba_runtime_s": sum(r["bundle_adjustment"]["runtime_s"] for r in rounds),
        "two_view_pose_runtime_s": (prior.get("relative_pose") or {}).get("runtime_s", 0),
        "descriptor_candidates_examined": sum(
            r.get("candidates_examined", 0) + r.get("reverse_candidates_examined", 0)
            for r in epipolar
        ),
        "patch_centers_examined": sum(
            r.get("candidates_examined", 0) + r.get("reverse_candidates_examined", 0) for r in local
        ),
        "refinement_rounds": [
            {
                key: row[key]
                for key in (
                    "round",
                    "bundle_adjustment",
                    "search_radius_before_px",
                    "search_radius_px",
                )
            }
            for row in rounds
        ],
        "refinement_stop_reason": refinement.get("stop_reason"),
    }


def collect_diagnostics(tracking, parallax, bootstrap, incremental, scene):
    incremental = incremental or {}
    registrations = {r["image_id"]: r for r in incremental.get("images", [])}
    registered = {c.image_id for c in scene.cameras} if scene else set()
    seed = bootstrap.get("bootstrap_pair") or []
    cameras = []
    for image_id in tracking["selected_image_indices"]:
        registration = registrations.get(image_id, {})
        is_seed_target = bool(seed) and image_id == seed[-1]
        work = _camera_work(
            registration,
            incremental.get("seed_growth") if is_seed_target else registration.get("growth"),
            incremental.get("seed_refinement")
            if is_seed_target
            else registration.get("refinement"),
        )
        cameras.append(
            {
                "image_id": image_id,
                "registered": image_id in registered,
                "status": "bootstrap"
                if image_id in seed
                else (
                    "registered"
                    if image_id in registered
                    else ("registration_failed" if registration else "not_attempted")
                ),
                "final_pnp": registration.get("pnp"),
                "confidence_before_registration": {
                    k: registration.get("confidence", {}).get(k)
                    for k in ("trusted_point_count", "excluded_point_count")
                },
                **work,
            }
        )
    candidates = bootstrap.get("candidates", [])
    components = {
        "feature_selection": sum(r["selection_runtime_s"] for r in tracking["images"]),
        "optical_flow": sum(
            r["forward_lk_runtime_s"] + r["backward_lk_runtime_s"] for r in tracking["pairs"]
        ),
        "bounded_motion_initializer": sum(
            (r.get("motion_initialization") or {}).get("runtime_s", 0) for r in tracking["pairs"]
        ),
        "gyro_integration": sum(
            (r.get("gyro") or {}).get("runtime_s", 0) for r in parallax["pairs"]
        ),
        "parallax_ranking": sum(r.get("geometry_ranking_runtime_s", 0) for r in parallax["pairs"]),
        "bootstrap_selection": sum(
            (r.get("selection") or {}).get("selection_runtime_s", 0) for r in candidates
        ),
        "bootstrap_ransac": sum(
            (r.get("pose") or {}).get("essential_runtime_s", 0) for r in candidates
        ),
        "bootstrap_pose_recovery": sum(
            (r.get("pose") or {}).get("recovery_runtime_s", 0) for r in candidates
        ),
        "bootstrap_triangulation": sum(
            (r.get("landmarks") or {}).get("runtime_s", 0) for r in candidates
        ),
        "incremental_feature_extraction": sum(
            r["runtime_s"] for r in incremental.get("feature_extraction", {}).values()
        ),
    }
    for key in (
        "pnp",
        "local_patch_search",
        "epipolar_search",
        "track_merging",
        "triangulation",
        "local_ba",
        "two_view_pose",
    ):
        components[key] = sum(c[key + "_runtime_s"] for c in cameras)
    if "hybrid" in tracking:
        timing = tracking["hybrid"]["timing_s"]
        components["feature_selection"] = timing["preview_feature_selection"]
        components["optical_flow"] = sum(
            timing[k]
            for k in (
                "preview_forward_lk",
                "preview_backward_lk",
                "cross_stream_forward_lk",
                "cross_stream_backward_lk",
            )
        )
        components["bounded_motion_initializer"] = timing[
            "preview_motion_initializer"
        ] + timing.get("cross_stream_motion_initializer", 0)
        components["hybrid_gyro_search_priors"] = timing["cross_stream_gyro"]
    errors = np.linalg.norm(reprojection_residuals(scene).reshape(-1, 2), axis=1) if scene else []
    quality = {
        "camera_count": len(registered),
        "registered_image_indices": sorted(registered),
        "unregistered_image_indices": [
            i for i in tracking["selected_image_indices"] if i not in registered
        ],
        "point_count": len(scene.points) if scene else 0,
        "observation_count": len(errors),
        "reprojection_rmse_px": float(np.sqrt(np.mean(np.square(errors)))) if len(errors) else None,
        "reprojection_error_px": statistics(errors),
        "best_triangulation_angle_degrees": statistics(
            [p.triangulation_angle_degrees for p in scene.points] if scene else []
        ),
        "trusted_point_count": scene.metadata.get("landmark_confidence", {}).get(
            "trusted_point_count"
        )
        if scene
        else None,
        "excluded_point_count": scene.metadata.get("landmark_confidence", {}).get(
            "excluded_point_count"
        )
        if scene
        else None,
        "scale_status": scene.metadata.get("scale_status") if scene else None,
        "gauge": scene.metadata.get("gauge") if scene else None,
    }
    pairs = []
    for flow, rotation in zip(tracking["pairs"], parallax["pairs"], strict=True):
        gyro = rotation.get("gyro") or {}
        pairs.append(
            {
                "image_indices": flow["image_indices"],
                "input_track_count": flow["input_count"],
                "accepted_track_count": flow["accepted_count"],
                "median_accepted_forward_backward_error_px": flow[
                    "median_accepted_forward_backward_error_px"
                ],
                "gyro_available": rotation["available"],
                "gyro_reason": rotation.get("reason"),
                "gyro_sample_count": gyro.get("sample_count"),
                "gyro_max_sample_gap_s": gyro.get("max_sample_gap_s"),
                "median_parallax_px": rotation.get("median_parallax_px"),
                "selected_count": rotation.get("selected_count", 0),
            }
        )
    return {
        "component_runtime_s": components,
        "quality": quality,
        "tracking": tracking["summary"],
        "active_tracks": [
            {"image_id": r["index"], "count": r["active_count"]} for r in tracking["images"]
        ],
        "pairs": pairs,
        "bootstrap_candidates": [
            {
                "image_indices": r["image_indices"],
                "accepted": r["accepted"],
                "reasons": r["reasons"],
                "pose": r.get("pose"),
                "point_count": (r.get("landmarks") or {}).get("point_count", 0),
            }
            for r in candidates
        ],
        "cameras": cameras,
        "matching_cost": {
            "motion_initializer_descriptor_distance_upper_bound": sum(
                (r.get("motion_initialization") or {}).get("descriptor_distance_upper_bound", 0)
                for r in tracking["pairs"]
            ),
            "guided_descriptor_candidates_examined": sum(
                c["descriptor_candidates_examined"] for c in cameras
            ),
            "local_patch_centers_examined": sum(c["patch_centers_examined"] for c in cameras),
            "note": "Initializer uses bounded global descriptors. Its bidirectional distance "
            "count is an upper bound; guided counts include reverse checks. "
            "Patch centers are a separate unit.",
        },
    }


def render_report(report):
    q = report["diagnostics"]["quality"]
    lines = [
        "# Incremental reconstruction",
        "",
        f"Status: **{report['status']}**. "
        f"{report['failure_reason'] or 'All selected cameras registered.'}",
        f"Session: `{report['session_path']}`; selected IDs: {report['selected_image_indices']}.",
        f"Recovered {q['camera_count']}/{len(report['selected_image_indices'])} cameras; "
        f"{q['point_count']} landmarks; {q['observation_count']} observations; "
        f"RMSE {q['reprojection_rmse_px']} px.",
        f"Missing cameras: {q['unregistered_image_indices']}. "
        f"Raw inputs unchanged: {report['raw_inputs_unchanged']}.",
        "",
        "## Timings",
        "",
        "| Stage | Seconds |",
        "|---|---:|",
    ]
    lines += [f"| {k} | {v:.4f} |" for k, v in report["stage_runtime_s"].items()]
    lines += [
        "",
        "Algorithm components below are disjoint measured operations inside the stages. "
        "Validation, hashing, decoding, geometric bookkeeping, visualization and report I/O "
        "account for the remaining time.",
        "",
        "| Component | Seconds |",
        "|---|---:|",
    ]
    lines += [f"| {k} | {v:.4f} |" for k, v in report["diagnostics"]["component_runtime_s"].items()]
    lines += [
        "",
        "## Tracks and rotation",
        "",
        "| Pair | Accepted / input | Median accepted FB error (px) | Gyro samples | "
        "Median parallax (px) | Selected |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    lines += [
        f"| {r['image_indices']} | {r['accepted_track_count']}/{r['input_track_count']} | "
        f"{r['median_accepted_forward_backward_error_px']} | {r['gyro_sample_count']} | "
        f"{r['median_parallax_px']} | {r['selected_count']} |"
        for r in report["diagnostics"]["pairs"]
    ]
    lines += [
        "",
        "Active tracks by image: " + str(report["diagnostics"]["active_tracks"]),
        "",
        "## Bootstrap",
        "",
    ]
    for r in report["diagnostics"]["bootstrap_candidates"]:
        pose = r["pose"] or {}
        lines.append(
            f"- {r['image_indices']}: accepted={r['accepted']}; "
            f"RANSAC={pose.get('ransac_inlier_count')}/{pose.get('input_track_count')}; "
            f"pose inliers={pose.get('pose_inlier_count')}; "
            f"landmarks={r['point_count']}; reasons={r['reasons']}"
        )
    lines += [
        "",
        "## Camera registration and refinement",
        "",
        "| Camera | Status | PnP inliers / input | PnP (s) | Guided search (s) | BA (s) | Stop |",
        "|---|---|---:|---:|---:|---:|---|",
    ]
    for r in report["diagnostics"]["cameras"]:
        pnp = r["final_pnp"] or {}
        lines.append(
            f"| {r['image_id']} | {r['status']} | "
            f"{pnp.get('inlier_count')}/{pnp.get('input_count')} | {r['pnp_runtime_s']:.4f} | "
            f"{r['local_patch_search_runtime_s'] + r['epipolar_search_runtime_s']:.4f} | "
            f"{r['local_ba_runtime_s']:.4f} | {r['refinement_stop_reason']} |"
        )
    lines += [
        "",
        "## Geometry and matching cost",
        "",
        f"- Reprojection error (px): {q['reprojection_error_px']}",
        f"- Best triangulation angle per landmark (degrees): "
        f"{q['best_triangulation_angle_degrees']}",
        f"- Confidence: {q['trusted_point_count']} trusted, "
        f"{q['excluded_point_count']} excluded from pose estimation.",
        f"- Scale: `{q['scale_status']}`. Gauge: `{q['gauge']}`.",
        "",
    ]
    lines += [f"- {k}: {v}" for k, v in report["diagnostics"]["matching_cost"].items()]
    lines += [
        "",
        "## Artifacts and assumptions",
        "",
        "- [Scene](scene.json), present after successful bootstrap and verified input hashes.",
        "- Stage artifacts: `tracking/`, `parallax/`, `bootstrap/`, `incremental/` "
        "(scene preview in the latter).",
        "- [Machine-readable report](reconstruction.json) includes settings, source hashes, "
        "all PnP attempts and BA decisions.",
        "- Timing starts inside the pipeline after imports and ends before final report "
        "serialization; it includes stage diagnostics and raw hash verification.",
        "- A low residual on accepted observations alone does not establish pose accuracy; "
        "scale is arbitrary and no metric ground truth is available.",
        "",
    ]
    lines += [f"- {w}" for w in report["warnings"]]
    if "hybrid" in report:
        h = report["hybrid"]
        lines += [
            "",
            "## Hybrid preview replay",
            "",
            f"- Preview frames: {len(h['preview_frames'])}; "
            f"segments: {h['preview_segment_count']}.",
            f"- Image-verified still associations: {len(h['eligible_image_indices'])}/"
            f"{len(report['selected_image_indices'])}.",
            "- Still pixels are measured independently from both bracketing previews. "
            "Resizing is used only to express measured JPEG coordinates in working JPEG pixels.",
            "- Preview resets, unsupported metadata and gaps are preserved. "
            "Poses are never assigned from the nearest preview or "
            "across an unbootstrapped segment.",
            "",
            "| Still | Preview bracket | Segment | Verified 2D tracks | Status / reason |",
            "|---|---|---:|---:|---|",
        ]
        lines += [
            f"| {r['image_id']} | {r.get('bracket_frame_ids')} | {r['segment_id']} | "
            f"{r['accepted_count']} | {r['status']} / {r['reason']} |"
            for r in h["still_associations"]
        ]
        lines += ["", "Recording diagnostics: " + str(h["recording"]), ""]
        if "capture_timing" in h:
            timing = h["capture_timing"]
            lines += [
                "- JPEG exposure intervals (s): " + str(timing["exposure_intervals_s"]),
                "- Request-to-exposure (s): " + str(timing["request_to_exposure_s"]),
                "- Request-to-callback (s): " + str(timing["request_to_callback_s"]),
                "- Preview gaps overlapping JPEG requests (s): "
                + str(timing["preview_gaps_during_jpeg_requests_s"]),
                "- Preview gaps outside JPEG requests (s): "
                + str(timing["preview_gaps_outside_jpeg_requests_s"]),
                "- Capture skip reasons: " + str(timing["capture_skip_reasons"]),
                "- " + timing["interpretation"],
                "",
            ]
    return "\n".join(lines) + "\n"
