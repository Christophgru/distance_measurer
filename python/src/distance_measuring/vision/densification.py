"""Image-supported inspection points with frozen cameras; lecture 002 pp. 133–144.

Rectification, bidirectional semi-global stereo and photometric rejection extend
the lecture. This never adjusts K/R/C, applies scale, or duplicates input points
to meet a requested point count. Page numbers use one-based PPTX slide order.
"""

from dataclasses import replace
from itertools import combinations, zip_longest

import cv2
import numpy as np

from distance_measuring.scene import Observation, ScenePoint, SceneTrack
from distance_measuring.visualization.overlays import pose_rectification


def map_pixels(H, pixels):
    homogeneous = np.column_stack([pixels, np.ones(len(pixels))]) @ H.T
    return homogeneous[:, :2] / homogeneous[:, 2:]


def triangulate_batch(first, second, a, b):
    """Conditioned homogeneous SVD, then the normal 3 px / 1 degree gates."""
    a, b = np.asarray(a, float).reshape(-1, 2), np.asarray(b, float).reshape(-1, 2)
    if a.shape != b.shape or not np.isfinite([a, b]).all():
        raise ValueError("Stereo pixels must be aligned finite Nx2 arrays")
    if not len(a):
        return np.empty((0, 3)), np.zeros(0, bool), np.empty((0, 2)), np.zeros(0)
    origin = first.extrinsics.C_w
    unit = np.linalg.norm(second.extrinsics.C_w - origin)
    if unit < 1e-12:
        raise ValueError("Densification requires distinct camera centers")
    cameras = [first, second]
    # Lecture 002 pp. 70–76/81–83: K^-1 m and normalized [R|-RC] projections.
    projections = [
        np.c_[c.extrinsics.R_cw, c.extrinsics.R_cw @ ((origin - c.extrinsics.C_w) / unit)]
        for c in cameras
    ]
    # pp. 65–68, 98/101, 133–136: homogeneous least squares. OpenCV uses SVD
    # for each 4-coordinate point, equivalent to the single-point DLT solver.
    ph = cv2.triangulatePoints(
        *projections,
        first.intrinsics.normalize_pixels(a).T,
        second.intrinsics.normalize_pixels(b).T,
    ).T
    valid = np.abs(ph[:, 3]) > 1e-12
    xyz = np.full((len(a), 3), np.nan)
    xyz[valid] = origin + unit * ph[valid, :3] / ph[valid, 3:]
    valid &= np.isfinite(xyz).all(axis=1)
    errors = np.full((len(a), 2), np.inf)
    for k, (c, pixels) in enumerate(zip(cameras, [a, b], strict=True)):
        local = (xyz - c.extrinsics.C_w) @ c.extrinsics.R_cw.T
        valid &= local[:, 2] > 0
        projected = local @ c.intrinsics.K.T
        with np.errstate(divide="ignore", invalid="ignore"):
            errors[:, k] = np.linalg.norm(projected[:, :2] / projected[:, 2:] - pixels, axis=1)
    rays = [xyz - c.extrinsics.C_w for c in cameras]
    with np.errstate(divide="ignore", invalid="ignore"):
        cosine = np.sum(rays[0] * rays[1], axis=1) / (
            np.linalg.norm(rays[0], axis=1) * np.linalg.norm(rays[1], axis=1)
        )
    angles = np.degrees(np.arccos(np.clip(np.abs(cosine), 0, 1)))
    valid &= np.isfinite(errors).all(axis=1) & (errors.max(axis=1) <= 3) & (angles >= 1)
    return xyz, valid, errors, angles


def stereo_candidates(
    first,
    second,
    image_a,
    image_b,
    mask_a,
    mask_b,
    *,
    seed_pixels,
    max_image_size=1600,
    sample_step=1,
):
    """Return checked real pixel correspondences, never a synthetic surface."""
    size = first.intrinsics.image_size
    if any(
        im.dtype != np.uint8 or im.shape != size[::-1] for im in [image_a, image_b, mask_a, mask_b]
    ):
        raise ValueError("Stereo images/masks must be uint8 in the saved working frame")
    H1, H2, direction = pose_rectification(first, second)
    scale = min(1, max_image_size / max(size))
    output = tuple(max(32, round(s * scale)) for s in size)
    # Lecture 002 pp. 65–76/138–140: one shared sampling transform preserves
    # epipolar scanlines. Convert accepted pixels back to the original K frame.
    S = np.array(
        [
            [output[0] / size[0], 0, (output[0] / size[0] - 1) / 2],
            [0, output[1] / size[1], (output[1] / size[1] - 1) / 2],
            [0, 0, 1],
        ]
    )
    H1, H2 = S @ H1, S @ H2
    if direction == "vertical":
        turn = np.array([[0, -1, output[1] - 1], [1, 0, 0], [0, 0, 1]])
        H1, H2 = turn @ H1, turn @ H2
        output = output[::-1]
    images = [cv2.warpPerspective(im, H, output) for im, H in [(image_a, H1), (image_b, H2)]]
    masks = [
        cv2.erode(
            cv2.warpPerspective(m, H, output, flags=cv2.INTER_NEAREST), np.ones((11, 11), np.uint8)
        )
        for m, H in [(mask_a, H1), (mask_b, H2)]
    ]
    pa, pb = [
        map_pixels(H, np.asarray(p, float)) for H, p in zip([H1, H2], seed_pixels, strict=True)
    ]
    disparity = pa[:, 0] - pb[:, 0]
    disparity = disparity[np.isfinite(disparity)]
    if len(disparity) < 8:
        raise ValueError("Stereo pair needs at least eight existing shared visual points")
    lo, hi = np.percentile(disparity, [1, 99])
    minimum = int(np.floor(lo - 16))
    number = max(32, int(np.ceil((hi - lo + 32) / 16)) * 16)
    if number > 512 or number >= output[0] - 16:
        raise ValueError("Stereo disparity range exceeds the bounded search viewport")

    def matcher(minimum):
        return cv2.StereoSGBM_create(
            minDisparity=minimum,
            numDisparities=number,
            blockSize=5,
            P1=8 * 25,
            P2=32 * 25,
            disp12MaxDiff=1,
            preFilterCap=31,
            uniquenessRatio=15,
            speckleWindowSize=100,
            speckleRange=2,
            mode=cv2.STEREO_SGBM_MODE_SGBM_3WAY,
        )

    left = matcher(minimum).compute(*images).astype(np.float32) / 16
    right_minimum = -minimum - number + 1
    right = matcher(right_minimum).compute(*images[::-1]).astype(np.float32) / 16
    yy, xx = np.indices(left.shape, dtype=np.float32)
    other_x = xx - left
    other_disparity = cv2.remap(right, other_x, yy, cv2.INTER_LINEAR, borderValue=right_minimum - 1)
    other_mask = cv2.remap(masks[1], other_x, yy, cv2.INTER_NEAREST)
    keep = (
        (left > minimum - 1)
        & (other_disparity > right_minimum - 1)
        & (np.abs(left + other_disparity) <= 0.75)
    )
    keep &= (masks[0] > 0) & (other_mask > 0) & (other_x >= 0) & (other_x < output[0] - 1)
    fb_count = int(keep.sum())
    # Lecture 002 pp. 141–144: normalized correlation and texture rejection.
    # Windows compare the first image with the disparity-warped second image.
    warped = cv2.remap(images[1].astype(np.float32), other_x, yy, cv2.INTER_LINEAR)
    a, b = images[0].astype(np.float32), warped

    def mean(x):
        return cv2.boxFilter(x, -1, (11, 11), normalize=True)

    ma, mb = mean(a), mean(b)
    va, vb = np.maximum(0, mean(a * a) - ma * ma), np.maximum(0, mean(b * b) - mb * mb)
    score = (mean(a * b) - ma * mb) / np.sqrt(np.maximum(va * vb, 1e-8))
    keep &= (score >= 0.85) & (va >= 36) & (vb >= 36)
    photometric_count = int(keep.sum())
    sampled = np.zeros_like(keep)
    sampled[::sample_step, ::sample_step] = True
    keep &= sampled
    rows, cols = np.nonzero(keep)
    original_a = map_pixels(np.linalg.inv(H1), np.column_stack([cols, rows]))
    original_b = map_pixels(np.linalg.inv(H2), np.column_stack([other_x[rows, cols], rows]))
    xyz, valid, errors, angles = triangulate_batch(first, second, original_a, original_b)
    # Distinct locations are interleaved across image tiles, instead of filling
    # the budget with a few highly textured patches. Lengths/IMU never rank them.
    cell = (rows // max(1, output[1] // 18)) * 24 + cols // max(1, output[0] // 24)
    order = np.lexsort((-score[rows, cols], cell))
    rank = np.zeros(len(order), int)
    last, within = None, 0
    for n in order:
        within = within + 1 if cell[n] == last else 0
        rank[n] = within
        last = cell[n]
    order = np.lexsort((cell, rank))
    order = order[valid[order]]
    return {
        "image_ids": [first.image_id, second.image_id],
        "pixels_a": original_a[order],
        "pixels_b": original_b[order],
        "positions": xyz[order],
        "errors": errors[order],
        "angles": angles[order],
        "report": {
            "image_ids": [first.image_id, second.image_id],
            "rectified_size": list(output),
            "min_disparity": minimum,
            "num_disparities": number,
            "left_right_and_masks_passed": fb_count,
            "photometric_passed": photometric_count,
            "sampled_candidates": len(rows),
            "triangulation_passed": len(order),
        },
    }


def densify_scene(scene, images, masks, *, target_points=20000, max_image_size=1600):
    """Append independently matched points; retain all original cameras/points/IDs."""
    if type(target_points) is not int or not len(scene.points) <= target_points <= 1_000_000:
        raise ValueError("Target must be an integer from the existing point count to 1,000,000")
    cameras = {c.image_id: c for c in scene.cameras}
    if len(cameras) < 2:
        raise ValueError("Densification needs at least two saved cameras")
    tracks = list(scene.tracks)
    points = list(scene.points)
    feature_next = {i: 0 for i in cameras}
    occupied = set()
    for t in tracks:
        for o in t.observations:
            feature_next[o.image_id] = max(feature_next[o.image_id], o.feature_id + 1)
            if t.point_id is not None:
                occupied.add((o.image_id, *np.floor(np.array(o.pixel)).astype(int)))
    pairs = []
    reports = []
    if len(points) < target_points:
        for i, j in combinations(sorted(cameras), 2):
            shared = []
            for t in scene.tracks:
                obs = {o.image_id: o.pixel for o in t.observations}
                if t.point_id is not None and i in obs and j in obs:
                    shared.append((obs[i], obs[j]))
            if len(shared) < 8:
                reports.append(
                    {"image_ids": [i, j], "skipped": "insufficient_existing_shared_points"}
                )
                continue
            print(f"Densifying image pair {i}–{j} with frozen poses", flush=True)
            try:
                candidates = stereo_candidates(
                    cameras[i],
                    cameras[j],
                    images[i],
                    images[j],
                    masks[i],
                    masks[j],
                    seed_pixels=np.array(shared).transpose(1, 0, 2),
                    max_image_size=max_image_size,
                )
            except ValueError as error:
                reports.append({"image_ids": [i, j], "skipped": str(error)})
                continue
            pairs.append(candidates)
            reports.append(candidates["report"])
            print(candidates["report"], flush=True)
    next_point = max((p.point_id for p in points), default=-1) + 1
    next_track = max((t.track_id for t in tracks), default=-1) + 1
    selected = {tuple(p["image_ids"]): 0 for p in pairs}
    for candidates_by_pair in zip_longest(*(range(len(p["positions"])) for p in pairs)):
        for pair, n in zip(pairs, candidates_by_pair, strict=True):
            if n is None or len(points) >= target_points:
                continue
            ids = pair["image_ids"]
            pixels = [pair["pixels_a"][n], pair["pixels_b"][n]]
            keys = [(i, *np.floor(xy).astype(int)) for i, xy in zip(ids, pixels, strict=True)]
            if any(key in occupied for key in keys):
                continue
            occupied.update(keys)
            observations = []
            for k, (i, xy) in enumerate(zip(ids, pixels, strict=True)):
                observations.append(
                    Observation(i, feature_next[i], tuple(xy), float(pair["errors"][n, k]))
                )
                feature_next[i] += 1
            tracks.append(SceneTrack(next_track, tuple(observations), next_point))
            points.append(
                ScenePoint(
                    next_point, next_track, tuple(pair["positions"][n]), float(pair["angles"][n])
                )
            )
            selected[tuple(ids)] += 1
            next_point += 1
            next_track += 1
        if len(points) >= target_points:
            break
    report = {
        "target_points": target_points,
        "original_point_count": len(scene.points),
        "added_point_count": len(points) - len(scene.points),
        "final_point_count": len(points),
        "target_reached": len(points) >= target_points,
        "pairs": reports,
        "selected_by_pair": [{"image_ids": list(k), "count": v} for k, v in selected.items()],
        "method": "rectified_bidirectional_SGBM_NCC_fixed_camera_triangulation",
        "thresholds": {
            "left_right_px": 0.75,
            "ncc_min": 0.85,
            "patch_std_min": 6,
            "reprojection_max_working_px": 3,
            "triangulation_angle_min_degrees": 1,
            "deduplication_working_pixel_cell_size": 1,
        },
        "camera_geometry_changed": False,
        "metric_scale_applied": False,
        "synthetic_or_duplicate_padding_used": False,
        "new_feature_identifier_source": "unique_stereo_pixel_sample_ids_per_image",
    }
    metadata = {
        **scene.metadata,
        "original_feature_identifier_source": scene.metadata.get(
            "feature_identifier_source", "unspecified"
        ),
        "feature_identifier_source": "original_tracks_and_unique_stereo_pixel_samples",
        "quality_status": "densified_inspection_unverified",
        "densification": report,
        "warnings": [
            *scene.metadata.get("warnings", []),
            "Additional stereo points are inspection data, "
            "not independently validated metric measurements",
        ],
    }
    return replace(scene, tracks=tuple(tracks), points=tuple(points), metadata=metadata), report
