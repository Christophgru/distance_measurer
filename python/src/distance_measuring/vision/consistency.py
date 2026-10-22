"""Conservative three-view correspondence audit, lecture 002 pp. 136–144.

Page numbers are PPTX slide order. Affine-SIFT, RootSIFT, spatial grouping,
bidirectional ratio tests and cycle closure are explicit implementation
extensions; the lecture motivates correspondence and geometric verification.
"""

from dataclasses import dataclass

import cv2
import numpy as np
from scipy.spatial import cKDTree


def rootsift(descriptors: np.ndarray) -> np.ndarray:
    """Arandjelovic/Zisserman 2012, section 3: sqrt of L1-normalized SIFT."""
    descriptors = np.asarray(descriptors, dtype=np.float32)
    if (
        descriptors.ndim != 2
        or descriptors.shape[1] == 0
        or not np.isfinite(descriptors).all()
        or np.any(descriptors < 0)
    ):
        raise ValueError("RootSIFT requires finite nonnegative NxD descriptors")
    total = descriptors.sum(axis=1, keepdims=True)
    return np.sqrt(np.divide(descriptors, total, out=np.zeros_like(descriptors), where=total > 0))


@dataclass(frozen=True)
class GroupedFeatures:
    points: np.ndarray
    descriptors: np.ndarray
    group_ids: np.ndarray
    raw_rows: np.ndarray
    rejected_cluster_count: int


def group_features(points, descriptors, *, radius=1.5, max_radius=2.0) -> GroupedFeatures:
    """Collapse orientation/synthetic-view duplicates; reject diffuse clusters.

    A chain of neighboring detections must not become one poorly localized point.
    """
    points = np.asarray(points, dtype=float)
    descriptors = np.asarray(descriptors, dtype=np.float32)
    if points.ndim != 2 or points.shape[1] != 2 or not np.isfinite(points).all():
        raise ValueError("Feature points must be a finite Nx2 array")
    if (
        descriptors.ndim != 2
        or len(descriptors) != len(points)
        or descriptors.shape[1] == 0
        or not np.isfinite(descriptors).all()
    ):
        raise ValueError("Descriptors must be finite NxD rows aligned with feature points")
    if not np.isfinite([radius, max_radius]).all() or not 0 < radius <= max_radius:
        raise ValueError("Require 0 < radius <= max_radius")
    parent = np.arange(len(points))

    def root(key):
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    for a, b in cKDTree(points).query_pairs(radius, output_type="ndarray"):
        a, b = root(a), root(b)
        if a != b:
            parent[max(a, b)] = min(a, b)
    components = {}
    for row in range(len(points)):
        components.setdefault(root(row), []).append(row)
    centers, rows, ids, rejected = [], [], [], 0
    for members in components.values():
        center = np.median(points[members], axis=0)
        if np.max(np.linalg.norm(points[members] - center, axis=1)) > max_radius:
            rejected += 1
            continue
        ids.extend([len(centers)] * len(members))
        rows.extend(members)
        centers.append(center)
    rows = np.asarray(rows, dtype=np.int64)
    return GroupedFeatures(
        np.array(centers, dtype=float).reshape(-1, 2),
        np.ascontiguousarray(descriptors[rows]),
        np.asarray(ids, dtype=np.int64),
        rows,
        rejected,
    )


@dataclass(frozen=True)
class ConsistentMatches:
    indices: np.ndarray
    ratios: np.ndarray  # Columns: forward and reverse; both must pass.


def match_grouped(first: GroupedFeatures, second: GroupedFeatures, *, ratio=0.75, neighbors=12):
    """Require ratio tests in BOTH directions and one-to-one physical groups.

    Synthetic variants of the same point are not competing second neighbors.
    Missing a distinct second neighbor conservatively rejects that descriptor.
    """
    if not np.isfinite(ratio) or not 0 < ratio < 1:
        raise ValueError("ratio must be finite and in (0,1)")
    if type(neighbors) is not int or neighbors < 2:
        raise ValueError("neighbors must be an integer >=2")

    def candidates(a, b):
        result = {}
        if not len(a.descriptors) or not len(b.descriptors):
            return result
        rows = cv2.BFMatcher(cv2.NORM_L2).knnMatch(
            a.descriptors, b.descriptors, k=min(neighbors, len(b.descriptors))
        )
        for row in rows:
            best = row[0]
            target = int(b.group_ids[best.trainIdx])
            other = next((m for m in row[1:] if b.group_ids[m.trainIdx] != target), None)
            if other is None or not best.distance < ratio * other.distance:
                continue
            key = (int(a.group_ids[best.queryIdx]), target)
            value = (best.distance / other.distance, best.distance)
            result[key] = min(result.get(key, value), value)
        return result

    forward, reverse = candidates(first, second), candidates(second, first)
    proposals = []
    for (a, b), (f_ratio, distance) in forward.items():
        if (b, a) in reverse:
            r_ratio = reverse[b, a][0]
            proposals.append((max(f_ratio, r_ratio), distance, a, b, f_ratio, r_ratio))
    # Greedy quality ordering makes the exported pair a one-to-one relation.
    # This is not a global assignment optimum or a ground-truth guarantee.
    used_a, used_b, selected = set(), set(), []
    for _, _, a, b, fr, rr in sorted(proposals):
        if a not in used_a and b not in used_b:
            selected.append((a, b, fr, rr))
            used_a.add(a)
            used_b.add(b)
    selected.sort()
    return ConsistentMatches(
        np.array([(a, b) for a, b, _, _ in selected], dtype=np.int64).reshape(-1, 2),
        np.array([(fr, rr) for _, _, fr, rr in selected], dtype=float).reshape(-1, 2),
    )


def closed_three_view_tracks(pairs, points, *, min_separation=4.0):
    """Require A→B→C to equal direct A→C, then deduplicate physical locations.

    Lecture 002 pp. 136–138 require the SAME physical point in all views.
    Cycle equality is an extra necessary consistency test, not proof of identity.
    Input keys are (0,1), (1,2), (0,2), in the caller's local image order.
    """
    if not np.isfinite(min_separation) or min_separation <= 0:
        raise ValueError("min_separation must be positive and finite")
    maps, scores = {}, {}
    for ids in ((0, 1), (1, 2), (0, 2)):
        result = pairs[ids]
        if any(len(np.unique(result.indices[:, k])) != len(result.indices) for k in range(2)):
            raise ValueError("Pair matches must be one-to-one")
        maps[ids] = dict(result.indices.tolist())
        scores[ids] = {
            tuple(row): float(max(r)) for row, r in zip(result.indices, result.ratios, strict=True)
        }
    candidates, open_paths, contradictions = [], 0, 0
    for a, b in maps[0, 1].items():
        if b not in maps[1, 2]:
            open_paths += 1
            continue
        c = maps[1, 2][b]
        if a not in maps[0, 2]:
            open_paths += 1
            continue
        if maps[0, 2][a] != c:
            contradictions += 1
            continue
        quality = max(scores[0, 1][a, b], scores[1, 2][b, c], scores[0, 2][a, c])
        candidates.append((quality, (a, b, c)))
    retained = []
    for _, track in sorted(candidates):
        if any(
            any(
                np.linalg.norm(points[i][track[i]] - points[i][other[i]]) < min_separation
                for i in range(3)
            )
            for other in retained
        ):
            continue
        retained.append(track)
    return np.array(retained, dtype=np.int64).reshape(-1, 3), {
        "closed_before_spatial_deduplication": len(candidates),
        "spatial_duplicates_removed": len(candidates) - len(retained),
        "open_paths": open_paths,
        "contradictory_cycles": contradictions,
    }
