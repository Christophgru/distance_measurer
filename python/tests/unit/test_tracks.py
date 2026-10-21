import random

import pytest

from distance_measuring.vision.tracks import MatchEdge, build_tracks


def test_merges_triangles_and_keeps_distinct_tracks():
    edges = [
        MatchEdge((0, 3), (1, 8)),
        MatchEdge((1, 8), (2, 5)),
        MatchEdge((0, 3), (2, 5)),
        MatchEdge((0, 4), (2, 6)),
    ]
    result = build_tracks(edges)
    assert [track.features for track in result.tracks] == [
        ((0, 3), (1, 8), (2, 5)),
        ((0, 4), (2, 6)),
    ]
    assert [track.track_id for track in result.tracks] == [0, 1]
    assert result.redundant_edges == 1
    assert not result.conflicts


def test_conflicting_union_leaves_both_components_valid():
    edges = [
        MatchEdge((0, 1), (1, 2), 0.1),
        MatchEdge((1, 2), (2, 3), 0.2),
        MatchEdge((0, 9), (2, 3), 0.3),
        MatchEdge((0, 9), (1, 8), 0.4),
        MatchEdge((2, 3), (1, 8), 0.5),
    ]
    result = build_tracks(edges)
    assert len(result.conflicts) == 2
    assert {conflict.reason for conflict in result.conflicts} == {"conflicting_image_features"}
    assert [track.features for track in result.tracks] == [
        ((0, 1), (1, 2), (2, 3)),
        ((0, 9), (1, 8)),
    ]
    for track in result.tracks:
        assert len({image_id for image_id, _ in track.features}) == len(track.features)


def test_lower_error_edges_win_conflicts_independently_of_input_order():
    edges = [
        MatchEdge((4, 5), (7, 8), 0.5),
        MatchEdge((4, 6), (7, 8), 0.1),
        MatchEdge((7, 8), (9, 3), 0.2),
        MatchEdge((4, 6), (9, 3), 0.3),
    ]
    expected = build_tracks(edges)
    random.Random(15).shuffle(edges)
    reversed_edges = [MatchEdge(edge.second, edge.first, edge.score) for edge in edges]
    assert build_tracks(iter(reversed_edges)) == expected
    assert expected.tracks[0].features == ((4, 6), (7, 8), (9, 3))


def test_duplicate_edges_are_redundant_and_same_image_edges_rejected():
    result = build_tracks(
        [
            MatchEdge((0, 1), (1, 2)),
            MatchEdge((1, 2), (0, 1)),
            MatchEdge((0, 3), (0, 4)),
            MatchEdge((2, 1), (2, 1)),
        ]
    )
    assert len(result.tracks) == 1
    assert result.redundant_edges == 1
    assert [conflict.reason for conflict in result.conflicts] == ["same_image_edge"] * 2
    assert not build_tracks([]).tracks


@pytest.mark.parametrize(
    "first,score",
    [((-1, 0), 0), ((True, 0), 0), ((0, 0.5), 0), ((0,), 0), ((0, 1), -1), ((0, 1), float("nan"))],
)
def test_invalid_edge_identity_and_score(first, score):
    with pytest.raises(ValueError):
        MatchEdge(first, (1, 2), score)
