"""Conservative pre-geometry keyframe candidates from the P2/P3 measurements."""

from dataclasses import asdict, dataclass

import numpy as np


@dataclass(frozen=True)
class KeyframeConfig:
    min_tracks: int = 24
    grid_rows: int = 6
    grid_cols: int = 8
    min_occupied_cells: int = 6
    min_parallax_fraction: float = 3 / 1600

    def __post_init__(self):
        for name in ("min_tracks", "grid_rows", "grid_cols", "min_occupied_cells"):
            if type(getattr(self, name)) is not int or getattr(self, name) < 1:
                raise ValueError(f"{name} must be a positive integer")
        if self.min_occupied_cells > self.grid_rows * self.grid_cols:
            raise ValueError("Keyframe coverage threshold must fit the grid")
        if not np.isfinite(self.min_parallax_fraction) or self.min_parallax_fraction <= 0:
            raise ValueError("Keyframe parallax fraction must be positive and finite")


DEFAULT_KEYFRAME = KeyframeConfig()


def score_keyframe(
    track_count,
    source_cells,
    target_cells,
    median_parallax_px,
    image_size,
    *,
    rotation_available=True,
    config=DEFAULT_KEYFRAME,
):
    """Score a processed frame pair; a candidate is not a registered camera."""
    if (
        any(type(n) is not int or n < 0 for n in (track_count, source_cells, target_cells))
        or len(image_size) != 2
        or any(type(n) is not int or n <= 0 for n in image_size)
        or max(source_cells, target_cells) > config.grid_rows * config.grid_cols
        or max(source_cells, target_cells) > track_count
        or type(rotation_available) is not bool
        or (
            median_parallax_px is not None
            and (not np.isfinite(median_parallax_px) or median_parallax_px < 0)
        )
    ):
        raise ValueError("Invalid keyframe counts, coverage, parallax or image dimensions")
    threshold = config.min_parallax_fraction * max(image_size)
    reasons = []
    if track_count < config.min_tracks:
        reasons.append("insufficient_tracks")
    if min(source_cells, target_cells) < config.min_occupied_cells:
        reasons.append("insufficient_coverage")
    if not rotation_available:
        reasons.append("rotation_unavailable")
    if median_parallax_px is None:
        reasons.append("parallax_unavailable")
    elif median_parallax_px < threshold:
        reasons.append("insufficient_parallax")
    score = (
        min(
            1.0,
            track_count / config.min_tracks,
            min(source_cells, target_cells) / config.min_occupied_cells,
            median_parallax_px / threshold,
        )
        if rotation_available and median_parallax_px is not None
        else 0.0
    )
    return {
        "label": "skip" if reasons else "candidate keyframe",
        "candidate": not reasons,
        "score": float(score),
        "reasons": reasons or ["supported_motion_candidate"],
        "track_count": track_count,
        "occupied_cells": [source_cells, target_cells],
        "median_residual_parallax_px": median_parallax_px,
        "parallax_threshold_px": threshold,
        "rotation_available": rotation_available,
        "settings": asdict(config),
    }
