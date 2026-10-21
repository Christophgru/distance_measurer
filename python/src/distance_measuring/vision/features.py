"""Sparse feature backend: the correspondence stage in lecture 002, slide/page 137.

SIFT itself is an extension beyond this deck's block matching (pp. 141–144).
See Lowe (2004), sections 3–6, and docs/math/feature_matching.md.
"""

from dataclasses import dataclass
from typing import Protocol

import cv2
import numpy as np
from numpy.typing import NDArray


@dataclass(frozen=True)
class Features:
    """Aligned rows in the input image's pixel frame; size is a diameter in pixels."""

    points: NDArray[np.float64]
    descriptors: NDArray[np.float32]
    response: NDArray[np.float64]
    size: NDArray[np.float64]
    angle: NDArray[np.float64]
    octave: NDArray[np.int32]

    def __len__(self) -> int:
        return len(self.points)


class FeatureBackend(Protocol):
    """Backends return coordinates and descriptors with identical row indices."""

    def extract(self, image: NDArray[np.uint8]) -> Features: ...


@dataclass(frozen=True)
class SiftBackend:
    max_features: int = 6000
    contrast_threshold: float = 0.04

    def __post_init__(self) -> None:
        if type(self.max_features) is not int or self.max_features < 1:
            raise ValueError("max_features must be a positive integer")
        if not np.isfinite(self.contrast_threshold) or self.contrast_threshold <= 0:
            raise ValueError("contrast_threshold must be positive and finite")

    def extract(self, image: NDArray[np.uint8]) -> Features:
        if image.dtype != np.uint8 or image.size == 0:
            raise ValueError("SIFT requires a nonempty uint8 image")
        if image.ndim == 3 and image.shape[2] == 3:
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        elif image.ndim == 2:
            gray = image
        else:
            raise ValueError("SIFT requires grayscale or three-channel BGR input")
        detector = cv2.SIFT_create(
            nfeatures=self.max_features, contrastThreshold=self.contrast_threshold
        )
        keypoints, descriptors = detector.detectAndCompute(gray, None)
        # Stable ordering gives reproducible feature IDs on the same OpenCV build.
        order = sorted(
            range(len(keypoints)),
            key=lambda i: (
                -keypoints[i].response,
                *keypoints[i].pt,
                keypoints[i].size,
                keypoints[i].angle,
                keypoints[i].octave,
            ),
        )[: self.max_features]
        keys = [keypoints[i] for i in order]
        return Features(
            points=np.array([k.pt for k in keys], dtype=np.float64).reshape(-1, 2),
            descriptors=(
                np.ascontiguousarray(descriptors[order], dtype=np.float32)
                if keys
                else np.empty((0, 128), dtype=np.float32)
            ),
            response=np.array([k.response for k in keys]),
            size=np.array([k.size for k in keys]),
            angle=np.array([k.angle for k in keys]),
            octave=np.array([k.octave for k in keys], dtype=np.int32),
        )
