"""Deterministic pre-pose track ranking using current optical/gyro diagnostics."""

from dataclasses import dataclass

import numpy as np

from .tracking import TrackingConfig, grid_cell_ids


@dataclass(frozen=True)
class RankingConfig:
    grid_rows: int = 6
    grid_cols: int = 8
    min_parallax_px: float = 2.0
    max_fb_error_px: float = 1.5
    age_saturation: int = 5
    parallax_saturation_px: float = 32.0

    def __post_init__(self):
        TrackingConfig(grid_rows=self.grid_rows, grid_cols=self.grid_cols)
        if type(self.age_saturation) is not int or self.age_saturation < 2:
            raise ValueError("age_saturation must be an integer of at least 2")
        for name in ("min_parallax_px", "max_fb_error_px", "parallax_saturation_px"):
            value = getattr(self, name)
            if not np.isfinite(value) or value < 0 or (name != "min_parallax_px" and value == 0):
                qualifier = "nonnegative" if name == "min_parallax_px" else "positive"
                raise ValueError(f"{name} must be finite and {qualifier}")


DEFAULT_RANKING = RankingConfig()


@dataclass(frozen=True)
class TrackRanking:
    selected_indices: np.ndarray
    eligible: np.ndarray
    scores: np.ndarray
    components: dict[str, np.ndarray]
    source_cells: np.ndarray
    target_cells: np.ndarray


def rank_tracks(
    source_pixels,
    target_pixels,
    *,
    track_ids,
    ages,
    responses,
    fb_errors_px,
    parallax_px,
    first_size,
    second_size,
    best_n: int,
    config: RankingConfig = DEFAULT_RANKING,
) -> TrackRanking:
    """Return up to N eligible rows, balancing coverage in BOTH images.

    Score is the equal mean of bounded age, birth response (90th-percentile
    normalization), FB quality, capped parallax and inverse cell occupancy.
    Greedy selection favors unused/less-used source and target cells before
    score; ties use stable track IDs. A low residual or invalid prediction does
    not become useful merely because a caller requests more tracks.
    """
    if type(best_n) is not int or best_n < 1:
        raise ValueError("best_n must be a positive integer")
    ids, ages = np.asarray(track_ids), np.asarray(ages)
    response, fb, parallax = [
        np.asarray(a, dtype=float) for a in (responses, fb_errors_px, parallax_px)
    ]
    if ids.ndim != 1:
        raise ValueError("Track IDs must be a vector")
    count = len(ids)
    if any(a.shape != (count,) for a in (ids, ages, response, fb, parallax)):
        raise ValueError("Track IDs, ages, quality, FB and parallax must be aligned vectors")
    if (
        ids.dtype.kind not in "iu"
        or ages.dtype.kind not in "iu"
        or np.any(ids < 0)
        or len(set(ids)) != count
    ):
        raise ValueError("Track IDs must be unique nonnegative integers; ages must be integers")
    grid = TrackingConfig(grid_rows=config.grid_rows, grid_cols=config.grid_cols)
    source_cells = grid_cell_ids(source_pixels, (first_size[1], first_size[0]), grid)
    target_cells = grid_cell_ids(target_pixels, (second_size[1], second_size[0]), grid)
    if len(source_cells) != count or len(target_cells) != count:
        raise ValueError("Pixel rows must align with track vectors")
    eligible = (
        (ages >= 2)
        & np.isfinite(response)
        & (response >= 0)
        & np.isfinite(fb)
        & (fb >= 0)
        & (fb <= config.max_fb_error_px)
        & np.isfinite(parallax)
        & (parallax >= config.min_parallax_px)
    )
    components = {
        name: np.zeros(count)
        for name in ("age", "response", "forward_backward", "parallax", "occupancy")
    }
    rows = np.flatnonzero(eligible)
    cells = config.grid_rows * config.grid_cols
    if len(rows):
        components["age"][rows] = np.minimum(ages[rows] / config.age_saturation, 1)
        scale = max(float(np.percentile(response[rows], 90)), 1e-12)
        components["response"][rows] = np.minimum(response[rows] / scale, 1)
        components["forward_backward"][rows] = 1 / (1 + fb[rows])
        components["parallax"][rows] = np.minimum(parallax[rows] / config.parallax_saturation_px, 1)
        source_counts = np.bincount(source_cells[rows], minlength=cells)
        target_counts = np.bincount(target_cells[rows], minlength=cells)
        components["occupancy"][rows] = 1 / np.sqrt(
            source_counts[source_cells[rows]] * target_counts[target_cells[rows]]
        )
    scores = np.mean(list(components.values()), axis=0)
    source_load, target_load = np.zeros(cells, int), np.zeros(cells, int)
    remaining, selected = set(int(i) for i in rows), []
    while remaining and len(selected) < best_n:

        def priority(i):
            a, b = source_load[source_cells[i]], target_load[target_cells[i]]
            return max(a, b), a + b, -scores[i], int(ids[i])

        index = min(remaining, key=priority)
        remaining.remove(index)
        selected.append(index)
        source_load[source_cells[index]] += 1
        target_load[target_cells[index]] += 1
    return TrackRanking(
        np.asarray(selected, dtype=int), eligible, scores, components, source_cells, target_cells
    )
