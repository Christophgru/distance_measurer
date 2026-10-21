from dataclasses import replace

import cv2
import numpy as np
import pytest
from numpy.testing import assert_allclose

from distance_measuring.camera import (
    CameraIntrinsics,
    load_intrinsics,
    resolve_intrinsics,
    save_intrinsics,
)
from distance_measuring.dataset.schema import ImageMetadata


def test_K_normalization_and_skew():
    camera = CameraIntrinsics(800, 810, 320, 240, 640, 480, skew=3)
    pixels = [[123.0, 456.0], [320, 240]]
    assert_allclose(camera.pixels_from_normalized(camera.normalize_pixels(pixels)), pixels)
    assert_allclose(CameraIntrinsics.from_K(camera.K * 7, camera.image_size).K, camera.K)


@pytest.mark.parametrize(
    "field,value",
    [
        ("fx", 0),
        ("fy", -1),
        ("cx", float("nan")),
        ("cy", float("inf")),
        ("skew", True),
        ("image_width", 0),
        ("image_height", 1.5),
        ("distortion", [1, 2, 3]),
        ("source", "invented"),
        ("camera_id", 3),
        ("fx", 1j),
        ("assumptions", "not a sequence of assumption strings"),
    ],
)
def test_invalid_intrinsics(field, value):
    values = dict(fx=800, fy=810, cx=320, cy=240, image_width=640, image_height=480)
    values[field] = value
    with pytest.raises(ValueError):
        CameraIntrinsics(**values)


def test_distortion_matches_independent_opencv_model():
    camera = CameraIntrinsics(
        800, 810, 320, 240, 640, 480, distortion=(-0.15, 0.03, 0.002, -0.001, 0.004)
    )
    points = np.array([[0.1, -0.2, 1.0], [-0.3, 0.4, 1.2], [0.2, 0.3, 2.0]])
    expected, _ = cv2.projectPoints(
        points, np.zeros(3), np.zeros(3), camera.K, np.array(camera.distortion)
    )
    assert_allclose(camera.project(points), expected.reshape(-1, 2), atol=1e-10)


def test_point_undistortion_round_trip_sub_micro_pixel_with_skew():
    camera = CameraIntrinsics(
        1400, 1380, 640, 480, 1280, 960, skew=2.0, distortion=(-0.18, 0.04, 0.001, -0.002, 0.005)
    )
    rng = np.random.default_rng(42)
    rays = rng.uniform(-0.4, 0.4, (200, 2))
    ideal = camera.pixels_from_normalized(rays)
    distorted = camera.distort_points(ideal)
    assert np.max(np.abs(camera.undistort_points(distorted) - ideal)) < 1e-6
    assert_allclose(camera.undistort_points(distorted, normalized=True), rays, atol=1e-10)
    assert camera.undistort_points(np.empty((0, 2))).shape == (0, 2)


def test_image_undistortion_matches_opencv():
    camera = CameraIntrinsics(
        150, 152, 79.5, 59.5, 160, 120, distortion=(-0.1, 0.02, 0.001, -0.001, 0)
    )
    image = np.random.default_rng(3).integers(0, 256, (120, 160, 3), dtype=np.uint8)
    # Compare the same bilinear remap representation. cv2.undistort may use a
    # different interpolation implementation across OpenCV versions.
    map_x, map_y = cv2.initUndistortRectifyMap(
        camera.K, np.array(camera.distortion), None, camera.K, camera.image_size, cv2.CV_32FC1
    )
    columns, rows = np.meshgrid(np.arange(160), np.arange(120))
    mapped = camera.distort_points(np.stack((columns, rows), axis=-1))
    assert_allclose(mapped[..., 0], map_x, atol=1e-5)
    assert_allclose(mapped[..., 1], map_y, atol=1e-5)
    expected = cv2.remap(image, map_x, map_y, cv2.INTER_LINEAR)
    actual = camera.undistort_image(image)
    assert_allclose(actual, expected, atol=1)
    assert np.array_equal(camera.without_distortion().undistort_image(image), image)
    with pytest.raises(ValueError, match="dimensions"):
        camera.undistort_image(image[:50])


def test_singular_distortion_inverse_is_explicit():
    camera = CameraIntrinsics(1, 1, 0, 0, 10, 10, distortion=(-1, 0, 0, 0, 0))
    with pytest.raises(ValueError, match="singular"):
        camera.undistort_points([[np.sqrt(1 / 3), 0]])


def test_yaml_roundtrip_and_source_provenance(tmp_path):
    camera = CameraIntrinsics(
        800,
        810,
        320,
        240,
        640,
        480,
        skew=2.0,
        distortion=(-0.1, 0.01, 0.001, 0, 0),
        camera_id="0",
        source="metadata-derived",
        assumptions=("Test approximation",),
    )
    path = save_intrinsics(tmp_path / "camera.yaml", camera)
    loaded = load_intrinsics(path)
    assert loaded == camera
    assert loaded.approximate


def test_invalid_calibration_never_silently_falls_back(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("camera_matrix: nope")
    with pytest.raises(ValueError, match="invalid calibration"):
        resolve_intrinsics(ImageMetadata(width=640, height=480), calibration_path=path)


def test_calibrated_profile_checks_size_and_camera_id(tmp_path):
    camera = CameraIntrinsics(800, 810, 320, 240, 640, 480, camera_id="0")
    path = save_intrinsics(tmp_path / "camera.yaml", camera)
    metadata = ImageMetadata(width=640, height=480, camera_id="0")
    resolved = resolve_intrinsics(metadata, calibration_path=path)
    assert resolved.source == "calibrated"
    assert not resolved.approximate
    with pytest.raises(ValueError, match="image size"):
        resolve_intrinsics(replace(metadata, width=1280), calibration_path=path)
    with pytest.raises(ValueError, match="camera_id"):
        resolve_intrinsics(replace(metadata, camera_id="1"), calibration_path=path)


def test_physical_metadata_derivation_and_aspect_crop():
    metadata = ImageMetadata(
        width=1200,
        height=900,
        focal_length_mm=4.0,
        sensor_width_mm=8.0,
        sensor_height_mm=4.0,
        camera_id="0",
    )
    camera = resolve_intrinsics(metadata)
    assert camera.source == "metadata-derived"
    assert camera.approximate
    assert_allclose([camera.fx, camera.fy, camera.cx, camera.cy], [900, 900, 599.5, 449.5])
    assert "crop" in " ".join(camera.assumptions).lower()


def test_35mm_equivalent_and_heuristic_are_approximate():
    metadata = ImageMetadata(width=1200, height=900, focal_length_35mm=24.0)
    camera = resolve_intrinsics(metadata)
    assert_allclose(camera.fx, 24 * 1500 / np.hypot(36, 24))
    assert camera.source == "metadata-derived"
    heuristic = resolve_intrinsics(ImageMetadata(width=1200, height=900))
    assert heuristic.source == "heuristic"
    assert heuristic.approximate
    assert_allclose(heuristic.fx, 1200 / (2 * np.tan(np.pi / 6)))


@pytest.mark.parametrize("fov", [0, 180, float("nan")])
def test_heuristic_fov_must_be_valid(fov):
    with pytest.raises(ValueError, match="field of view"):
        resolve_intrinsics(ImageMetadata(width=640, height=480), heuristic_fov_degrees=fov)


def test_intrinsics_require_image_size():
    with pytest.raises(ValueError, match="width and height"):
        resolve_intrinsics(ImageMetadata())
    camera = resolve_intrinsics(ImageMetadata(), image_size=(640, 480))
    assert camera.image_size == (640, 480)
