"""Internal K, lens distortion, YAML profiles, and explicit approximate fallbacks.

See docs/math/camera_model.md, lecture slides 70–76, 82, 102, and 118–121.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import yaml
from numpy.typing import ArrayLike, NDArray

from distance_measuring.dataset.schema import ImageMetadata

from .geometry import CameraExtrinsics, from_homogeneous, points_array, to_homogeneous

SOURCES = ("calibrated", "metadata-derived", "heuristic")
PIXEL_CONVENTION = "stored_pixels_ignore_exif"


@dataclass(frozen=True, slots=True)
class CameraIntrinsics:
    fx: float
    fy: float
    cx: float
    cy: float
    image_width: int
    image_height: int
    distortion: tuple[float, ...] = (0, 0, 0, 0, 0)
    skew: float = 0.0
    source: str = "calibrated"
    camera_id: str | None = None
    assumptions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("fx", "fy", "cx", "cy", "skew"):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float, np.number))
                or not np.isrealobj(value)
            ):
                raise ValueError(f"{name} must be a finite number")
            if not np.isfinite(value) or (name in ("fx", "fy") and value <= 0):
                raise ValueError(
                    f"{name} must be finite" + (" and positive" if name in ("fx", "fy") else "")
                )
            object.__setattr__(self, name, float(value))
        if (
            type(self.image_width) is not int
            or self.image_width <= 0
            or type(self.image_height) is not int
            or self.image_height <= 0
        ):
            raise ValueError("Image width and height must be positive integers")
        coefficients = np.asarray(self.distortion, dtype=np.float64)
        if (
            coefficients.ndim != 1
            or len(coefficients) not in (0, 4, 5)
            or not np.isfinite(coefficients).all()
        ):
            raise ValueError("Distortion must contain 0, 4, or 5 finite Brown-Conrady coefficients")
        padded = np.zeros(5)
        padded[: len(coefficients)] = coefficients
        object.__setattr__(self, "distortion", tuple(float(value) for value in padded))
        if self.source not in SOURCES:
            raise ValueError(f"Intrinsic source must be one of {SOURCES}")
        if self.camera_id is not None and (
            not isinstance(self.camera_id, str) or not self.camera_id
        ):
            raise ValueError("camera_id must be a nonempty string or None")
        if not isinstance(self.assumptions, (tuple, list)) or not all(
            isinstance(value, str) for value in self.assumptions
        ):
            raise ValueError("assumptions must contain strings")
        object.__setattr__(self, "assumptions", tuple(self.assumptions))

    @property
    def K(self) -> NDArray[np.float64]:
        # Lecture K = M_int. cx/cy correspond to x0/y0 (or Cx/Cy) in slides.
        return np.array([[self.fx, self.skew, self.cx], [0, self.fy, self.cy], [0, 0, 1.0]])

    @property
    def image_size(self) -> tuple[int, int]:
        return self.image_width, self.image_height

    @property
    def approximate(self) -> bool:
        return self.source != "calibrated"

    @classmethod
    def from_K(cls, K: ArrayLike, image_size: tuple[int, int], **kwargs: Any) -> CameraIntrinsics:
        K = np.asarray(K, dtype=np.float64)
        if K.shape != (3, 3) or not np.isfinite(K).all() or K[2, 2] == 0:
            raise ValueError("K must be a finite 3x3 intrinsic matrix with nonzero K[2,2]")
        K = K / K[2, 2]
        if not np.allclose(K[[1, 2, 2], [0, 0, 1]], 0, atol=1e-10, rtol=0):
            raise ValueError("K must be upper triangular")
        return cls(K[0, 0], K[1, 1], K[0, 2], K[1, 2], *image_size, skew=K[0, 1], **kwargs)

    def normalize_pixels(self, pixels: ArrayLike) -> NDArray[np.float64]:
        pixels = points_array(pixels, 2, "pixels")
        return from_homogeneous(to_homogeneous(pixels) @ np.linalg.inv(self.K).T)

    def pixels_from_normalized(self, normalized: ArrayLike) -> NDArray[np.float64]:
        return from_homogeneous(to_homogeneous(points_array(normalized, 2)) @ self.K.T)

    def distort_normalized(self, normalized: ArrayLike) -> NDArray[np.float64]:
        points = points_array(normalized, 2)
        x, y = points[..., 0], points[..., 1]
        k1, k2, p1, p2, k3 = self.distortion
        r2 = x * x + y * y
        radial = 1 + r2 * (k1 + r2 * (k2 + r2 * k3))
        return np.stack(
            (
                x * radial + 2 * p1 * x * y + p2 * (r2 + 2 * x * x),
                y * radial + p1 * (r2 + 2 * y * y) + 2 * p2 * x * y,
            ),
            axis=-1,
        )

    def distort_points(self, ideal_pixels: ArrayLike) -> NDArray[np.float64]:
        return self.pixels_from_normalized(
            self.distort_normalized(self.normalize_pixels(ideal_pixels))
        )

    def undistort_points(
        self, pixels: ArrayLike, *, normalized: bool = False
    ) -> NDArray[np.float64]:
        """Invert radial/tangential distortion with vectorized Newton iterations.

        Pixel output uses the SAME K (including skew), without EXIF rotation.
        Set normalized=True for ideal camera rays (x, y, 1).
        """
        target = self.normalize_pixels(pixels)
        estimate = target.copy()
        if estimate.size and any(self.distortion):
            k1, k2, p1, p2, k3 = self.distortion
            for _ in range(40):
                x, y = estimate[..., 0], estimate[..., 1]
                r2 = x * x + y * y
                radial = 1 + r2 * (k1 + r2 * (k2 + r2 * k3))
                slope = k1 + 2 * k2 * r2 + 3 * k3 * r2 * r2
                j11 = radial + 2 * x * x * slope + 2 * p1 * y + 6 * p2 * x
                j12 = 2 * x * y * slope + 2 * p1 * x + 2 * p2 * y
                j22 = radial + 2 * y * y * slope + 6 * p1 * y + 2 * p2 * x
                determinant = j11 * j22 - j12 * j12
                if not np.isfinite(determinant).all() or np.any(np.abs(determinant) < 1e-14):
                    raise ValueError("Distortion inverse has a singular Jacobian")
                error = self.distort_normalized(estimate) - target
                step = np.stack(
                    (
                        (j22 * error[..., 0] - j12 * error[..., 1]) / determinant,
                        (j11 * error[..., 1] - j12 * error[..., 0]) / determinant,
                    ),
                    axis=-1,
                )
                estimate -= step
                if np.max(np.abs(step)) < 1e-12:
                    break
            if (
                not np.isfinite(estimate).all()
                or np.max(np.abs(self.distort_normalized(estimate) - target)) > 1e-10
            ):
                raise ValueError("Distortion inversion did not converge")
        return estimate if normalized else self.pixels_from_normalized(estimate)

    def project(
        self, P_w: ArrayLike, extrinsics: CameraExtrinsics | None = None, *, distort: bool = True
    ) -> NDArray[np.float64]:
        P_c = points_array(P_w, 3, "P_w") if extrinsics is None else extrinsics.world_to_camera(P_w)
        if np.any(P_c[..., 2] <= 0):
            raise ValueError("Projection requires points strictly in front of the camera (+Z)")
        normalized = P_c[..., :2] / P_c[..., 2:]
        if distort:
            normalized = self.distort_normalized(normalized)
        return self.pixels_from_normalized(normalized)

    def undistort_image(self, image: NDArray) -> NDArray:
        """Inverse-map ideal output pixels to distorted input; preserve image size/K."""
        if image.ndim not in (2, 3) or image.shape[:2] != (self.image_height, self.image_width):
            raise ValueError("Image dimensions must match the stored-pixel calibration")
        # Tile construction limits temporary float64 arrays for full-resolution phone JPEGs.
        map_x = np.empty((self.image_height, self.image_width), dtype=np.float32)
        map_y = np.empty_like(map_x)
        for first in range(0, self.image_height, 128):
            last = min(first + 128, self.image_height)
            columns, rows = np.broadcast_arrays(
                np.arange(self.image_width)[None, :], np.arange(first, last)[:, None]
            )
            mapped = self.distort_points(np.stack((columns, rows), axis=-1))
            map_x[first:last], map_y[first:last] = mapped[..., 0], mapped[..., 1]
        return cv2.remap(image, map_x, map_y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)

    def without_distortion(self) -> CameraIntrinsics:
        return replace(self, distortion=(0, 0, 0, 0, 0))

    def to_dict(self) -> dict[str, Any]:
        return {
            "image_width": self.image_width,
            "image_height": self.image_height,
            "camera_matrix": self.K.tolist(),
            "distortion": list(self.distortion),
            "source": self.source,
            "approximate": self.approximate,
            "pixel_convention": PIXEL_CONVENTION,
            "camera_id": self.camera_id,
            "assumptions": list(self.assumptions),
        }


def load_intrinsics(path: str | Path) -> CameraIntrinsics:
    path = Path(path)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Expected a YAML mapping")
        if data.get("pixel_convention", PIXEL_CONVENTION) != PIXEL_CONVENTION:
            raise ValueError("Calibration must describe stored pixels before EXIF orientation")
        result = CameraIntrinsics.from_K(
            data["camera_matrix"],
            (data["image_width"], data["image_height"]),
            distortion=data.get("distortion", ()),
            source=data.get("source", "calibrated"),
            camera_id=data.get("camera_id"),
            assumptions=data.get("assumptions", ()),
        )
        if "approximate" in data and data["approximate"] is not result.approximate:
            raise ValueError("approximate flag contradicts intrinsic source")
        return result
    except (OSError, yaml.YAMLError, KeyError, TypeError, ValueError) as error:
        raise ValueError(f"{path}: invalid calibration: {error}") from error


def save_intrinsics(
    path: str | Path, intrinsics: CameraIntrinsics, *, calibration: dict[str, Any] | None = None
) -> Path:
    data = intrinsics.to_dict()
    if calibration is not None:
        data["calibration"] = calibration
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


def resolve_intrinsics(
    metadata: ImageMetadata,
    *,
    calibration_path: str | Path | None = None,
    image_size: tuple[int, int] | None = None,
    heuristic_fov_degrees: float = 60,
) -> CameraIntrinsics:
    """Prefer a matching calibration; otherwise expose metadata/heuristic assumptions."""
    size = image_size or (metadata.width, metadata.height)
    if len(size) != 2 or any(type(value) is not int or value <= 0 for value in size):
        raise ValueError("Stored image width and height are required to determine intrinsics")
    width, height = size
    if (metadata.width is not None and width != metadata.width) or (
        metadata.height is not None and height != metadata.height
    ):
        raise ValueError("Decoded image size disagrees with metadata")
    if calibration_path is not None:
        result = load_intrinsics(calibration_path)
        if result.image_size != size:
            raise ValueError(
                "Calibration image size differs; do not silently resize or EXIF-rotate K"
            )
        if (
            result.camera_id is not None
            and metadata.camera_id is not None
            and result.camera_id != metadata.camera_id
        ):
            raise ValueError("Calibration camera_id differs from the captured camera")
        return result
    cx, cy = (width - 1) / 2, (height - 1) / 2  # Zero-based pixel centers; lecture x0/y0.
    if metadata.focal_length_mm and metadata.sensor_width_mm and metadata.sensor_height_mm:
        sensor_width, sensor_height = metadata.sensor_width_mm, metadata.sensor_height_mm
        # An explicit central-crop assumption; logical sensor size does not reveal
        # stabilization/digital zoom or the active physical lens's actual crop.
        if width / height > sensor_width / sensor_height:
            sensor_height = sensor_width * height / width
        else:
            sensor_width = sensor_height * width / height
        fx = metadata.focal_length_mm * width / sensor_width
        fy = metadata.focal_length_mm * height / sensor_height
        assumptions = (
            "Physical focal length and logical sensor size with centered aspect crop",
            "Principal point at image center; zero skew and unknown distortion set to zero",
            "Active lens crop, zoom, and stabilization are not calibrated",
        )
    elif metadata.focal_length_35mm:
        fx = fy = metadata.focal_length_35mm * math.hypot(width, height) / math.hypot(36, 24)
        assumptions = (
            "35mm focal equivalent interpreted as diagonal full-frame field of view",
            "Square pixels, centered principal point, zero skew/distortion assumed",
        )
    else:
        if not math.isfinite(heuristic_fov_degrees) or not 0 < heuristic_fov_degrees < 180:
            raise ValueError("Heuristic horizontal field of view must be between 0 and 180 degrees")
        fx = fy = width / (2 * math.tan(math.radians(heuristic_fov_degrees) / 2))
        return CameraIntrinsics(
            fx,
            fy,
            cx,
            cy,
            width,
            height,
            source="heuristic",
            camera_id=metadata.camera_id,
            assumptions=(
                f"Assumed horizontal field of view {heuristic_fov_degrees:g} degrees",
                "Square pixels, centered principal point, zero skew/distortion assumed",
            ),
        )
    return CameraIntrinsics(
        fx,
        fy,
        cx,
        cy,
        width,
        height,
        source="metadata-derived",
        camera_id=metadata.camera_id,
        assumptions=assumptions,
    )
