"""Require a connected graph of geometrically supported image overlaps.

Correspondences must refer to the same physical point (lecture 002 p. 137);
graph connectivity is an additional pipeline requirement, not proof of identity.
"""


class DisconnectedOverlapError(ValueError):
    """Selected images cannot all be reconstructed in one connected scene."""

    def __init__(self, components, stage):
        self.components = tuple(tuple(c) for c in components)
        self.stage = stage
        super().__init__(
            f"Disconnected image overlap at {stage}; groups: {list(map(list, self.components))}. "
            "Every selected image must connect to the others through verified overlaps; aborting."
        )


def require_connected_overlap(image_ids, edges, *, stage="verified correspondences"):
    """A chain is sufficient; isolated images and multiple components are errors."""
    ids = list(image_ids)
    edges = list(edges)
    if len(ids) < 2 or len(set(ids)) != len(ids):
        raise ValueError("Overlap validation needs at least two distinct image IDs")
    neighbors = {i: set() for i in ids}
    for i, j in edges:
        if i not in neighbors or j not in neighbors or i == j:
            raise ValueError("Overlap edges must reference two different selected images")
        neighbors[i].add(j)
        neighbors[j].add(i)
    components, remaining = [], set(ids)
    while remaining:
        group, pending = set(), [next(i for i in ids if i in remaining)]
        while pending:
            i = pending.pop()
            if i in group:
                continue
            group.add(i)
            pending.extend(neighbors[i] - group)
        components.append([i for i in ids if i in group])
        remaining -= group
    if len(components) > 1:
        raise DisconnectedOverlapError(components, stage)
    return {"connected": True, "image_ids": ids, "edges": [list(e) for e in edges]}
