"""Multiview correspondences for lecture 002, slides/pages 136–138.

The deck motivates corresponding image points; the conflict-aware union-find
algorithm is an implementation extension. See docs/math/sparse_reconstruction.md.
"""

from collections.abc import Iterable
from dataclasses import dataclass

import numpy as np

FeatureKey = tuple[int, int]  # (recorded image ID, feature row ID)


@dataclass(frozen=True)
class MatchEdge:
    first: FeatureKey
    second: FeatureKey
    score: float = 0.0  # Lower scores get first choice when matches conflict.

    def __post_init__(self):
        for key in (self.first, self.second):
            if (
                not isinstance(key, tuple)
                or len(key) != 2
                or any(type(v) is not int or v < 0 for v in key)
            ):
                raise ValueError("Feature keys must be (nonnegative integer image ID, feature ID)")
        if not np.isfinite(self.score) or self.score < 0:
            raise ValueError("Match score must be nonnegative and finite")


@dataclass(frozen=True)
class FeatureTrack:
    track_id: int
    features: tuple[FeatureKey, ...]


@dataclass(frozen=True)
class TrackConflict:
    edge: MatchEdge
    reason: str


@dataclass(frozen=True)
class TrackBuildResult:
    tracks: tuple[FeatureTrack, ...]
    conflicts: tuple[TrackConflict, ...]
    redundant_edges: int


def build_tracks(edges: Iterable[MatchEdge]) -> TrackBuildResult:
    """Deterministic greedy merges; reject a union if it duplicates any image."""
    canonical = [MatchEdge(*sorted((edge.first, edge.second)), edge.score) for edge in edges]
    canonical.sort(key=lambda edge: (edge.score, edge.first, edge.second))
    parent, members = {}, {}

    def root(key):
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    conflicts, redundant = [], 0
    for edge in canonical:
        if edge.first[0] == edge.second[0]:
            conflicts.append(TrackConflict(edge, "same_image_edge"))
            continue
        for key in (edge.first, edge.second):
            if key not in parent:
                parent[key], members[key] = key, {key[0]: key}
        a, b = root(edge.first), root(edge.second)
        if a == b:
            redundant += 1  # A duplicate edge or consistent triangle closure.
            continue
        # Lecture 002 pp. 136–137 require observations of the SAME physical
        # point. Two different features in one image contradict that assumption.
        # Check BEFORE mutation; a rejected union must not corrupt either track.
        if members[a].keys() & members[b].keys():
            conflicts.append(TrackConflict(edge, "conflicting_image_features"))
            continue
        a, b = sorted((a, b))
        parent[b] = a
        members[a].update(members.pop(b))
    groups = sorted(tuple(sorted(group.values())) for group in members.values() if len(group) >= 2)
    return TrackBuildResult(
        tuple(FeatureTrack(index, group) for index, group in enumerate(groups)),
        tuple(conflicts),
        redundant,
    )
