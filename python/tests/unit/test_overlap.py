import pytest

from distance_measuring.vision.overlap import DisconnectedOverlapError, require_connected_overlap


def test_overlap_chain_connects_all_images_without_requiring_all_pairs():
    result = require_connected_overlap([0, 1, 2, 3], iter([(0, 1), (1, 2), (2, 3)]))
    assert result["connected"] and len(result["edges"]) == 3


@pytest.mark.parametrize(
    "edges,components",
    [
        ([(0, 1), (1, 2)], ((0, 1, 2), (3,))),
        ([(0, 1), (2, 3)], ((0, 1), (2, 3))),
        ([], ((0,), (1,), (2,), (3,))),
    ],
)
def test_isolated_images_and_disconnected_groups_abort(edges, components):
    with pytest.raises(DisconnectedOverlapError, match="aborting") as caught:
        require_connected_overlap([0, 1, 2, 3], edges)
    assert caught.value.components == components
