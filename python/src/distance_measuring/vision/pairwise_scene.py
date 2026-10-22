"""Rebuild a scene from audited pairs; lecture 002 pp. 81–83, 133–138.

Essential/PnP initialization and track linking extend the lecture geometry.
Points observed in only two images remain eligible. Ruler lengths are not inputs.
"""

from dataclasses import dataclass
from itertools import combinations

import cv2
import numpy as np

from distance_measuring.camera import CameraExtrinsics
from distance_measuring.scene import Observation, Scene, SceneCamera, ScenePoint, SceneTrack
from distance_measuring.scene.model import intrinsics_from_dict

from .pose import estimate_essential, recover_relative_pose
from .tracks import MatchEdge, build_tracks
from .triangulation import triangulate_point


@dataclass(frozen=True)
class PairwiseSceneResult:
    scene: Scene
    pair_scenes: dict
    report: dict


def _scene(tracks, points, cameras, poses, metadata):
    scene_tracks, scene_points = [], []
    for track in tracks:
        features = [(i, k) for i, k in track.features if i in poses]
        if len(features) < 2:
            continue
        ids = [i for i, _ in features]
        result = triangulate_point(
            [points[i][k] for i, k in features], [cameras[i] for i in ids], [poses[i] for i in ids]
        )
        point_id = track.track_id if result.position is not None else None
        observations = tuple(
            Observation(
                i,
                k,
                tuple(points[i][k]),
                result.reprojection_errors_px[n] if result.reprojection_errors_px else None,
            )
            for n, (i, k) in enumerate(features)
        )
        scene_tracks.append(
            SceneTrack(track.track_id, observations, point_id, result.rejection_reason)
        )
        if point_id is not None:
            scene_points.append(
                ScenePoint(point_id, track.track_id, result.position, result.angle_degrees)
            )
    return Scene(
        tuple(SceneCamera(i, cameras[i], poses[i]) for i in sorted(poses)),
        tuple(scene_tracks),
        tuple(scene_points),
        metadata,
    )


def rebuild_pairwise_scene(audit):
    """Strongest two-view seed, then PnP for the remaining view; fixed K throughout."""
    ids = audit["image_ids"]
    if len(ids) != 3 or len(set(ids)) != 3:
        raise ValueError("Connected retry requires three distinct image IDs")
    cameras = {
        r["image_id"]: intrinsics_from_dict(r["working_intrinsics"]) for r in audit["images"]
    }
    points = {i: {} for i in ids}
    edges = []
    by_pair = {key: [] for key in combinations(ids, 2)}
    for row in audit["pairwise_matches"]:
        if not row["usable_pairwise"]:
            continue
        present = [(i, k) for i, k in zip(ids, row["group_ids"], strict=True) if k is not None]
        if len(present) != 2:
            raise ValueError("Pairwise candidates must have exactly two observations")
        (i, a), (j, b) = present
        for image_id, key in present:
            pixel = row["working_pixels"][ids.index(image_id)]
            if key in points[image_id] and not np.array_equal(points[image_id][key], pixel):
                raise ValueError("A feature group has inconsistent pixel coordinates")
            points[image_id][key] = pixel
        edges.append(MatchEdge((i, a), (j, b), 1 - row["correlation"]["score"]))
        by_pair[i, j].append(row)
    linked = build_tracks(edges)
    metadata = {
        "session_id": audit["session_id"],
        "scale_status": "arbitrary_visual_scale_unverified",
        "quality_status": "pairwise_rebuilt_unverified",
        "source_review_id": audit["review_id"],
        "feature_identifier_source": "audited_affine_location_groups",
        "warnings": [
            "Camera K is unchanged; calibration and physical scale are unverified",
            "Automatic correspondence checks are not exhaustive manual ground truth",
        ],
    }
    hypotheses = {}
    pair_scenes = {}
    pair_reports = []
    for (i, j), rows in by_pair.items():
        if len(rows) < 8:
            continue
        p1 = np.array([r["working_pixels"][ids.index(i)] for r in rows])
        p2 = np.array([r["working_pixels"][ids.index(j)] for r in rows])
        x1, x2 = cameras[i].normalize_pixels(p1), cameras[j].normalize_pixels(p2)
        sampling = min(np.linalg.svd(cameras[k].K[:2, :2], compute_uv=False)[-1] for k in (i, j))
        estimate = estimate_essential(x1, x2, threshold_normalized=1.5 / sampling, seed=0)
        pose = recover_relative_pose(
            x1, x2, estimate, max_reprojection_error_normalized=3 / sampling
        )
        pair_reports.append(
            {
                "image_ids": [i, j],
                "match_count": len(rows),
                "essential_inliers": int(estimate.inliers.sum()),
                "pose_inliers": int(pose.inliers.sum()),
                "failure": pose.failure,
            }
        )
        if pose.R is None:
            continue
        poses = {
            i: CameraExtrinsics(np.eye(3), np.zeros(3)),
            j: CameraExtrinsics(pose.R, -pose.R.T @ pose.t_direction),
        }
        pair_scenes[i, j] = _scene(linked.tracks, points, cameras, poses, metadata)
        hypotheses[i, j] = (pose, poses, rows)
    if not hypotheses:
        raise ValueError("No audited pair supplies a valid visual pose")
    # Select by pose support BEFORE inspecting ruler ticks or IMU scale.
    seed = max(
        hypotheses, key=lambda key: (int(hypotheses[key][0].inliers.sum()), len(by_pair[key]), key)
    )
    pose, poses, rows = hypotheses[seed]
    poses = dict(poses)
    direct = {
        tuple(r["group_ids"][ids.index(i)] for i in seed)
        for r, ok in zip(rows, pose.inliers, strict=True)
        if ok
    }
    seed_points = {}
    for track in linked.tracks:
        obs = dict(track.features)
        if not all(i in obs for i in seed) or tuple(obs[i] for i in seed) not in direct:
            continue
        result = triangulate_point(
            [points[i][obs[i]] for i in seed], [cameras[i] for i in seed], [poses[i] for i in seed]
        )
        if result.position is not None:
            seed_points[track.track_id] = result.position
    third = next(i for i in ids if i not in seed)
    shared = [t for t in linked.tracks if t.track_id in seed_points and third in dict(t.features)]
    if len(shared) < 8:
        raise ValueError("Fewer than eight seed points overlap the remaining image")
    world = np.array([seed_points[t.track_id] for t in shared])
    pixels = np.array([points[third][dict(t.features)[third]] for t in shared])
    # Lecture 002 pp. 81–83: m~K[R|-RC]Pw. PnP fits R and the origin column
    # O to existing 3D points; convert O back to center C=-R.T O explicitly.
    # PnP/RANSAC is an implementation extension, not ruler calibration.
    cv2.setRNGSeed(0)
    ok, rvec, tvec, inliers = cv2.solvePnPRansac(
        world,
        pixels,
        cameras[third].K,
        None,
        iterationsCount=10000,
        reprojectionError=3,
        confidence=0.999,
        flags=cv2.SOLVEPNP_EPNP,
    )
    if not ok or inliers is None or len(inliers) < 8:
        raise ValueError("Remaining image failed 3D-to-2D pose verification")
    keep = inliers[:, 0]
    rvec, tvec = cv2.solvePnPRefineLM(world[keep], pixels[keep], cameras[third].K, None, rvec, tvec)
    rotation = cv2.Rodrigues(rvec)[0]
    poses[third] = CameraExtrinsics(rotation, -rotation.T @ tvec.ravel())
    scene = _scene(linked.tracks, points, cameras, poses, metadata)
    return PairwiseSceneResult(
        scene,
        pair_scenes,
        {
            "seed_image_ids": list(seed),
            "seed_point_count": len(seed_points),
            "pnp_image_id": third,
            "pnp_candidates": len(shared),
            "pnp_inliers": len(keep),
            "track_count": len(linked.tracks),
            "track_conflicts": len(linked.conflicts),
            "accepted_point_count": len(scene.points),
            "pair_poses": pair_reports,
        },
    )
