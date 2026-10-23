"""One compass world alignment, visual photo rotations and gyro interpolation.

Lecture 002 pp. 36–45/81–83: orthonormal changes of world/camera reference.
The compass construction, stationary checks and inertial propagation extend the
lecture; see docs/math/visual_heading.md. Magnetic north is not true north.
"""

from dataclasses import replace
from itertools import combinations

import numpy as np
from scipy.spatial.transform import Rotation

from distance_measuring.camera import CameraExtrinsics
from distance_measuring.vision.overlap import require_connected_overlap

from .experimental import (
    back_camera_from_device,
    gyro_orientation_interpolator,
    orientation_interpolator,
)


def stationary_calibration(at, acceleration, gt, gyro, window):
    """Validate a declared rest interval, then estimate its constant gyro offset.

    Sensor checks cannot distinguish every slow motion from bias. The capture
    protocol must actually hold the phone still. One pose does not identify full
    accelerometer bias; its mean is used only as a gravity-direction proxy.
    """
    start, end = window
    if not np.isfinite(window).all() or end - start < 1:
        raise ValueError("The declared stationary window must span at least one second")
    at, acceleration, gt, gyro = [np.asarray(x, float) for x in (at, acceleration, gt, gyro)]
    a, g = acceleration[(at >= start) & (at <= end)], gyro[(gt >= start) & (gt <= end)]
    result = {
        "window_s": [float(start), float(end)],
        "passed": False,
        "gyro_bias_rad_s": [0.0, 0.0, 0.0],
        "accelerometer_bias_estimated": False,
    }
    for times in (at, gt):
        t = times[(times >= start) & (times <= end)]
        if len(t) < 30 or t[0] > start + 0.05 or t[-1] < end - 0.05 or np.max(np.diff(t)) > 0.05:
            return dict(result, reason="insufficient_stationary_window_coverage")
    gravity, bias = np.mean(a, axis=0), np.mean(g, axis=0)
    result.update(
        acceleration_std_m_s2=np.std(a, axis=0).tolist(),
        gyro_std_rad_s=np.std(g, axis=0).tolist(),
        gravity_direction_proxy_device_m_s2=gravity.tolist(),
    )
    if (
        not 9.3 <= np.linalg.norm(gravity) <= 10.3
        or np.max(np.std(a, axis=0)) > 0.05
        or np.max(np.std(g, axis=0)) > 0.005
        or np.linalg.norm(bias) > 0.03
    ):
        return dict(result, reason="declared_window_contains_motion_or_unexpected_gravity")
    return dict(result, passed=True, reason=None, gyro_bias_rad_s=bias.tolist())


def compass_reference(gravity_device, magnetic_device):
    """Device -> east/magnetic-north/up from one gravity/compass observation."""
    gravity, magnetic = np.asarray(gravity_device, float), np.asarray(magnetic_device, float)
    if (
        gravity.shape != (3,)
        or magnetic.shape != (3,)
        or not np.isfinite([gravity, magnetic]).all()
        or np.linalg.norm(gravity) < 1e-8
    ):
        raise ValueError("Compass reference needs finite gravity and magnetic vectors")
    if not 10 <= np.linalg.norm(magnetic) <= 100:
        raise ValueError(
            "Magnetic field outside the configured 10–100 microtesla plausibility gate"
        )
    up = gravity / np.linalg.norm(gravity)
    horizontal = magnetic - (magnetic @ up) * up
    if np.linalg.norm(horizontal) < 5:
        raise ValueError("Insufficient horizontal magnetic field for heading")
    # Lecture 002 pp. 36–39: construct a right-handed orthonormal basis.
    # Magnetic north is the horizontal compass component; east = north × up.
    north = horizontal / np.linalg.norm(horizontal)
    east = np.cross(north, up)
    return Rotation.from_matrix(np.vstack([east, north, up]))


def rotate_scene_world(scene, world_rotation, heading_metadata):
    """One world rotation preserves every relative image pose, ray and distance."""
    Q = world_rotation.as_matrix()
    # Lecture 002 pp. 81–83: m~K R(P-C). With P'=QP, C'=QC, use R'=R Q.T.
    cameras = tuple(
        replace(c, extrinsics=CameraExtrinsics(c.extrinsics.R_cw @ Q.T, Q @ c.extrinsics.C_w))
        for c in scene.cameras
    )
    points = tuple(replace(p, position=tuple(Q @ p.position)) for p in scene.points)
    return replace(
        scene,
        cameras=cameras,
        points=points,
        metadata={**scene.metadata, "heading": heading_metadata},
    )


class VisualAnchoredOrientation:
    """Anchor gyro heading to images using world-vertical rotations only.

    Visual camera poses remain unchanged. Gyro tilt is preserved during inertial
    propagation: a heading correction cannot rotate gravity into acceleration.
    One common device-fixed axis defines heading for all images. Corrections are
    interpolated between photos and held constant outside them. No compass
    measurements enter these relative corrections.
    """

    def __init__(
        self, gyro_orientation, image_times, image_orientations, *, heading_axis_device=(0, 1, 0)
    ):
        times = np.asarray(image_times, float)
        if (
            times.ndim != 1
            or len(times) < 2
            or not np.isfinite(times).all()
            or np.any(np.diff(times) <= 0)
        ):
            raise ValueError("Need at least two increasing image times")
        if image_orientations.as_matrix().shape != (len(times), 3, 3):
            raise ValueError("Image orientations must align with image times")
        self.gyro = gyro_orientation
        self.times = times
        axis = np.asarray(heading_axis_device, float)
        if axis.shape != (3,) or not np.isfinite(axis).all() or np.linalg.norm(axis) < 1e-8:
            raise ValueError("Heading needs a finite nonzero device-fixed reference axis")
        self.heading_axis_device = axis / np.linalg.norm(axis)
        actual = gyro_orientation(times).apply(self.heading_axis_device)
        target = image_orientations.apply(self.heading_axis_device)
        if (
            min(
                np.linalg.norm(actual[:, :2], axis=1).min(),
                np.linalg.norm(target[:, :2], axis=1).min(),
            )
            < 0.05
        ):
            raise ValueError("Heading reference axis is nearly vertical in an image")
        # Lecture 002 pp. 36–45: signed angle between horizontal projections;
        # rotate ONLY about world Z. For up=[0,0,1], Rz(yaw)*up=up exactly.
        difference = np.arctan2(target[:, 1], target[:, 0]) - np.arctan2(actual[:, 1], actual[:, 0])
        self.correction_angles = np.unwrap(np.arctan2(np.sin(difference), np.cos(difference)))

    def __call__(self, times):
        angles = np.interp(times, self.times, self.correction_angles)
        correction = Rotation.from_rotvec(np.asarray(angles)[..., None] * np.array([0, 0, 1]))
        return correction * self.gyro(times)


def scene_overlap(scene, min_shared_points=8):
    """Count unique reconstructed points with acceptable observations per edge."""
    counts = {}
    for track in scene.tracks:
        if track.point_id is None:
            continue
        ids = sorted(
            o.image_id
            for o in track.observations
            if o.reprojection_error_px is not None and o.reprojection_error_px <= 3
        )
        for pair in combinations(ids, 2):
            counts[pair] = counts.get(pair, 0) + 1
    return require_connected_overlap(
        [c.image_id for c in scene.cameras],
        [p for p, n in counts.items() if n >= min_shared_points],
        stage="reconstructed shared points",
    )


def prepare_visual_heading(session, scene, *, stationary_window=(0.1, 1.6)):
    """Return a globally aligned scene, continuous orientation and provenance.

    An unavailable/untrusted compass leaves an explicit relative-only result.
    Disconnected images always raise, before any output is saved. No scale fit.
    """
    overlap = scene_overlap(scene)
    camera_ids = {c.image_id for c in scene.cameras}
    images = sorted(
        (e for e in session.images if e.index in camera_ids), key=lambda e: e.timestamp_ns
    )
    if len(images) != len(camera_ids):
        raise ValueError("Every scene camera must have an image in the selected recording")
    if scene.metadata.get("session_id") != session.metadata.session_id:
        raise ValueError("Scene and recording describe different sessions")
    mounts = {e.metadata.sensor_orientation_degrees for e in images}
    if len(mounts) != 1 or any(
        e.metadata.lens_facing != 1 or e.metadata.target_rotation != 0 or e.metadata.is_flipped
        for e in images
    ):
        raise ValueError(
            "Nominal camera/IMU mounting needs an unmirrored back camera at native rotation"
        )
    R_cd = Rotation.from_matrix(back_camera_from_device(mounts.pop()))
    streams = session.sensor_streams

    def arrays(name):
        samples = streams.get(name, ())
        return (
            np.array([(s.timestamp_ns - session.start_ns) / 1e9 for s in samples]),
            np.array([[s.x, s.y, s.z] for s in samples]),
        )

    at, acceleration = arrays("accelerometer")
    gt, gyro = arrays("gyroscope")
    if min(len(at), len(gt)) < 2:
        raise ValueError("Heading needs accelerometer and gyro streams")
    if np.max(np.diff(gt)) > 0.25:
        raise ValueError("Gyro gaps exceed 0.25 seconds; cannot propagate continuous orientation")
    calibration = stationary_calibration(at, acceleration, gt, gyro, stationary_window)
    corrected_gyro = gyro - calibration["gyro_bias_rad_s"]
    identity_gyro = gyro_orientation_interpolator(gt, corrected_gyro, Rotation.identity())
    magnetic = streams.get("magnetometer", ())
    compass = {"used": False, "reason": "magnetometer_not_recorded", "north": "magnetic"}
    reference = None
    midpoint = sum(stationary_window) / 2
    if magnetic:
        if not calibration["passed"]:
            compass["reason"] = "stationary_reference_failed"
        else:
            window = [
                s
                for s in magnetic
                if stationary_window[0]
                <= (s.timestamp_ns - session.start_ns) / 1e9
                <= stationary_window[1]
            ]
            times = np.array([(s.timestamp_ns - session.start_ns) / 1e9 for s in window])
            vectors = np.array([[s.x, s.y, s.z] for s in window])
            if (
                len(window) < 10
                or times[0] > stationary_window[0] + 0.1
                or times[-1] < stationary_window[1] - 0.1
                or np.max(np.diff(times)) > 0.15
            ):
                compass["reason"] = "insufficient_compass_reference_coverage"
            elif any(s.accuracy < 2 for s in window):
                compass["reason"] = "unreliable_compass_accuracy"
            else:
                median = np.median(vectors, axis=0)
                if np.max(np.linalg.norm(vectors - median, axis=1)) > 0.1 * np.linalg.norm(median):
                    compass["reason"] = "unstable_magnetic_reference"
                else:
                    try:
                        reference = compass_reference(
                            calibration["gravity_direction_proxy_device_m_s2"], median
                        )
                        compass.update(
                            used=True,
                            reason=None,
                            reference_time_s=midpoint,
                            magnetic_device_microtesla=median.tolist(),
                        )
                    except ValueError as error:
                        compass["reason"] = str(error)
    if reference is not None:
        initial = reference * identity_gyro(midpoint).inv()
        heading_source = "single_stationary_compass_reference"
    else:
        rotation = streams.get("rotation_vector", ())
        if rotation:
            rt = np.array([(s.timestamp_ns - session.start_ns) / 1e9 for s in rotation])
            covered = (gt >= rt[0]) & (gt <= rt[-1])
            gt, corrected_gyro = gt[covered], corrected_gyro[covered]
            if len(gt) < 2:
                raise ValueError("Insufficient shared gyro/orientation coverage")
            initial = orientation_interpolator(rt, [[s.x, s.y, s.z, s.w] for s in rotation])(gt[0])
            heading_source = "initial_fused_reference_raw_compass_unavailable_or_rejected"
        elif calibration["passed"]:
            gravity = np.array(calibration["gravity_direction_proxy_device_m_s2"])
            reference, _ = Rotation.align_vectors([[0, 0, 1]], [gravity / np.linalg.norm(gravity)])
            initial = reference * identity_gyro(midpoint).inv()
            heading_source = "gravity_reference_arbitrary_heading"
        else:
            raise ValueError("Need a valid rest reference or initial rotation-vector orientation")
    propagated = gyro_orientation_interpolator(gt, corrected_gyro, initial)
    times = np.array([(e.timestamp_ns - session.start_ns) / 1e9 for e in images])
    if times[0] < gt[0] or times[-1] > gt[-1]:
        raise ValueError("Gyro coverage must bracket every image timestamp")
    cameras = {c.image_id: c for c in scene.cameras}
    first = cameras[images[0].index]
    # Lecture 002 pp. 38–45/81–83: visual world -> reference world. Apply this
    # ONE rotation to every camera/point; no compass-derived per-image yaw.
    world_rotation = propagated(times[0]) * R_cd.inv() * Rotation.from_matrix(first.extrinsics.R_cw)
    metadata = {
        "world_alignment_source": heading_source,
        "compass": compass,
        "relative_image_orientation_source": "visual_reconstruction",
        "inter_image_orientation_source": "gyro_tilt_with_visual_heading_corrections",
        "visual_correction_axes": "world_vertical_only",
        "R_reference_visual": world_rotation.as_matrix().tolist(),
        "camera_imu_mounting_calibrated": False,
        "magnetic_reference_corrected_during_recording": False,
        "stationary_calibration": calibration,
        "overlap": overlap,
        "metric_scale_accepted": False,
    }
    orientations = Rotation.from_matrix(
        [
            world_rotation.as_matrix() @ cameras[e.index].extrinsics.R_cw.T @ R_cd.as_matrix()
            for e in images
        ]
    )
    # A fixed image axis must have a usable horizontal projection throughout.
    # Prefer the best-conditioned of camera forward/up/right; this handles both
    # desk-facing and upright captures. The choice uses geometry, never ruler size.
    camera_axes = [
        ("camera_forward", [0, 0, 1]),
        ("camera_up", [0, -1, 0]),
        ("camera_right", [1, 0, 0]),
    ]
    candidates = [(name, R_cd.inv().apply(axis)) for name, axis in camera_axes]

    def conditioning(candidate):
        axis = candidate[1]
        return min(
            np.linalg.norm(R.apply(axis)[:, :2], axis=1).min()
            for R in (orientations, propagated(times))
        )

    axis_name, axis = max(candidates, key=conditioning)
    metadata["heading_axis"] = axis_name
    metadata["heading_axis_device"] = axis.tolist()
    aligned = rotate_scene_world(scene, world_rotation, metadata)
    continuous = VisualAnchoredOrientation(
        propagated, times, orientations, heading_axis_device=axis
    )
    photo_rows = []
    for e, t, R in zip(images, times, orientations, strict=True):
        forward = (R * R_cd.inv()).apply([0, 0, 1])
        heading = (
            None
            if np.linalg.norm(forward[:2]) < 1e-6
            else float(np.degrees(np.arctan2(forward[0], forward[1])) % 360)
        )
        photo_rows.append(
            {
                "image_id": e.index,
                "time_s": float(t),
                "timestamp_source": e.timestamp_source,
                "visual_device_to_reference_xyzw": R.as_quat().tolist(),
                "camera_forward_azimuth_degrees": heading,
                "image_heading_degrees": float(
                    np.degrees(np.arctan2(R.apply(axis)[0], R.apply(axis)[1])) % 360
                ),
                "gyro_with_visual_heading_xyzw": continuous(t).as_quat().tolist(),
            }
        )
    report = {
        **metadata,
        "session_id": session.metadata.session_id,
        "photos": photo_rows,
        "continuous_orientation_coverage_s": [float(gt[0]), float(gt[-1])],
        "scale_applied": False,
    }
    return aligned, continuous, report
