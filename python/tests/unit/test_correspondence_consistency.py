import numpy as np
import pytest
from numpy.testing import assert_allclose, assert_array_equal

from distance_measuring.vision.consistency import (
    ConsistentMatches,
    GroupedFeatures,
    closed_three_view_tracks,
    group_features,
    match_grouped,
    rootsift,
)


def grouped(values, ids=None):
    descriptors = np.array(values, np.float32).reshape(-1, 1)
    ids = np.arange(len(descriptors)) if ids is None else np.array(ids)
    return GroupedFeatures(
        np.zeros((len(np.unique(ids)), 2)), descriptors, ids, np.arange(len(ids)), 0
    )


def test_rootsift_known_histogram_and_empty_rows():
    actual = rootsift([[1, 3], [0, 0]])
    assert_allclose(actual, [[0.5, np.sqrt(0.75)], [0, 0]])
    assert_allclose(np.linalg.norm(actual[0]), 1)
    assert rootsift(np.empty((0, 128))).shape == (0, 128)
    with pytest.raises(ValueError):
        rootsift([[-1, 2]])


def test_grouping_collapses_variant_detections_and_preserves_descriptor_rows():
    points = [[0, 0], [10, 10], [0.2, 0.1], [10.1, 9.9]]
    descriptors = np.arange(8).reshape(4, 2)
    result = group_features(points, descriptors)
    assert len(result.points) == 2
    assert_array_equal(result.group_ids, [0, 0, 1, 1])
    assert_array_equal(result.raw_rows, [0, 2, 1, 3])
    assert_array_equal(result.descriptors, descriptors[result.raw_rows])
    assert_allclose(result.points, [[0.1, 0.05], [10.05, 9.95]])


def test_chained_detections_are_rejected_instead_of_becoming_one_diffuse_point():
    result = group_features(
        np.c_[np.arange(7), np.zeros(7)], np.ones((7, 3)), radius=1.1, max_radius=2
    )
    assert len(result.points) == 0 and result.rejected_cluster_count == 1


def test_variants_do_not_count_as_ambiguous_distinct_second_neighbors():
    a = grouped([0, 0.01, 10, 20], [0, 0, 1, 2])
    b = grouped([0.02, 0.03, 10.02, 30], [0, 0, 1, 2])
    result = match_grouped(a, b)
    assert_array_equal(result.indices, [[0, 0], [1, 1]])
    assert (result.ratios < 0.75).all()


def test_reverse_ratio_rejects_a_forward_confident_but_reverse_ambiguous_match():
    result = match_grouped(grouped([0, 0.1, 25]), grouped([0.05, 10, 30]))
    assert (0, 0) not in map(tuple, result.indices)
    assert (1, 0) not in map(tuple, result.indices)


def test_missing_distinct_neighbor_and_empty_features_are_not_matches():
    assert len(match_grouped(grouped([1, 1], [0, 0]), grouped([2, 2], [0, 0])).indices) == 0
    empty = group_features(np.empty((0, 2)), np.empty((0, 128)))
    assert match_grouped(empty, empty).indices.shape == (0, 2)


def pair(rows):
    rows = np.array(rows, int).reshape(-1, 2)
    return ConsistentMatches(rows, np.full(rows.shape, 0.3))


def test_cycle_closure_rejects_contradictions_and_open_paths():
    pairs = {
        (0, 1): pair([[0, 0], [1, 1], [2, 2]]),
        (1, 2): pair([[0, 0], [1, 1]]),
        (0, 2): pair([[0, 2], [1, 1]]),
    }
    points = [np.array([[0, 0], [10, 10], [20, 20]])] * 3
    tracks, summary = closed_three_view_tracks(pairs, points)
    assert_array_equal(tracks, [[1, 1, 1]])
    assert summary["contradictory_cycles"] == summary["open_paths"] == 1


def test_nearby_duplicates_do_not_inflate_three_view_support():
    pairs = {key: pair([[0, 0], [1, 1], [2, 2]]) for key in [(0, 1), (1, 2), (0, 2)]}
    points = [np.array([[0, 0], [2, 0], [10, 0]])] * 3
    tracks, summary = closed_three_view_tracks(pairs, points)
    assert len(tracks) == 2 and summary["spatial_duplicates_removed"] == 1


@pytest.mark.parametrize("options", [{"ratio": 0}, {"ratio": float("nan")}, {"neighbors": 1}])
def test_invalid_match_thresholds_fail(options):
    with pytest.raises(ValueError):
        match_grouped(grouped([1, 2]), grouped([1, 2]), **options)
