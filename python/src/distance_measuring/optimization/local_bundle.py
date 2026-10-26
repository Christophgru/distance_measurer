"""Local camera/landmark windows using the existing gauge-fixed SciPy solver."""

from dataclasses import asdict, dataclass, replace
from time import perf_counter

import numpy as np

from .bundle import LOSSES, BundleResult, bundle_adjust, reprojection_residuals


@dataclass(frozen=True)
class LocalBundleConfig:
    window_size: int = 3
    min_landmarks: int = 8
    max_nfev: int = 100
    loss: str = "soft_l1"
    f_scale_px: float = 1.0
    rmse_tolerance_px: float = 1e-9

    def __post_init__(self):
        for name in ("window_size", "min_landmarks", "max_nfev"):
            if type(getattr(self, name)) is not int or getattr(self, name) < 1:
                raise ValueError(f"{name} must be a positive integer")
        if self.min_landmarks < 6:
            raise ValueError("Local BA requires at least six selected landmarks")
        if self.loss not in LOSSES or not np.isfinite(self.f_scale_px) or self.f_scale_px <= 0:
            raise ValueError("Invalid local BA loss or scale")
        if not np.isfinite(self.rmse_tolerance_px) or self.rmse_tolerance_px < 0:
            raise ValueError("Local BA RMSE tolerance must be nonnegative and finite")


DEFAULT_LOCAL_BUNDLE = LocalBundleConfig()


def scene_rmse(scene):
    residuals = reprojection_residuals(scene).reshape(-1, 2)
    return float(np.sqrt(np.mean(np.sum(residuals**2, axis=1)))) if len(residuals) else None


def local_bundle_adjust(
    scene, newest_image_id, *, trusted_point_ids=None, config=DEFAULT_LOCAL_BUNDLE
):
    """Keep the original world gauge and reject global or selected RMSE increases."""
    start = perf_counter()
    ids = [c.image_id for c in scene.cameras]
    if newest_image_id not in ids:
        raise ValueError("Local BA needs a registered target camera")
    newest = ids.index(newest_image_id)
    window = ids[max(0, newest + 1 - config.window_size) : newest + 1]
    variables = [i for i in window if i != ids[0]]
    known_points = {p.point_id for p in scene.points}
    trusted = known_points if trusted_point_ids is None else set(trusted_point_ids)
    if not trusted <= known_points:
        raise ValueError("Unknown trusted landmark IDs")
    local_points = [
        t.point_id
        for t in scene.tracks
        if t.point_id in trusted and any(o.image_id in window for o in t.observations)
    ]
    support = {
        i: sum(
            t.point_id in trusted and any(o.image_id == i for o in t.observations)
            for t in scene.tracks
        )
        for i in window
    }
    variables = [i for i in variables if support[i] >= config.min_landmarks]
    before = scene_rmse(scene)
    report = {
        "accepted": False,
        "reason": None,
        "newest_image_id": newest_image_id,
        "window_camera_ids": window,
        "variable_camera_ids": variables,
        "trusted_observation_count_by_camera": {str(i): n for i, n in support.items()},
        "variable_point_ids": sorted(local_points),
        "excluded_point_count": len(scene.points) - len(local_points),
        "settings": asdict(config),
        "before_rmse_px": before,
        "after_rmse_px": before,
        "candidate_rmse_px": None,
        "solver": None,
    }
    chosen = scene
    if newest_image_id not in variables or len(local_points) < config.min_landmarks:
        report["reason"] = "insufficient_local_camera_or_landmark_support"
    else:
        try:
            result = bundle_adjust(
                scene,
                loss=config.loss,
                f_scale=config.f_scale_px,
                max_nfev=config.max_nfev,
                variable_camera_ids=variables,
                variable_point_ids=local_points,
                observation_point_ids=local_points,
            )
            report["solver"] = result.report
            candidate_rmse = scene_rmse(result.scene)
            report["candidate_rmse_px"] = candidate_rmse
            selected_before = result.report["before"]["reprojection_rmse_px"]
            selected_after = result.report["after"]["reprojection_rmse_px"]
            if not result.report["accepted"]:
                report["reason"] = "bundle_candidate_rejected"
            elif candidate_rmse > before + config.rmse_tolerance_px:
                report["reason"] = "full_scene_rmse_increased"
            elif selected_after > selected_before + config.rmse_tolerance_px:
                report["reason"] = "selected_landmark_rmse_increased"
            else:
                report.update(accepted=True, after_rmse_px=candidate_rmse)
                chosen = result.scene
        except ValueError as error:
            report["reason"] = f"local_problem_unavailable: {error}"
    report["runtime_s"] = perf_counter() - start
    if report["accepted"]:
        # Keep input warnings as provenance once; the global BA wrapper's warning
        # prefixes are unsuitable for repeated local rounds.
        chosen = replace(
            chosen,
            metadata={
                **scene.metadata,
                "quality_status": "locally_bundle_adjusted_unverified",
                "scale_status": "arbitrary_visual_scale_unverified",
                "local_bundle_adjustment": report,
            },
        )
    return BundleResult(chosen, report)
