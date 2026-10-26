"""Bundle adjustment with lecture 002 pp. 65–76, 81–83 and 133–136 references.

Page numbers use PPTX slide order. Joint nonlinear refinement, robust losses,
gauge parameterization and sparse Jacobians extend the deck's camera/ray model.
See docs/math/bundle_adjustment.md; no lecture assets are redistributed.
"""

import copy
from dataclasses import dataclass, replace
from itertools import combinations, pairwise

import numpy as np
from scipy.optimize import least_squares
from scipy.sparse import lil_matrix
from scipy.spatial.transform import Rotation

from distance_measuring.camera import CameraExtrinsics
from distance_measuring.scene import Scene, SceneCamera

LOSSES = ("linear", "soft_l1", "huber", "cauchy")


def reprojection_residuals(scene: Scene) -> np.ndarray:
    """Predicted minus observed (u,v), in track/observation order, accepted points only."""
    cameras = {camera.image_id: camera for camera in scene.cameras}
    points = {point.point_id: point for point in scene.points}
    residuals = []
    for track in scene.tracks:
        if track.point_id is None:
            continue
        point = points[track.point_id].position
        for observation in track.observations:
            camera = cameras[observation.image_id]
            # Lecture 002 pp. 65–68, 70–76, 82–83: rho*m=K[R|-R*C]P_h.
            # Divide homogeneous scale before comparing ideal working pixels.
            predicted = camera.intrinsics.project(point, camera.extrinsics, distort=False)
            residuals.extend(predicted - observation.pixel)
    return np.asarray(residuals, dtype=float)


def _cost(residuals: np.ndarray, loss: str, scale: float) -> float:
    z = (residuals / scale) ** 2
    if loss == "linear":
        rho = z
    elif loss == "soft_l1":
        rho = 2 * (np.sqrt(1 + z) - 1)
    elif loss == "huber":
        rho = np.where(z <= 1, z, 2 * np.sqrt(z) - 1)
    else:
        rho = np.log1p(z)
    return float(0.5 * scale**2 * np.sum(rho))


def _metrics(residuals: np.ndarray, loss: str, scale: float) -> dict:
    errors = np.linalg.norm(residuals.reshape(-1, 2), axis=1)
    return {
        "observation_count": len(errors),
        "reprojection_rmse_px": float(np.sqrt(np.mean(errors**2))),
        "median_reprojection_error_px": float(np.median(errors)),
        "max_reprojection_error_px": float(np.max(errors)),
        "robust_cost": _cost(residuals, loss, scale),
    }


class _Problem:
    """Gauge-reduced variables in first-baseline units; camera/point IDs stay external."""

    def __init__(
        self,
        scene: Scene,
        *,
        variable_camera_ids=None,
        variable_point_ids=None,
        observation_point_ids=None,
    ):
        if len(scene.cameras) < 2 or not scene.points:
            raise ValueError("Bundle adjustment needs at least two cameras and accepted 3D points")
        self.scene = scene
        self.camera_ids = {camera.image_id: i for i, camera in enumerate(scene.cameras)}
        self.point_ids = {point.point_id: i for i, point in enumerate(scene.points)}
        selective = any(
            v is not None for v in (variable_camera_ids, variable_point_ids, observation_point_ids)
        )

        def selection(values, known, default):
            values = list(default if values is None else values)
            if any(type(v) is not int or v not in known for v in values) or len(set(values)) != len(
                values
            ):
                raise ValueError(
                    "Bundle variable/observation selections must contain unique known IDs"
                )
            return set(values)

        variable_cameras = selection(
            variable_camera_ids, self.camera_ids, list(self.camera_ids)[1:]
        )
        if scene.cameras[0].image_id in variable_cameras:
            raise ValueError("The global first camera must remain fixed")
        variable_points = selection(variable_point_ids, self.point_ids, self.point_ids)
        observed_points = selection(observation_point_ids, self.point_ids, self.point_ids)
        if not variable_points <= observed_points:
            raise ValueError("Variable landmarks must belong to the selected observation set")
        self.origin = scene.cameras[0].extrinsics.C_w
        self.baseline = float(np.linalg.norm(scene.cameras[1].extrinsics.C_w - self.origin))
        if not np.isfinite(self.baseline) or self.baseline <= 0:
            raise ValueError("The first two cameras need a positive finite baseline to fix scale")
        self.rotations = np.array([camera.extrinsics.R_cw for camera in scene.cameras])
        self.centers = np.array(
            [(camera.extrinsics.C_w - self.origin) / self.baseline for camera in scene.cameras]
        )
        self.direction = self.centers[1]
        axis = np.eye(3)[np.argmin(np.abs(self.direction))]
        tangent = np.cross(self.direction, axis)
        tangent /= np.linalg.norm(tangent)
        self.basis = np.column_stack((tangent, np.cross(self.direction, tangent)))
        self.camera_slices, initial = {}, []
        for i in range(1, len(scene.cameras)):
            if scene.cameras[i].image_id not in variable_cameras:
                continue
            count = 5 if i == 1 else 6
            self.camera_slices[i] = slice(len(initial), len(initial) + count)
            initial.extend([0.0] * 3)
            initial.extend([0.0] * 2 if i == 1 else self.centers[i])
        self.point_start = len(initial)
        self.positions = (
            np.array([p.position for p in scene.points]) - self.origin
        ) / self.baseline
        self.point_slices = {}
        for i, point in enumerate(scene.points):
            if point.point_id in variable_points:
                self.point_slices[i] = slice(len(initial), len(initial) + 3)
                initial.extend(self.positions[i])
        if not initial:
            raise ValueError("Bundle adjustment has no free variables")
        self.initial = np.asarray(initial, dtype=float)
        rows, observed = [], []
        graph = {i: set() for i in range(len(scene.cameras))}
        for track in scene.tracks:
            if track.point_id is None or track.point_id not in observed_points:
                continue
            views = [self.camera_ids[observation.image_id] for observation in track.observations]
            for a, b in combinations(views, 2):
                graph[a].add(b)
                graph[b].add(a)
            for observation in track.observations:
                rows.append((self.camera_ids[observation.image_id], self.point_ids[track.point_id]))
                observed.append(observation.pixel)
        reached, pending = set(), [0]
        while pending:
            current = pending.pop()
            if current not in reached:
                reached.add(current)
                pending.extend(graph[current] - reached)
        if not selective and len(reached) != len(scene.cameras):
            raise ValueError(
                "Accepted observation graph must connect every camera to the fixed camera"
            )
        if selective:
            participating = {camera for camera, _ in rows}
            if not set(self.camera_slices) <= participating:
                raise ValueError("Every variable camera needs selected landmark observations")
            # Outside cameras are fixed in the ORIGINAL global frame. Each
            # variable component needs two fixed centers, the original gauge
            # pair, or fixed non-collinear 3D anchors to avoid a local scale gauge.
            unseen = set(participating)
            while unseen:
                component, pending = set(), [min(unseen)]
                while pending:
                    current = pending.pop()
                    if current not in component:
                        component.add(current)
                        pending.extend(graph[current] - component)
                unseen -= component
                fixed = sorted(component - self.camera_slices.keys())
                two_fixed = any(
                    np.linalg.norm(self.centers[a] - self.centers[b]) > 1e-12
                    for a, b in combinations(fixed, 2)
                )
                fixed_points = sorted(
                    {
                        point
                        for camera, point in rows
                        if camera in component and point not in self.point_slices
                    }
                )
                anchors = self.positions[fixed_points]
                fixed_structure = (
                    len(anchors) >= 3 and np.linalg.matrix_rank(anchors - anchors.mean(axis=0)) >= 2
                )
                if not ({0, 1} <= component or two_fixed or fixed_structure):
                    raise ValueError("Local observation component lacks global scale/pose anchors")
        if 2 * len(rows) < len(self.initial):
            raise ValueError(
                "Too few reprojection equations for the gauge-reduced camera/point variables"
            )
        self.rows = np.asarray(rows, dtype=int)
        self.observed = np.asarray(observed, dtype=float)
        self.K = np.array([camera.intrinsics.K for camera in scene.cameras])
        self.depth_floor = 1e-8
        self.depth_weight = 10 * float(np.max(self.K[:, :2, :2]))
        pattern = lil_matrix((3 * len(rows), len(initial)), dtype=int)
        for i, (camera, point) in enumerate(rows):
            if camera in self.camera_slices:
                pattern[3 * i : 3 * i + 3, self.camera_slices[camera]] = 1
            if point in self.point_slices:
                pattern[3 * i : 3 * i + 3, self.point_slices[point]] = 1
        self.sparsity = pattern.tocsr()

    def decode(self, parameters):
        rotations, centers = self.rotations.copy(), self.centers.copy()
        for camera, section in self.camera_slices.items():
            values = parameters[section]
            # Lecture 002 pp. 39–45, 81–83: proper rotation increments compose
            # with R_cw; a rotation vector provides three independent parameters.
            rotations[camera] = (
                Rotation.from_rotvec(values[:3]).as_matrix() @ self.rotations[camera]
            )
            if camera == 1:
                # Remove scale freedom EXACTLY: a two-dimensional tangent vector
                # maps onto the unit sphere. No radial variable or soft baseline
                # penalty remains. Camera zero is absent from the parameter vector.
                theta = np.linalg.norm(values[3:])
                centers[camera] = np.cos(theta) * self.direction + np.sinc(theta / np.pi) * (
                    self.basis @ values[3:]
                )
            else:
                centers[camera] = values[3:]
        points = self.positions.copy()
        for point, section in self.point_slices.items():
            points[point] = parameters[section]
        return rotations, centers, points

    def project(self, parameters):
        rotations, centers, points = self.decode(parameters)
        camera, point = self.rows.T
        # Lecture 002 pp. 82–83, 133–136: P_c=R(P_w-C), then m~K*P_c.
        # Baseline-normalized world units cancel during homogeneous division.
        local = np.einsum("nij,nj->ni", rotations[camera], points[point] - centers[camera])
        depth = local[:, 2]
        rays = np.column_stack(
            (local[:, :2] / np.maximum(depth, self.depth_floor)[:, None], np.ones(len(local)))
        )
        projected = np.einsum("nij,nj->ni", self.K[camera], rays)[:, :2]
        return projected - self.observed, depth

    def residuals(self, parameters):
        residuals, depth = self.project(parameters)
        # Page 136 motivates approximate least squares. This extension supplies
        # finite residuals and a forward-depth penalty for trial iterates; acceptance
        # separately requires positive depth for EVERY retained observation.
        penalty = self.depth_weight * np.maximum(self.depth_floor - depth, 0)
        return np.column_stack((residuals, penalty)).ravel()


@dataclass(frozen=True)
class BundleResult:
    scene: Scene
    report: dict


def bundle_adjust(
    scene: Scene,
    *,
    loss: str = "soft_l1",
    f_scale: float = 1.0,
    max_nfev: int = 300,
    variable_camera_ids=None,
    variable_point_ids=None,
    observation_point_ids=None,
) -> BundleResult:
    if loss not in LOSSES:
        raise ValueError(f"loss must be one of {LOSSES}")
    if not np.isfinite(f_scale) or f_scale <= 0:
        raise ValueError("f_scale must be positive and finite in working pixels")
    if type(max_nfev) is not int or max_nfev < 1:
        raise ValueError("max_nfev must be a positive integer")
    problem = _Problem(
        scene,
        variable_camera_ids=variable_camera_ids,
        variable_point_ids=variable_point_ids,
        observation_point_ids=observation_point_ids,
    )
    initial_residuals, initial_depth = problem.project(problem.initial)
    if np.any(initial_depth <= problem.depth_floor):
        raise ValueError("Initial accepted points must lie in front of every observing camera")
    before = _metrics(initial_residuals.ravel(), loss, f_scale)
    # Lecture 002 p. 136's least-squares motivation, extended to simultaneous
    # camera/point reprojection fitting. K stays fixed (pp. 70–76); sparse columns
    # connect each observation only to its own camera and shared 3D point.
    solved = least_squares(
        problem.residuals,
        problem.initial,
        jac_sparsity=problem.sparsity,
        method="trf",
        tr_solver="lsmr",
        tr_options={"atol": 1e-8, "btol": 1e-8, "maxiter": 1000},
        x_scale="jac",
        loss=loss,
        f_scale=f_scale,
        max_nfev=max_nfev,
        ftol=1e-7,
        xtol=1e-8,
        gtol=1e-7,
    )
    candidate, depths = problem.project(solved.x)
    finite = np.isfinite(candidate).all() and np.isfinite(solved.x).all()
    after_candidate = _metrics(candidate.ravel(), loss, f_scale) if finite else None
    accepted = bool(
        finite
        and np.all(depths > problem.depth_floor)
        and after_candidate["robust_cost"]
        <= before["robust_cost"] + 1e-9 * max(1.0, before["robust_cost"])
    )
    if accepted:
        candidate_rotations, candidate_centers, candidate_positions = problem.decode(solved.x)
        # A selective fit must also keep observations excluded from its objective
        # forward of their cameras; those points still belong to the saved scene.
        accepted = all(
            (
                candidate_rotations[problem.camera_ids[o.image_id]]
                @ (
                    candidate_positions[problem.point_ids[t.point_id]]
                    - candidate_centers[problem.camera_ids[o.image_id]]
                )
            )[2]
            > problem.depth_floor
            for t in scene.tracks
            if t.point_id is not None
            for o in t.observations
        )
    chosen = solved.x if accepted else problem.initial
    after = after_candidate if accepted else before
    rotations, centers, positions = problem.decode(chosen)
    cameras = [scene.cameras[0]]
    for i, original in enumerate(scene.cameras[1:], 1):
        if i not in problem.camera_slices:
            cameras.append(original)
            continue
        cameras.append(
            SceneCamera(
                original.image_id,
                original.intrinsics,
                CameraExtrinsics(rotations[i], problem.origin + problem.baseline * centers[i]),
            )
        )
    point_positions = {
        p.point_id: (problem.origin + problem.baseline * positions[i])
        if i in problem.point_slices
        else p.position
        for i, p in enumerate(scene.points)
    }
    camera_by_id = {c.image_id: c for c in cameras}
    tracks = []
    for track in scene.tracks:
        if track.point_id is None:
            tracks.append(track)
            continue
        observations = tuple(
            replace(
                observation,
                reprojection_error_px=float(
                    np.linalg.norm(
                        camera_by_id[observation.image_id].intrinsics.project(
                            point_positions[track.point_id],
                            camera_by_id[observation.image_id].extrinsics,
                            distort=False,
                        )
                        - observation.pixel
                    )
                ),
            )
            for observation in track.observations
        )
        tracks.append(replace(track, observations=observations))
    points = []
    track_by_point = {track.point_id: track for track in tracks if track.point_id is not None}
    for index, original in enumerate(scene.points):
        if index not in problem.point_slices:
            points.append(original)
            continue
        position = problem.origin + problem.baseline * positions[index]
        views = track_by_point[original.point_id].observations
        rays = np.array(
            [positions[index] - centers[problem.camera_ids[obs.image_id]] for obs in views]
        )
        rays /= np.linalg.norm(rays, axis=1, keepdims=True)
        angle = max(
            float(np.degrees(np.arccos(np.clip(abs(np.dot(a, b)), 0, 1))))
            for a, b in combinations(rays, 2)
        )
        points.append(
            replace(original, position=tuple(position), triangulation_angle_degrees=angle)
        )
    if not accepted:
        cameras, tracks, points = list(scene.cameras), list(scene.tracks), list(scene.points)
    report = {
        "accepted": accepted,
        "converged": bool(solved.success),
        "solver_status": int(solved.status),
        "solver_message": str(solved.message),
        "nfev": int(solved.nfev),
        "optimality": float(solved.optimality),
        "settings": {
            "loss": loss,
            "f_scale_px": f_scale,
            "max_nfev": max_nfev,
            "method": "trf",
            "tr_solver": "lsmr",
            "lsmr_atol": 1e-8,
            "lsmr_btol": 1e-8,
            "lsmr_maxiter": 1000,
            "ftol": 1e-7,
            "xtol": 1e-8,
            "gtol": 1e-7,
        },
        "gauge": {
            "fixed_camera_id": scene.cameras[0].image_id,
            "fixed_baseline_camera_ids": [scene.cameras[0].image_id, scene.cameras[1].image_id],
            "fixed_baseline_length": problem.baseline,
        },
        "before": before,
        "after": after,
        "candidate": after_candidate,
        "input_scale_status": scene.metadata.get("scale_status"),
        "input_quality_status": scene.metadata.get("quality_status"),
        "input_baseline_convention": scene.metadata.get("baseline_convention"),
        "input_pose_closure": scene.metadata.get("pose_closure"),
        "point_count": len(points),
        "variable_camera_ids": [scene.cameras[i].image_id for i in problem.camera_slices],
        "variable_point_ids": [scene.points[i].point_id for i in problem.point_slices],
        "observation_point_ids": [
            p.point_id
            for p in scene.points
            if p.point_id in {scene.points[i].point_id for _, i in problem.rows}
        ],
        "rejected_track_count": sum(track.point_id is None for track in tracks),
    }
    metadata = copy.deepcopy(scene.metadata)
    if accepted:
        metadata.update(
            scale_status="arbitrary_visual_scale_unverified",
            quality_status="bundle_adjusted_unverified",
        )
        metadata["baseline_convention"] = {
            f"{a.image_id}-{b.image_id}": float(np.linalg.norm(a.extrinsics.C_w - b.extrinsics.C_w))
            for a, b in pairwise(cameras)
        }
        if "pose_closure" in metadata:
            metadata["input_pose_closure"] = metadata.pop("pose_closure")
        metadata["warnings"] = [
            f"Input reconstruction: {warning}" for warning in metadata.get("warnings", [])
        ]
        metadata["warnings"].append(
            "Bundle adjustment fits the retained observations; physical scale "
            "and geometric correctness remain unverified"
        )
    if not solved.success:
        metadata.setdefault("warnings", []).append(
            "Bundle adjustment stopped without solver convergence; inspect its termination report"
        )
    if not accepted:
        metadata.setdefault("warnings", []).append(
            "Bundle-adjustment candidate rejected: invalid depth, nonfinite values "
            "or increased robust cost"
        )
    metadata["bundle_adjustment"] = report
    return BundleResult(Scene(tuple(cameras), tuple(tracks), tuple(points), metadata), report)
