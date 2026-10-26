import cv2
import numpy as np
import pytest
from numpy.testing import assert_array_equal
from scipy.spatial.distance import cdist, pdist

from distance_measuring.vision.tracking import (
    TemporalTracker,
    TrackingConfig,
    grid_occupancy,
    select_track_points,
    track_points,
)


def texture():
    return cv2.GaussianBlur(
        np.random.default_rng(67).integers(0, 256, (240, 360), dtype=np.uint8), (3, 3), 0
    )


def test_seeds_are_spatial_deterministic_and_retain_quality_without_mutating_image():
    image = texture()
    before = image.copy()
    config = TrackingConfig(max_points=96, grid_rows=4, grid_cols=6, min_distance_px=7)
    first, second = select_track_points(image, config), select_track_points(image, config)
    assert len(first.points) == 96
    assert_array_equal(grid_occupancy(first.points, image.shape, config), np.full(24, 4))
    assert np.all(first.response > 0)
    assert first.response.shape == (96,)
    assert pdist(first.points).min() >= 7
    assert_array_equal(first.points, second.points)
    assert_array_equal(first.response, second.response)
    assert_array_equal(image, before)
    assert first.runtime_s > 0


def test_high_texture_tile_does_not_steal_other_tiles_quotas():
    image = np.zeros((240, 360), np.uint8)
    image[:120, :180] = texture()[:120, :180]
    config = TrackingConfig(max_points=80, grid_rows=2, grid_cols=2)
    seeds = select_track_points(image, config)
    assert 0 < len(seeds.points) <= 20
    assert_array_equal(grid_occupancy(seeds.points, image.shape, config)[1:], [0, 0, 0])


def test_existing_points_and_tile_edges_obey_global_spacing():
    image = texture()
    active = np.array([[179.5, 119.5], [10.25, 10.25]])
    config = TrackingConfig(max_points=100, grid_rows=2, grid_cols=2, min_distance_px=12)
    seeds = select_track_points(image, config, active_points=active)
    assert len(seeds.points) + len(active) <= 100
    assert cdist(active, seeds.points).min() >= 12
    assert pdist(seeds.points).min() >= 12


def test_full_tiles_are_not_redetected(monkeypatch):
    config = TrackingConfig(max_points=4, grid_rows=2, grid_cols=2)
    active = np.array([[30, 30], [210, 30], [30, 150], [210, 150]])

    def forbidden(*args, **kwargs):
        pytest.fail("A full grid must not invoke detection")

    monkeypatch.setattr(cv2, "goodFeaturesToTrackWithQuality", forbidden)
    seeds = select_track_points(texture(), config, active_points=active)
    assert seeds.points.shape == (0, 2)
    assert seeds.response.shape == (0,)
    assert seeds.visited_cells == ()


def test_blank_and_bgr_inputs():
    image = texture()
    assert_array_equal(
        select_track_points(image).points,
        select_track_points(cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)).points,
    )
    blank = select_track_points(np.zeros_like(image))
    assert blank.points.shape == (0, 2)
    assert blank.response.shape == (0,)


@pytest.mark.parametrize(
    "options",
    [
        {"max_points": 0},
        {"max_points": True},
        {"grid_rows": 0},
        {"min_distance_px": float("nan")},
        {"quality_level": 0},
        {"lk_window_px": 4},
        {"lk_max_level": -1},
        {"fb_threshold_px": 0},
        {"replenish_ratio": 1.1},
        {"initialize_motion": 1},
        {"motion_max_features": 7},
        {"motion_max_image_size": 1},
        {"motion_retry_ratio": 0},
    ],
)
def test_invalid_seed_options(options):
    with pytest.raises(ValueError):
        TrackingConfig(**options)


@pytest.mark.parametrize(
    "image", [np.zeros((2, 2)), np.zeros((0, 3), np.uint8), np.zeros((20, 20, 4), np.uint8)]
)
def test_invalid_images(image):
    with pytest.raises(ValueError):
        select_track_points(image)


def test_grid_and_active_point_validation():
    with pytest.raises(ValueError, match="Grid"):
        select_track_points(texture(), TrackingConfig(grid_cols=361))
    for active in ([[1, 2, 3]], [[360, 0]], [[float("nan"), 0]]):
        with pytest.raises(ValueError):
            select_track_points(texture(), active_points=active)


def translated(image, x, y):
    return cv2.warpAffine(image, np.array([[1.0, 0, x], [0, 1.0, y]]), image.shape[::-1])


def motion_texture():
    # Resolved texture at coarse pyramid levels, unlike near-Nyquist pixel noise.
    return cv2.resize(
        np.random.default_rng(5).integers(0, 256, (60, 90), dtype=np.uint8),
        (360, 240),
        interpolation=cv2.INTER_CUBIC,
    )


def test_lk_recovers_known_translation_with_finite_status_and_patch_error():
    image = motion_texture()
    points = select_track_points(image, TrackingConfig(max_points=120)).points
    result = track_points(image, translated(image, 5, -3), points)
    assert result.accepted.sum() > 0.9 * len(points)
    error = np.linalg.norm(
        result.points[result.accepted] - points[result.accepted] - [5, -3], axis=1
    )
    assert np.median(error) < 0.05
    assert np.quantile(error, 0.9) < 0.2
    assert np.isfinite(result.lk_error[result.accepted]).all()
    assert result.runtime_s > 0


def test_temporal_ids_ages_and_detector_quality_persist_across_frames():
    image = motion_texture()
    tracker = TemporalTracker(TrackingConfig(max_points=120))
    first = tracker.process(image, 4)
    assert all(o.age == 1 and o.status == "seeded" for o in first.observations)
    original = {o.track_id: o for o in tracker.active}
    for frame_id, dx in ((7, 2), (9, 4), (10, 6)):
        frame = tracker.process(translated(image, dx, 0), frame_id)
        assert frame.flow.runtime_s > 0
    assert len(tracker.active) > 0.9 * len(original)
    for o in tracker.active:
        assert o.age == 4 and o.status == "tracked"
        assert o.response == original[o.track_id].response
        # Motion accuracy is checked where the full LK patch stays in the image.
        # Border tracks have less symmetric support even if their round trip passes.
        pixel = np.asarray(o.pixel)
        if (pixel > 31).all() and (pixel < [329, 209]).all():
            assert np.linalg.norm(pixel - original[o.track_id].pixel - [6, 0]) < 0.3


def test_empty_tracks_skip_opencv_and_shape_or_order_failures_preserve_state(monkeypatch):
    tracker = TemporalTracker()
    image = np.zeros((240, 360), np.uint8)
    tracker.process(image, 2)

    def forbidden(*args, **kwargs):
        pytest.fail("Empty tracks must not call LK")

    monkeypatch.setattr(cv2, "calcOpticalFlowPyrLK", forbidden)
    assert tracker.process(image, 4).flow.points.shape == (0, 2)
    for bad_image, bad_id in ((image, 4), (image, True), (image[:100], 5)):
        with pytest.raises(ValueError):
            tracker.process(bad_image, bad_id)
        assert tracker.previous_id == 4


def test_forward_backward_filter_keeps_row_alignment_and_reports_distinct_failures(monkeypatch):
    points = np.array([[20, 20], [40, 40], [60, 60], [80, 80], [100, 100]], float)
    target = points + [2, 0]
    target[3] = [400, 80]
    calls = []

    def fake_lk(first, second, pixels, initial, **options):
        calls.append(pixels.copy())
        if len(calls) == 1:
            return (
                target.astype(np.float32).reshape(-1, 1, 2),
                np.array([1, 1, 0, 1, 1], np.uint8),
                np.zeros(5),
            )
        assert_array_equal(pixels.reshape(-1, 2), target[[0, 1, 4]])
        return (
            np.array([[20.2, 20], [45, 40], [100, 100]], np.float32).reshape(-1, 1, 2),
            np.array([1, 1, 0], np.uint8),
            None,
        )

    monkeypatch.setattr(cv2, "calcOpticalFlowPyrLK", fake_lk)
    result = track_points(texture(), texture(), points)
    assert_array_equal(result.accepted, [True, False, False, False, False])
    assert result.rejection_reason == (
        None,
        "forward_backward_inconsistent",
        "forward_failed",
        "outside_image",
        "backward_failed",
    )
    assert result.forward_backward_error_px[0] == pytest.approx(0.2, abs=1e-5)
    assert np.isnan(result.lk_error[2])
    assert np.isnan(result.forward_backward_error_px[4])
    assert result.forward_runtime_s > 0 and result.backward_runtime_s > 0


def test_backward_out_of_bounds_is_rejected_even_with_loose_roundtrip_threshold(monkeypatch):
    calls = 0

    def fake_lk(*args, **kwargs):
        nonlocal calls
        calls += 1
        point = [21, 20] if calls == 1 else [-1, 20]
        return np.array(point, np.float32).reshape(1, 1, 2), np.ones(1, np.uint8), np.zeros(1)

    monkeypatch.setattr(cv2, "calcOpticalFlowPyrLK", fake_lk)
    result = track_points(texture(), texture(), [[20, 20]], TrackingConfig(fb_threshold_px=100))
    assert not result.accepted[0]
    assert result.rejection_reason == ("backward_outside_image",)


def test_invalid_forward_coordinates_never_enter_reverse_flow(monkeypatch):
    calls = 0

    def fake_lk(*args, **kwargs):
        nonlocal calls
        calls += 1
        return np.full((1, 1, 2), np.nan, np.float32), np.ones(1, np.uint8), np.zeros(1)

    monkeypatch.setattr(cv2, "calcOpticalFlowPyrLK", fake_lk)
    result = track_points(texture(), texture(), [[20, 20]])
    assert calls == 1 and not result.accepted[0]
    assert result.rejection_reason == ("forward_failed",)


def test_replenishment_preserves_survivors_and_adds_only_new_ids_in_deficient_tiles():
    image = motion_texture()
    config = TrackingConfig(max_points=120)
    tracker = TemporalTracker(config)
    tracker.process(image, 0)
    original = {o.track_id: o for o in tracker.active}
    # Emulate earlier losses confined to the left two columns of tiles.
    tracker.active = tuple(o for o in tracker.active if o.pixel[0] >= 120)
    survivors = {o.track_id for o in tracker.active}
    frame = tracker.process(image, 1)
    assert frame.selection is not None and frame.selection.visited_cells
    assert all(cell % config.grid_cols < 3 for cell in frame.selection.visited_cells)
    new = [o for o in tracker.active if o.status == "seeded"]
    assert new and min(o.track_id for o in new) >= len(original)
    assert all(o.age == 1 for o in new)
    continued = [o for o in tracker.active if o.status == "tracked"]
    assert {o.track_id for o in continued} == survivors
    assert all(o.age == 2 and o.response == original[o.track_id].response for o in continued)
    assert len(tracker.active) >= 0.9 * config.max_points
    assert (
        cdist(np.array([o.pixel for o in new]), np.array([o.pixel for o in continued])).min()
        >= config.min_distance_px
    )


def test_long_sequence_replenishes_without_resetting_track_ids(monkeypatch):
    tracker = TemporalTracker(TrackingConfig(max_points=120, replenish_ratio=0.85))
    image = motion_texture()
    tracker.process(image, 0)
    protected = {o.track_id for o in tracker.active if o.pixel[0] > 200}
    first_new_id = tracker.next_track_id
    new_ids = []
    for frame_id in range(1, 13):
        if frame_id % 3 == 0:
            tracker.active = tuple(
                o for o in tracker.active if o.track_id in protected or o.track_id % 3 == 0
            )
        frame = tracker.process(image, frame_id)
        new_ids.extend(o.track_id for o in frame.observations if o.status == "seeded")
        assert 0.85 * 120 <= len(tracker.active) <= 120
        assert protected <= {o.track_id for o in tracker.active}
    assert new_ids and len(set(new_ids)) == len(new_ids)
    assert min(new_ids) >= first_new_id
    assert all(o.age == 13 for o in tracker.active if o.track_id in protected)


def test_enough_tracks_skip_seed_detection(monkeypatch):
    tracker = TemporalTracker(TrackingConfig(max_points=120))
    image = motion_texture()
    tracker.process(image, 0)

    def forbidden(*args, **kwargs):
        pytest.fail("Healthy active budget must skip detection")

    monkeypatch.setattr(cv2, "goodFeaturesToTrackWithQuality", forbidden)
    assert tracker.process(image, 1).selection is None


def test_optional_initializer_recovers_known_large_rotation_in_original_pixel_frame():
    image = motion_texture()
    config = TrackingConfig(max_points=120, initialize_motion=True, motion_max_image_size=240)
    points = select_track_points(image, config).points
    matrix = cv2.getRotationMatrix2D((180, 120), 75, 1)
    moved = cv2.warpAffine(image, matrix, image.shape[::-1])
    result = track_points(image, moved, points, config)
    assert result.motion_initialization["success"]
    assert result.motion_initialization["guided_retry_selected"]
    assert max(result.motion_initialization["feature_counts"]) <= 600
    assert result.motion_initialization["descriptor_distance_upper_bound"] <= 720000
    assert result.accepted.sum() > 40
    expected = np.c_[points, np.ones(len(points))] @ matrix.T
    error = np.linalg.norm(result.points[result.accepted] - expected[result.accepted], axis=1)
    assert np.median(error) < 0.15
    assert np.quantile(error, 0.9) < 0.5
    assert np.max(result.forward_backward_error_px[result.accepted]) <= config.fb_threshold_px


def test_healthy_temporal_flow_does_not_invoke_optional_descriptor_initializer(monkeypatch):
    import distance_measuring.vision.tracking as tracking

    def forbidden(*args, **kwargs):
        pytest.fail("Healthy temporal flow must not extract descriptors")

    monkeypatch.setattr(tracking, "_motion_initialization", forbidden)
    config = TrackingConfig(max_points=120, initialize_motion=True)
    image = motion_texture()
    points = select_track_points(image, config).points
    assert track_points(image, image, points, config).motion_initialization is None


def test_failed_descriptor_initialization_preserves_the_rejected_flow_result():
    image = np.zeros((240, 360), np.uint8)
    result = track_points(image, image, [[30, 30]], TrackingConfig(initialize_motion=True))
    assert not result.accepted.any()
    assert not result.motion_initialization["success"]
    assert not result.motion_initialization["guided_retry_selected"]
    assert result.motion_initialization["feature_counts"] == [0, 0]
