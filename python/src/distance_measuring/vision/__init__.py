"""Sparse features, descriptor matching, and epipolar verification."""

from .epipolar import estimate_fundamental, rectify_matches, sampson_errors
from .features import FeatureBackend, Features, SiftBackend
from .matching import DescriptorMatches, match_descriptors
from .pose import (
    EssentialEstimate,
    RelativePose,
    chain_camera,
    decompose_essential,
    estimate_essential,
    estimate_essential_linear,
    recover_relative_pose,
)
from .tracks import FeatureTrack, MatchEdge, TrackBuildResult, TrackConflict, build_tracks
from .triangulation import TriangulationResult, triangulate_point

__all__ = [
    "DescriptorMatches",
    "EssentialEstimate",
    "FeatureBackend",
    "FeatureTrack",
    "Features",
    "MatchEdge",
    "RelativePose",
    "SiftBackend",
    "TrackBuildResult",
    "TrackConflict",
    "TriangulationResult",
    "build_tracks",
    "chain_camera",
    "decompose_essential",
    "estimate_essential",
    "estimate_essential_linear",
    "estimate_fundamental",
    "match_descriptors",
    "rectify_matches",
    "recover_relative_pose",
    "sampson_errors",
    "triangulate_point",
]
