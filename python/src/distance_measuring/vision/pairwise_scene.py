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

from .overlap import require_connected_overlap
from .pose import estimate_essential, recover_relative_pose
from .tracks import MatchEdge, build_tracks
from .triangulation import triangulate_point


@dataclass(frozen=True)
class PairwiseSceneResult:
    scene: Scene
    pair_scenes: dict
    report: dict


def _scene(tracks, points, cameras, poses, metadata, *, initialization_camera=None):
    scene_tracks, scene_points = [], []
    for track in tracks:
        features = [(i, k) for i, k in track.features if i in poses]
        if len(features) < 2:
            continue
        ids = [i for i, _ in features]
        result = triangulate_point(
            [points[i][k] for i, k in features],
            [cameras[i] for i in ids],
            [poses[i] for i in ids],
            max_reprojection_error_px=15 if initialization_camera in ids else 3,
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


def _pairwise_pose_initialization(image_id, hypotheses, poses, tracks, points, cameras, metadata):
    """Use two registered visual baselines when no 3-view point supports PnP.

    Independent two-image tracks suffice around a nondegenerate camera loop.
    A lone/collinear baseline cannot determine the new camera's relative scale.
    """
    from scipy.spatial.transform import Rotation

    from distance_measuring.optimization.bundle import bundle_adjust

    from .validation import prune_visual_outliers

    neighbors = []
    for (i, j), (relative, _, _) in hypotheses.items():
        if image_id not in (i, j):
            continue
        other = j if image_id == i else i
        if other not in poses:
            continue
        R = relative.R.T @ poses[j].R_cw if image_id == i else relative.R @ poses[i].R_cw
        neighbors.append((other, relative, image_id == i, R))
    proposals = []
    for a, b in combinations(neighbors, 2):
        delta = np.degrees(Rotation.from_matrix(a[3] @ b[3].T).magnitude())
        if delta > 3:
            continue
        R = Rotation.from_matrix(np.array([a[3], b[3]])).mean().as_matrix()
        directions = [
            poses[o].R_cw.T @ p.t_direction if reverse else -R.T @ p.t_direction
            for o, p, reverse, _ in (a, b)
        ]
        projections = [np.eye(3) - np.outer(d, d) for d in directions]
        A = sum(projections)
        eigenvalues = np.linalg.eigvalsh(A)
        if eigenvalues[0] < 0.001 * eigenvalues[-1]:
            continue
        centers = [poses[a[0]].C_w, poses[b[0]].C_w]
        # Lecture 002 pp. 98/101, 133–136: orthogonal ray least squares. Here
        # rays constrain CAMERA centers using visual translation directions.
        center = np.linalg.solve(A, sum(P @ C for P, C in zip(projections, centers, strict=True)))
        baseline = np.linalg.norm(centers[1] - centers[0])
        if baseline <= 0 or any(
            (center - C) @ d <= 0 for C, d in zip(centers, directions, strict=True)
        ):
            continue
        miss = (
            max(np.linalg.norm(P @ (center - C)) for P, C in zip(projections, centers, strict=True))
            / baseline
        )
        if miss > 0.05:
            continue
        support = int(a[1].inliers.sum() + b[1].inliers.sum())
        proposals.append((support, miss, R, center, [a[0], b[0]], float(delta)))
    for support, miss, R, C, ids, delta in sorted(proposals, key=lambda p: (-p[0], p[1]))[:3]:
        trial_poses = {**poses, image_id: CameraExtrinsics(R, C)}
        trial = _scene(
            tracks, points, cameras, trial_poses, metadata, initialization_camera=image_id
        )
        observations = [
            o
            for t in trial.tracks
            if t.point_id is not None
            for o in t.observations
            if o.image_id == image_id
        ]
        if len(observations) < 20:
            continue
        try:
            refined = bundle_adjust(trial, max_nfev=150)
            strict, _ = prune_visual_outliers(refined.scene)
            refined = bundle_adjust(strict, max_nfev=150)
        except ValueError:
            continue
        observations = [
            o
            for t in refined.scene.tracks
            if t.point_id is not None
            for o in t.observations
            if o.image_id == image_id
        ]
        if (
            refined.report["accepted"]
            and len(observations) >= 20
            and max(o.reprojection_error_px for o in observations) <= 3
        ):
            return refined.scene, {
                "image_id": image_id,
                "method": "visual_baseline_intersection",
                "registered_neighbors": ids,
                "pair_pose_support": support,
                "normalized_ray_miss": float(miss),
                "rotation_consistency_degrees": delta,
                "accepted_observations": len(observations),
                "initialization_reprojection_limit_px": 15,
                "accepted_reprojection_limit_px": 3,
                "bundle_adjustment": refined.report,
            }
    return None, None


def rebuild_pairwise_scene(audit, *, refine_incremental=False, seed_strategy="support"):
    """Visual seed, incremental PnP/triangulation and optional BA; fixed K.

    Neither acceleration nor known object distances enter camera estimation.
    Every selected camera must register; unresolved views cause an error.
    """
    ids = audit["image_ids"]
    if seed_strategy not in ("support", "conditioned"):
        raise ValueError("Seed strategy must be support or conditioned")
    if len(ids) < 2 or len(set(ids)) != len(ids):
        raise ValueError("Connected reconstruction needs at least two distinct image IDs")
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
    require_connected_overlap(ids, [pair for pair, rows in by_pair.items() if len(rows) >= 8])
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
                "median_parallax_degrees": pose.median_parallax_degrees,
                "failure": pose.failure,
            }
        )
        if pose.R is None or pose.inliers.sum() < 8:
            continue
        poses = {
            i: CameraExtrinsics(np.eye(3), np.zeros(3)),
            j: CameraExtrinsics(pose.R, -pose.R.T @ pose.t_direction),
        }
        pair_scenes[i, j] = _scene(linked.tracks, points, cameras, poses, metadata)
        hypotheses[i, j] = (pose, poses, rows)
    overlap = require_connected_overlap(ids, list(hypotheses), stage="verified visual poses")
    # Select by pose support BEFORE inspecting ruler ticks or IMU scale.
    if seed_strategy == "conditioned":
        # Lecture 002 pp. 133–136: nearly parallel rays amplify depth error.
        # Prefer supported, finite-baseline seeds BEFORE ruler/IMU evaluation.
        # The support/parallax score is an implementation heuristic, not a bound
        # on camera accuracy; independent held-out rays still validate the result.
        eligible = [
            key
            for key, (p, _, _) in hypotheses.items()
            if p.inliers.sum() >= 30
            and p.median_parallax_degrees is not None
            and 1 <= min(p.median_parallax_degrees, 180 - p.median_parallax_degrees) <= 45
        ]
        if not eligible:
            raise ValueError("No well-supported visual seed supplies suitable parallax")
        seed = max(
            eligible,
            key=lambda key: (
                hypotheses[key][0].inliers.sum()
                * np.sin(np.radians(hypotheses[key][0].median_parallax_degrees)) ** 2,
                key,
            ),
        )
    else:
        seed = max(
            hypotheses,
            key=lambda key: (int(hypotheses[key][0].inliers.sum()), len(by_pair[key]), key),
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
    scene = _scene(linked.tracks, points, cameras, poses, metadata)
    initial_seed_point_count = len(seed_points)
    registrations, refinements = [], []
    remaining = set(ids) - set(seed)
    while remaining:
        candidates = []
        for image_id in sorted(remaining):
            shared = [
                t
                for t in linked.tracks
                if t.track_id in seed_points and image_id in dict(t.features)
            ]
            candidates.append((image_id, shared))
        candidates.sort(key=lambda item: (-len(item[1]), item[0]))
        registered = False
        for image_id, shared in candidates:
            if len(shared) < 8:
                continue
            world = np.array([seed_points[t.track_id] for t in shared])
            pixels = np.array([points[image_id][dict(t.features)[image_id]] for t in shared])
            # Lecture 002 pp. 81–83: m~K[R|-RC]Pw. PnP fits R and the origin
            # column O; camera center is C=-R.T O. No IMU or ruler observations.
            cv2.setRNGSeed(0)
            ok, rvec, tvec, inliers = cv2.solvePnPRansac(
                world,
                pixels,
                cameras[image_id].K,
                None,
                iterationsCount=10000,
                reprojectionError=3,
                confidence=0.999,
                flags=cv2.SOLVEPNP_EPNP,
            )
            if not ok or inliers is None or len(inliers) < 8:
                continue
            keep = inliers[:, 0]
            rvec, tvec = cv2.solvePnPRefineLM(
                world[keep], pixels[keep], cameras[image_id].K, None, rvec, tvec
            )
            rotation = cv2.Rodrigues(rvec)[0]
            poses[image_id] = CameraExtrinsics(rotation, -rotation.T @ tvec.ravel())
            registrations.append(
                {
                    "image_id": image_id,
                    "method": "pnp",
                    "candidates": len(shared),
                    "inliers": len(keep),
                }
            )
            remaining.remove(image_id)
            scene = _scene(linked.tracks, points, cameras, poses, metadata)
            if refine_incremental:
                from distance_measuring.optimization.bundle import bundle_adjust

                refined = bundle_adjust(scene, max_nfev=150)
                refinements.append({"after_image_id": image_id, **refined.report})
                scene = refined.scene
                poses = {c.image_id: c.extrinsics for c in scene.cameras}
            seed_points = {p.track_id: np.array(p.position) for p in scene.points}
            registered = True
            break
        if not registered:
            if refine_incremental:
                for image_id, _ in candidates:
                    trial, record = _pairwise_pose_initialization(
                        image_id, hypotheses, poses, linked.tracks, points, cameras, metadata
                    )
                    if trial is not None:
                        scene = trial
                        poses = {c.image_id: c.extrinsics for c in scene.cameras}
                        seed_points = {p.track_id: np.array(p.position) for p in scene.points}
                        registrations.append(record)
                        remaining.remove(image_id)
                        registered = True
                        break
        if not registered:
            counts = {i: len(rows) for i, rows in candidates}
            raise ValueError(
                "Visual registration failed (insufficient shared points, failed PnP, "
                "or unconstrained visual baselines) for remaining images "
                f"{sorted(remaining)}; shared counts: {counts}. Aborting."
            )
    return PairwiseSceneResult(
        scene,
        pair_scenes,
        {
            "seed_image_ids": list(seed),
            "seed_strategy": seed_strategy,
            "seed_point_count": initial_seed_point_count,
            "pnp_image_id": registrations[0]["image_id"]
            if len(registrations) == 1 and registrations[0]["method"] == "pnp"
            else None,
            "pnp_candidates": registrations[0].get("candidates")
            if len(registrations) == 1
            else None,
            "pnp_inliers": registrations[0].get("inliers") if len(registrations) == 1 else None,
            "registrations": registrations,
            "incremental_bundle_adjustments": refinements,
            "track_count": len(linked.tracks),
            "track_conflicts": len(linked.conflicts),
            "accepted_point_count": len(scene.points),
            "pair_poses": pair_reports,
            "overlap": overlap,
        },
    )
