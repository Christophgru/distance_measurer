"""Bounded local BA / guided correspondence rounds for registered cameras."""

from dataclasses import asdict, dataclass, replace
from time import perf_counter

import numpy as np

from distance_measuring.optimization.local_bundle import DEFAULT_LOCAL_BUNDLE, local_bundle_adjust
from distance_measuring.scene import Observation

from .guided import DEFAULT_GUIDED, project_landmarks, search_landmark_patches
from .incremental import grow_landmarks, with_landmark_quality
from .landmark_confidence import DEFAULT_CONFIDENCE, score_landmarks


@dataclass(frozen=True)
class RefinementConfig:
    max_rounds: int = 2
    min_search_radius_px: int = 4
    radius_shrink_factor: float = 0.5
    tighten_rmse_px: float = 1.0
    min_rmse_improvement_px: float = 0.001

    def __post_init__(self):
        if type(self.max_rounds) is not int or not 1 <= self.max_rounds <= 2:
            raise ValueError("The prototype permits one or two refinement rounds")
        if type(self.min_search_radius_px) is not int or self.min_search_radius_px < 1:
            raise ValueError("Minimum search radius must be a positive integer")
        if not np.isfinite(self.radius_shrink_factor) or not 0 < self.radius_shrink_factor < 1:
            raise ValueError("Radius shrink factor must be in (0, 1)")
        if not np.isfinite(self.tighten_rmse_px) or self.tighten_rmse_px <= 0:
            raise ValueError("Tightening RMSE must be positive and finite")
        if not np.isfinite(self.min_rmse_improvement_px) or self.min_rmse_improvement_px < 0:
            raise ValueError("Meaningful RMSE improvement must be nonnegative and finite")


DEFAULT_REFINEMENT = RefinementConfig()


def retry_unmatched_observations(
    scene, target_id, images, *, reserved_feature_id=-1, config=DEFAULT_GUIDED
):
    """Confirm missing landmark observations against an already registered pose."""
    start = perf_counter()
    cameras = {c.image_id: c for c in scene.cameras}
    target = cameras[target_id]
    unmatched = {
        t.track_id
        for t in scene.tracks
        if t.point_id is not None and target_id not in {o.image_id for o in t.observations}
    }
    projections = [
        r
        for r in project_landmarks(scene, target.intrinsics, target.extrinsics, config=config)
        if r["track_id"] in unmatched
    ]
    searches, matching = search_landmark_patches(
        scene, images, images[target_id], projections, config=config
    )
    occupied = [
        np.asarray(o.pixel) for t in scene.tracks for o in t.observations if o.image_id == target_id
    ]
    feature_id = (
        max([reserved_feature_id, *(o.feature_id for t in scene.tracks for o in t.observations)])
        + 1
    )
    additions = {}
    for row in searches:
        if not row["accepted"]:
            continue
        pixel = np.asarray(row["second_pixel_refined"])
        error = float(np.linalg.norm(pixel - row["predicted_pixel"]))
        row["reprojection_error_px"] = error
        if error > config.max_reprojection_error_px:
            row.update(accepted=False, reason="retry_high_reprojection_error")
        elif any(np.linalg.norm(pixel - p) <= config.merge_radius_px for p in occupied):
            row.update(accepted=False, reason="retry_target_feature_already_used")
        else:
            additions[row["track_id"]] = Observation(target_id, feature_id, tuple(pixel), error)
            feature_id += 1
            occupied.append(pixel)
    tracks = tuple(
        replace(t, observations=t.observations + (additions[t.track_id],))
        if t.track_id in additions
        else t
        for t in scene.tracks
    )
    result = with_landmark_quality(replace(scene, tracks=tracks))
    return result, {
        "target_image_id": target_id,
        "query_count": len(projections),
        "added_observation_count": len(additions),
        "projections": projections,
        "patch_matches": searches,
        "local_search": matching,
        "runtime_s": perf_counter() - start,
    }


def refine_registered_camera(
    scene,
    source_id,
    target_id,
    images,
    first_features,
    second_features,
    *,
    reserved_track_id=-1,
    config=DEFAULT_REFINEMENT,
    guided_config=DEFAULT_GUIDED,
    bundle_config=DEFAULT_LOCAL_BUNDLE,
    confidence_config=DEFAULT_CONFIDENCE,
):
    start = perf_counter()
    radius, rounds = guided_config.search_radius_px, []
    stop = "round_limit"
    initial_points = len(scene.points)
    initial_observations = sum(len(t.observations) for t in scene.tracks if t.point_id is not None)
    for index in range(config.max_rounds):
        confidence = score_landmarks(scene, config=confidence_config)
        result = local_bundle_adjust(
            scene, target_id, trusted_point_ids=confidence.trusted_point_ids, config=bundle_config
        )
        record = {
            "round": index + 1,
            "bundle_adjustment": result.report,
            "confidence": confidence.report,
            "search_radius_before_px": radius,
            "search_radius_px": radius,
            "radius_reduced": False,
            "observation_retry": None,
            "growth": None,
        }
        rounds.append(record)
        if not result.report["accepted"]:
            stop = "no_accepted_local_refinement"
            break
        scene = result.scene
        if result.report["after_rmse_px"] <= config.tighten_rmse_px:
            floor = min(radius, config.min_search_radius_px)
            smaller = max(floor, int(np.ceil(radius * config.radius_shrink_factor)))
            record.update(search_radius_px=smaller, radius_reduced=smaller < radius)
            radius = smaller
        search_config = replace(guided_config, search_radius_px=radius)
        scene, retry = retry_unmatched_observations(
            scene,
            target_id,
            images,
            reserved_feature_id=reserved_track_id,
            config=search_config,
        )
        scene, growth = grow_landmarks(
            scene,
            source_id,
            target_id,
            first_features,
            second_features,
            reserved_track_id=reserved_track_id,
            config=search_config,
        )
        record.update(observation_retry=retry, growth=growth)
        improvement = result.report["before_rmse_px"] - result.report["after_rmse_px"]
        record["rmse_improvement_px"] = improvement
        if retry["added_observation_count"] == 0 and growth["new_landmark_count"] == 0:
            stop = (
                "no_meaningful_improvement"
                if improvement < config.min_rmse_improvement_px
                else "no_new_guided_correspondences"
            )
            break
    report = {
        "target_image_id": target_id,
        "source_image_id": source_id,
        "settings": asdict(config),
        "local_bundle_settings": asdict(bundle_config),
        "confidence_settings": asdict(confidence_config),
        "rounds": rounds,
        "round_count": len(rounds),
        "stop_reason": stop,
        "added_landmark_count": len(scene.points) - initial_points,
        "added_observation_count": sum(
            len(t.observations) for t in scene.tracks if t.point_id is not None
        )
        - initial_observations,
        "bundle_adjustment_runtime_s": sum(r["bundle_adjustment"]["runtime_s"] for r in rounds),
        "matching_runtime_s": sum(
            r["observation_retry"]["runtime_s"] + r["growth"]["runtime_s"]
            for r in rounds
            if r["observation_retry"] is not None
        ),
        "runtime_s": perf_counter() - start,
    }
    target = next(c for c in scene.cameras if c.image_id == target_id)
    report["final_pose"] = {
        "R_cw": target.extrinsics.R_cw.tolist(),
        "C_w": target.extrinsics.C_w.tolist(),
    }
    scene = with_landmark_quality(scene)
    scene = replace(
        scene,
        metadata={
            **scene.metadata,
            "landmark_confidence": score_landmarks(scene, config=confidence_config).report,
        },
    )
    return scene, report
