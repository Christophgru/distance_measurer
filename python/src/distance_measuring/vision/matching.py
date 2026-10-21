"""L2 nearest neighbors, Lowe ratio (2004, section 7.1), optional mutual nearest.

This supplies sparse correspondences for lecture 002, slide/page 137; the deck
describes SSD/correlation block matching on pp. 141–144, not SIFT's ratio test.
"""

from dataclasses import dataclass

import cv2
import numpy as np
from numpy.typing import ArrayLike, NDArray


@dataclass(frozen=True)
class DescriptorMatches:
    indices: NDArray[np.int64]
    distances: NDArray[np.float64]
    ratios: NDArray[np.float64]
    raw_count: int
    two_neighbor_count: int
    ratio_count: int

    def __len__(self) -> int:
        return len(self.indices)


def match_descriptors(
    first: ArrayLike, second: ArrayLike, *, ratio: float = 0.75, mutual: bool = True
) -> DescriptorMatches:
    if not np.isfinite(ratio) or not 0 < ratio < 1:
        raise ValueError("ratio must be finite and strictly between zero and one")
    first, second = [np.asarray(value, dtype=np.float32) for value in (first, second)]
    if (
        first.ndim != 2
        or second.ndim != 2
        or first.shape[1] != second.shape[1]
        or first.shape[1] == 0
        or not np.isfinite(first).all()
        or not np.isfinite(second).all()
    ):
        raise ValueError("Descriptors must be finite NxD arrays with the same positive D")
    matcher = cv2.BFMatcher(cv2.NORM_L2)
    neighbors = matcher.knnMatch(first, second, k=min(2, len(second))) if len(second) else []
    raw_count = sum(bool(row) for row in neighbors)
    two_count = sum(len(row) == 2 for row in neighbors)
    accepted = [
        (a.queryIdx, a.trainIdx, a.distance, a.distance / b.distance)
        for row in neighbors
        if len(row) == 2
        for a, b in [row]
        # Strict comparison rejects equal-distance ambiguity, including zero/zero.
        if a.distance < ratio * b.distance
    ]
    ratio_count = len(accepted)
    if mutual and accepted:
        reverse = {m.queryIdx: m.trainIdx for m in matcher.match(second, first)}
        accepted = [row for row in accepted if reverse.get(row[1]) == row[0]]
    accepted.sort(key=lambda row: (row[0], row[1]))
    return DescriptorMatches(
        indices=np.array([(row[0], row[1]) for row in accepted], dtype=np.int64).reshape(-1, 2),
        distances=np.array([row[2] for row in accepted]),
        ratios=np.array([row[3] for row in accepted]),
        raw_count=raw_count,
        two_neighbor_count=two_count,
        ratio_count=ratio_count,
    )
