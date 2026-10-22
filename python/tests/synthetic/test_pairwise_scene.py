import copy

import numpy as np
import pytest
from numpy.testing import assert_allclose
from scipy.spatial.transform import Rotation

from distance_measuring.camera import CameraExtrinsics, CameraIntrinsics
from distance_measuring.optimization.bundle import reprojection_residuals
from distance_measuring.vision.pairwise_scene import rebuild_pairwise_scene


def audit_fixture():
    K = CameraIntrinsics(850, 850, 639.5, 479.5, 1280, 960)
    points = np.random.default_rng(19).uniform([-0.7, -0.6, 4], [1.2, 0.8, 8], (60, 3))
    centers = np.array([[0, 0, 0], [0.6, 0.02, 0.01], [1.1, 0.05, 0.04]])
    rotations = Rotation.from_rotvec(
        [[0, 0, 0], [0.01, 0.06, 0.005], [0.02, 0.1, -0.01]]
    ).as_matrix()
    poses = [CameraExtrinsics(R, C) for R, C in zip(rotations, centers, strict=True)]
    pixels = [K.project(points, p, distort=False) for p in poses]
    rows = []
    # There are NO closed 0→1→2→0 triangles. Views overlap in pairs, and
    # the 0–2-only points 50..59 must survive with two observations.
    for (i, j), indices in [
        ((0, 1), range(20)),
        ((0, 2), range(20, 40)),
        ((1, 2), range(50)),
        ((0, 2), range(50, 60)),
    ]:
        for k in indices:
            groups = [None] * 3
            groups[i] = groups[j] = k
            xy = [None] * 3
            xy[i] = pixels[i][k].tolist()
            xy[j] = pixels[j][k].tolist()
            rows.append(
                {
                    "usable_pairwise": True,
                    "group_ids": groups,
                    "working_pixels": xy,
                    "correlation": {"score": 0.97},
                }
            )
    return (
        {
            "image_ids": [0, 1, 2],
            "images": [{"image_id": i, "working_intrinsics": K.to_dict()} for i in range(3)],
            "pairwise_matches": rows,
            "session_id": "original_synthetic",
            "review_id": "synthetic_review",
        },
        points,
        poses,
    )


def test_incremental_scene_uses_pair_overlap_without_requiring_three_view_cycles():
    audit, points, truth = audit_fixture()
    before = copy.deepcopy(audit)
    result = rebuild_pairwise_scene(audit)
    assert audit == before
    assert result.report["seed_image_ids"] == [1, 2]
    assert len(result.scene.points) == 60
    assert result.report["pnp_inliers"] >= 35
    assert len(reprojection_residuals(result.scene)) > 0
    assert_allclose(reprojection_residuals(result.scene), 0, atol=1e-3)
    baseline = np.linalg.norm(truth[2].C_w - truth[1].C_w)
    by_track = {t.track_id: t for t in result.scene.tracks}
    for point in result.scene.points:
        track = by_track[point.track_id]
        source_id = track.observations[0].feature_id
        expected = truth[1].world_to_camera(points[source_id]) / baseline
        assert_allclose(point.position, expected, atol=1e-4)
        if source_id >= 50:
            assert len(track.observations) == 2
    assert result.scene.metadata["scale_status"] == "arbitrary_visual_scale_unverified"


def test_rebuilding_requires_consistent_coordinates_for_shared_feature_groups():
    audit, _, _ = audit_fixture()
    row = next(r for r in audit["pairwise_matches"] if r["group_ids"][0] == 0)
    row["working_pixels"][1][0] += 2
    with pytest.raises(ValueError, match="inconsistent pixel coordinates"):
        rebuild_pairwise_scene(audit)


def test_missing_overlap_for_remaining_camera_stops_connected_reconstruction():
    audit, _, _ = audit_fixture()
    audit["pairwise_matches"] = [
        r for r in audit["pairwise_matches"] if r["group_ids"][0] is None or r["group_ids"][0] < 2
    ]
    with pytest.raises(ValueError, match="Fewer than eight"):
        rebuild_pairwise_scene(audit)
