"""Sparse features, descriptor matching, and epipolar verification."""

from .epipolar import estimate_fundamental, rectify_matches, sampson_errors
from .features import FeatureBackend, Features, SiftBackend
from .matching import DescriptorMatches, match_descriptors

__all__ = [
    "DescriptorMatches",
    "FeatureBackend",
    "Features",
    "SiftBackend",
    "estimate_fundamental",
    "match_descriptors",
    "rectify_matches",
    "sampson_errors",
]
