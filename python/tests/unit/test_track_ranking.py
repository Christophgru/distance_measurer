import numpy as np
import pytest
from numpy.testing import assert_array_equal

from distance_measuring.vision.track_ranking import RankingConfig, rank_tracks


def rank(points, **kwargs):
    n = len(points)
    values = dict(
        track_ids=np.arange(n),
        ages=np.full(n, 3),
        responses=np.ones(n),
        fb_errors_px=np.full(n, 0.2),
        parallax_px=np.full(n, 10.0),
        first_size=(400, 400),
        second_size=(400, 400),
        best_n=8,
        config=RankingConfig(grid_rows=4, grid_cols=4),
    )
    values.update(kwargs)
    return rank_tracks(
        np.asarray(points, float).reshape(-1, 2), np.asarray(points, float).reshape(-1, 2), **values
    )


def test_dense_texture_cell_cannot_occupy_all_selected_slots():
    dense = [[10 + i, 20] for i in range(20)]
    spread = [[150, 50], [250, 50], [350, 50], [50, 150], [150, 150], [250, 150], [350, 150]]
    result = rank(dense + spread, responses=[100.0] * 20 + [1.0] * 7)
    assert len(result.selected_indices) == 8
    assert len(set(result.source_cells[result.selected_indices])) == 8
    assert result.components["occupancy"][-1] > result.components["occupancy"][0]


@pytest.mark.parametrize(
    "changes",
    [
        {"ages": [5, 2]},
        {"responses": [1.0, 0.1]},
        {"fb_errors_px": [0.1, 1.0]},
        {"parallax_px": [30.0, 3.0]},
    ],
)
def test_each_quality_measure_improves_rank_with_coverage_held_equal(changes):
    result = rank([[20, 20], [40, 40]], best_n=1, **changes)
    assert result.selected_indices.tolist() == [0]
    assert result.scores[0] > result.scores[1]


def test_invalid_or_rotation_only_tracks_are_not_returned_to_fill_requested_n():
    result = rank(
        [[20, 20], [120, 20], [220, 20], [320, 20]],
        parallax_px=[0.0, np.nan, 20.0, 20.0],
        fb_errors_px=[0.0, 0.0, 2.0, 0.0],
    )
    assert_array_equal(result.eligible, [False, False, False, True])
    assert result.selected_indices.tolist() == [3]


def test_exact_ties_are_stable_by_track_id_and_row_reordering_preserves_selection():
    points = [[20, 20], [40, 40], [60, 60]]
    result = rank(points, track_ids=[30, 10, 20], best_n=2)
    assert result.selected_indices.tolist() == [1, 2]
    reordered = rank(list(reversed(points)), track_ids=[20, 10, 30], best_n=2)
    assert np.array([20, 10, 30])[reordered.selected_indices].tolist() == [10, 20]


def test_empty_candidates_and_invalid_options():
    result = rank([], track_ids=np.array([], int), ages=np.array([], int))
    assert result.selected_indices.size == 0
    for options in ({"best_n": 0}, {"track_ids": [1, 1]}, {"ages": [2.5, 3.0]}):
        with pytest.raises(ValueError):
            rank([[20, 20], [40, 40]], **options)
    with pytest.raises(ValueError):
        RankingConfig(min_parallax_px=-1)


def test_selection_preserves_target_coverage_even_for_stronger_concentrated_candidates():
    source = [[20, 20], [120, 20], [220, 20], [320, 20], [20, 120], [120, 120]]
    target = [[20, 20], [40, 40], [60, 60], [120, 20], [220, 20], [320, 20]]
    result = rank_tracks(
        source,
        target,
        track_ids=np.arange(6),
        ages=np.full(6, 3),
        responses=[100.0, 100.0, 100.0, 1.0, 1.0, 1.0],
        fb_errors_px=np.zeros(6),
        parallax_px=np.full(6, 10.0),
        first_size=(400, 400),
        second_size=(400, 400),
        best_n=4,
        config=RankingConfig(grid_rows=4, grid_cols=4),
    )
    assert len(set(result.source_cells[result.selected_indices])) == 4
    assert len(set(result.target_cells[result.selected_indices])) == 4
