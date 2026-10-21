import cv2
import numpy as np
import pytest
from numpy.testing import assert_allclose, assert_array_equal

from distance_measuring.camera import CameraIntrinsics, from_homogeneous, to_homogeneous
from distance_measuring.vision import SiftBackend, match_descriptors
from distance_measuring.vision.pipeline import working_camera


def texture():
    return cv2.GaussianBlur(
        np.random.default_rng(14).integers(0, 256, (320, 480), np.uint8), (3, 3), 0
    )


def test_sift_rows_are_aligned_and_repeatable():
    backend = SiftBackend(max_features=180)
    first, second = backend.extract(texture()), backend.extract(texture())
    assert 50 < len(first) <= 180
    assert first.descriptors.shape == (len(first), 128)
    assert first.descriptors.dtype == np.float32
    assert_array_equal(first.points, second.points)
    assert_array_equal(first.descriptors, second.descriptors)
    assert (
        len(first.response)
        == len(first.size)
        == len(first.angle)
        == len(first.octave)
        == len(first)
    )
    assert np.all(np.diff(first.response) <= 0)
    assert np.all(first.size > 0)
    assert np.all((first.points >= 0) & (first.points < [480, 320]))
    matches = match_descriptors(first.descriptors, second.descriptors)
    assert len(matches) > 0.95 * len(first)
    assert_array_equal(matches.indices[:, 0], matches.indices[:, 1])


def test_grayscale_and_bgr_features_agree():
    image = texture()
    backend = SiftBackend(max_features=50)
    assert_array_equal(
        backend.extract(image).descriptors,
        backend.extract(cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)).descriptors,
    )


def test_blank_image_has_shaped_empty_arrays():
    features = SiftBackend().extract(np.zeros((80, 100), np.uint8))
    assert features.points.shape == (0, 2)
    assert features.descriptors.shape == (0, 128)
    assert len(features) == 0


@pytest.mark.parametrize(
    "image",
    [np.zeros((30, 30), float), np.zeros((0, 0), np.uint8), np.zeros((20, 20, 4), np.uint8)],
)
def test_invalid_feature_input(image):
    with pytest.raises(ValueError):
        SiftBackend().extract(image)


@pytest.mark.parametrize(
    "options", [{"max_features": 0}, {"max_features": True}, {"contrast_threshold": float("nan")}]
)
def test_invalid_sift_configuration(options):
    with pytest.raises(ValueError):
        SiftBackend(**options)


def test_ratio_counts_and_optional_mutual_filter():
    # Both query descriptors choose train 0, but only query 0 is its nearest.
    first, second = [[0.0], [0.2], [9.0]], [[0.0], [10.0], [11.0]]
    forward = match_descriptors(first, second, mutual=False)
    mutual = match_descriptors(first, second)
    assert forward.raw_count == forward.two_neighbor_count == 3
    assert forward.ratio_count == 3
    assert_array_equal(forward.indices, [[0, 0], [1, 0], [2, 1]])
    assert_array_equal(mutual.indices, [[0, 0], [2, 1]])
    assert mutual.ratio_count == 3
    assert_allclose(mutual.distances, [0, 1])
    assert_allclose(mutual.ratios, [0, 0.5])


def test_ambiguous_ties_and_missing_second_neighbor_are_rejected():
    assert len(match_descriptors([[0]], [[0], [0]])) == 0
    singleton = match_descriptors([[0], [1]], [[0]])
    assert singleton.raw_count == 2
    assert singleton.two_neighbor_count == singleton.ratio_count == len(singleton) == 0


@pytest.mark.parametrize("n,m", [(0, 0), (0, 3), (3, 0)])
def test_empty_descriptors(n, m):
    result = match_descriptors(np.zeros((n, 128)), np.zeros((m, 128)))
    assert result.indices.shape == (0, 2)
    assert result.raw_count == 0


@pytest.mark.parametrize("ratio", [0, 1, -0.1, float("nan")])
def test_invalid_ratio(ratio):
    with pytest.raises(ValueError, match="ratio"):
        match_descriptors([[0]], [[0], [1]], ratio=ratio)


@pytest.mark.parametrize(
    "first,second",
    [([0], [[0]]), ([[np.nan]], [[0]]), ([[0, 1]], [[0]]), (np.empty((2, 0)), np.empty((1, 0)))],
)
def test_invalid_descriptor_shapes_and_values(first, second):
    with pytest.raises(ValueError, match="Descriptors"):
        match_descriptors(first, second)


def test_resizing_K_preserves_rays_and_distortion_with_pixel_center_offset():
    camera = CameraIntrinsics(
        900, 870, 490, 355, 1000, 720, skew=2.5, distortion=(-0.1, 0.02, 0.001, -0.003, 0)
    )
    resized, A = working_camera(camera, (701, 505))
    points = np.array([[2, 1, 7], [-1, -1, 5], [0, 0, 4]])
    assert_allclose(resized.K, A @ camera.K)
    assert resized.distortion == camera.distortion
    pixels = camera.project(points)
    expected = from_homogeneous(to_homogeneous(pixels) @ A.T)
    assert_allclose(resized.project(points), expected, atol=1e-10)
    assert_allclose(
        resized.undistort_points(expected, normalized=True),
        points[:, :2] / points[:, 2:],
        atol=1e-9,
    )
