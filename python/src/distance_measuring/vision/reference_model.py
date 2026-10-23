"""Adapt image-only COLMAP geometry to the lecture K/R/C scene convention.

Lecture 002 pp. 36–45, 65–76, 81–83, 98/101. A display similarity aligns two
arbitrary visual gauges; it does not estimate metric scale or use inertial data.
"""

from itertools import combinations

import numpy as np
from scipy.spatial.transform import Rotation

from distance_measuring.camera import CameraExtrinsics, CameraIntrinsics
from distance_measuring.scene import Observation, Scene, SceneCamera, ScenePoint, SceneTrack


def scene_from_colmap(model, manifest):
    by_name = {r["name"]: r for r in manifest["images"]}
    image_map = {i: by_name[im.name]["image_id"] for i, im in model.images.items() if im.has_pose}
    cameras = {}
    for i, image in model.images.items():
        if i not in image_map:
            continue
        native = image.camera
        if any(abs(v) > 1e-14 for v in native.params[4:]) or native.model_name not in [
            "PINHOLE",
            "SIMPLE_PINHOLE",
        ]:
            raise ValueError("Import the undistorted pinhole MVS model, not distorted photo pixels")
        K = native.calibration_matrix().copy()
        # COLMAP: top-left pixel center (.5,.5); this repository: (0,0).
        # Lecture 002 pp. 65–76: coordinate origin/sampling belongs in K.
        K[:2, 2] -= 0.5
        intrinsics = CameraIntrinsics.from_K(
            K,
            (native.width, native.height),
            source="calibrated",
            camera_id=str(native.camera_id),
            assumptions=(
                "Shared focal/radial parameters refined from images; physical accuracy unverified",
                "Undistorted pinhole frame, COLMAP half-pixel origin converted explicitly",
            ),
        )
        pose = image.cam_from_world()
        R = pose.rotation.matrix()
        C = -R.T @ pose.translation
        cameras[i] = SceneCamera(image_map[i], intrinsics, CameraExtrinsics(R, C))
    tracks = []
    points = []
    collapsed_observations = 0
    for point_id, p in model.points3D.items():
        observations = []
        for element in p.track.elements:
            if element.image_id not in cameras:
                continue
            c = cameras[element.image_id]
            pixel = model.image(element.image_id).points2D[element.point2D_idx].xy - 0.5
            predicted = c.intrinsics.project(p.xyz, c.extrinsics)
            observations.append(
                Observation(
                    c.image_id,
                    int(element.point2D_idx),
                    tuple(pixel),
                    float(np.linalg.norm(predicted - pixel)),
                )
            )
        # SIFT can associate multiple orientation descriptors at one location.
        # The scene contract keeps one measured observation per camera/track.
        unique = {}
        for observation in observations:
            old = unique.get(observation.image_id)
            if old is None or observation.reprojection_error_px < old.reprojection_error_px:
                unique[observation.image_id] = observation
        collapsed_observations += len(observations) - len(unique)
        observations = sorted(unique.values(), key=lambda o: o.image_id)
        if len(observations) < 2:
            continue
        rays = np.array(
            [
                p.xyz - c.extrinsics.C_w
                for c in cameras.values()
                if c.image_id in {o.image_id for o in observations}
            ]
        )
        rays /= np.linalg.norm(rays, axis=1, keepdims=True)
        angle = max(
            float(np.degrees(np.arccos(np.clip(abs(a @ b), 0, 1))))
            for a, b in combinations(rays, 2)
        )
        point_id = int(point_id)
        tracks.append(SceneTrack(point_id, tuple(observations), point_id))
        points.append(ScenePoint(point_id, point_id, tuple(p.xyz), angle))
    return Scene(
        tuple(sorted(cameras.values(), key=lambda c: c.image_id)),
        tuple(tracks),
        tuple(points),
        {
            "session_id": manifest["session_id"],
            "scale_status": "not_estimated_visual_only",
            "quality_status": "image_only_reference_unverified",
            "camera_fit_sources": "images_only_self_calibration",
            "metric_scale_applied": False,
            "sensor_data_used": False,
            "ruler_lengths_used": False,
            "backend": "COLMAP image-only SfM and calibrated multi-view stereo",
            "duplicate_orientation_observations_collapsed": collapsed_observations,
            "excluded_images": [
                {"image_id": r["image_id"], "reason": "not_registered_by_visual_mapper"}
                for r in manifest["images"]
                if r["image_id"] not in image_map.values()
            ],
            "warnings": [
                "Joint visual reconstruction has arbitrary scale; "
                "density is not an accuracy certificate"
            ],
            "input_sha256": manifest["input_sha256"],
        },
    )


def compare_visual_cameras(previous, reference):
    old = {c.image_id: c for c in previous.cameras}
    new = {c.image_id: c for c in reference.cameras}
    ids = sorted(old.keys() & new.keys())
    if len(ids) < 3:
        raise ValueError("Camera comparison needs at least three common camera positions")
    # Lecture 002 pp. 38–45/81–83: average changes of world reference. This
    # avoids unstable rotation estimation from almost-collinear camera centers.
    Q = (
        Rotation.from_matrix(
            np.array([new[i].extrinsics.R_cw.T @ old[i].extrinsics.R_cw for i in ids])
        )
        .mean()
        .as_matrix()
    )
    X = np.array([old[i].extrinsics.C_w for i in ids])
    Y = np.array([new[i].extrinsics.C_w for i in ids])
    Xc, Yc = X - X.mean(axis=0), Y - Y.mean(axis=0)
    variance = float(np.sum(Xc * Xc))
    if variance < 1e-12:
        raise ValueError("Coincident camera centers cannot define a display unit ratio")
    ratio = float(np.sum((Xc @ Q.T) * Yc) / variance)
    if ratio <= 0:
        raise ValueError("Previous camera centers cannot be positively aligned")
    t = Y.mean(axis=0) - ratio * (Q @ X.mean(axis=0))
    baseline = float(np.linalg.norm(Y[1] - Y[0]))
    if baseline < 1e-12:
        raise ValueError("Comparison baseline must be nonzero")
    overlay = []
    for i in ids:
        pose = old[i].extrinsics
        overlay.append(
            {
                "image_id": i,
                "center": (ratio * Q @ pose.C_w + t).tolist(),
                "R_cw": (pose.R_cw @ Q.T).tolist(),
                "intrinsics": old[i].intrinsics.to_dict(),
                "center_error_reference_baselines": float(
                    np.linalg.norm(ratio * Q @ pose.C_w + t - new[i].extrinsics.C_w) / baseline
                ),
            }
        )
    relations = []
    old_base = np.linalg.norm(X[1] - X[0])
    for i, j in combinations(ids, 2):
        relative_old = old[j].extrinsics.R_cw @ old[i].extrinsics.R_cw.T
        relative_new = new[j].extrinsics.R_cw @ new[i].extrinsics.R_cw.T
        old_ratio = float(np.linalg.norm(old[j].extrinsics.C_w - old[i].extrinsics.C_w) / old_base)
        new_ratio = float(np.linalg.norm(new[j].extrinsics.C_w - new[i].extrinsics.C_w) / baseline)
        relations.append(
            {
                "image_ids": [i, j],
                "relative_rotation_difference_degrees": float(
                    np.degrees(Rotation.from_matrix(relative_old @ relative_new.T).magnitude())
                ),
                "previous_baseline_ratio": old_ratio,
                "reference_baseline_ratio": new_ratio,
                "baseline_ratio_difference_percent": float(100 * abs(old_ratio / new_ratio - 1)),
            }
        )
    return {
        "common_image_ids": ids,
        "display_similarity": {
            "rotation": Q.tolist(),
            "visual_unit_ratio": ratio,
            "translation": t.tolist(),
        },
        "metric_scale_estimated": False,
        "reference_is_ground_truth": False,
        "relations": relations,
        "previous_camera_overlay": overlay,
    }
