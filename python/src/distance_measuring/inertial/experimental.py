"""Experimental orientation, gravity compensation and visual/acceleration scale.

Lecture 002 pp. 36–45, 81–83 supplies rigid-frame geometry (PPTX page order).
Android sensor conventions and acceleration integration are additional material;
see docs/math/inertial_ruler_retry.md for primary references and assumptions.
"""

import numpy as np
from scipy.spatial.transform import Rotation, Slerp


def back_camera_from_device(sensor_orientation_degrees):
    """Ideal Android device -> stored back-camera axes; zero lever arm assumed.

    This nominal mounting is not an estimated/calibrated IMU-camera extrinsic.
    """
    if sensor_orientation_degrees not in (0, 90, 180, 270):
        raise ValueError("Sensor orientation must be a quarter turn")
    # Lecture 002 pp. 38–45, 81: compose orthonormal changes of reference.
    # Native-screen camera coordinates are (device X, -device Y, -device Z).
    # SENSOR_ORIENTATION rotates stored pixels clockwise into that native view.
    stored_to_native = Rotation.from_euler(
        "z", sensor_orientation_degrees, degrees=True
    ).as_matrix()
    return stored_to_native.T @ np.diag([1.0, -1.0, -1.0])


def orientation_interpolator(times, quaternions):
    """Android recorded (x,y,z,w) -> interpolated device-to-ENU rotations."""
    times, quaternions = np.asarray(times, float), np.asarray(quaternions, float)
    if (
        times.ndim != 1
        or len(times) < 2
        or quaternions.shape != (len(times), 4)
        or not np.isfinite(times).all()
        or not np.isfinite(quaternions).all()
        or np.any(np.diff(times) <= 0)
        or np.any(np.linalg.norm(quaternions, axis=1) < 1e-12)
    ):
        raise ValueError("Need increasing times and aligned finite nonzero xyzw quaternions")
    return Slerp(times, Rotation.from_quat(quaternions))


def gyro_orientation_interpolator(times, angular_velocity, initial_orientation):
    """Integrate device-frame gyro rad/s, seeded by one device-to-world rotation.

    Midpoint increments compose on the right because the gyro axes move with the
    device. Bias is assumed zero; this is a short-recording sensitivity trial.
    """
    times, angular_velocity = np.asarray(times, float), np.asarray(angular_velocity, float)
    if (
        times.ndim != 1
        or len(times) < 2
        or angular_velocity.shape != (len(times), 3)
        or not np.isfinite(times).all()
        or not np.isfinite(angular_velocity).all()
        or np.any(np.diff(times) <= 0)
        or initial_orientation.as_matrix().shape != (3, 3)
    ):
        raise ValueError("Need increasing gyro times, finite Nx3 rates and one initial rotation")
    orientations = [initial_orientation]
    for i, dt in enumerate(np.diff(times)):
        increment = Rotation.from_rotvec(0.5 * (angular_velocity[i] + angular_velocity[i + 1]) * dt)
        # Lecture 002 pp. 38–45: compose rotations of changing reference frames.
        # Gyro integration itself is additional inertial material.
        orientations.append(orientations[-1] * increment)
    return Slerp(times, Rotation.concatenate(orientations))


def integrate_interval(times, acceleration, start, end):
    """Exact integration of a piecewise-linear acceleration; initial velocity zero.

    Acceleration is in a fixed world frame, gravity already removed, in m/s².
    Return displacement in metres and velocity increment in m/s. Actual
    displacement also includes the unknown initial_velocity * (end-start).
    """
    times, acceleration = np.asarray(times, float), np.asarray(acceleration, float)
    if (
        times.ndim != 1
        or len(times) < 2
        or acceleration.shape != (len(times), 3)
        or not np.isfinite(times).all()
        or not np.isfinite(acceleration).all()
        or np.any(np.diff(times) <= 0)
    ):
        raise ValueError("Need increasing times and aligned finite Nx3 acceleration")
    if not np.isfinite([start, end]).all() or not times[0] <= start < end <= times[-1]:
        raise ValueError("Integration interval must lie inside the sampled timeline")
    t = np.r_[start, times[(times > start) & (times < end)], end]
    a = np.column_stack([np.interp(t, times, acceleration[:, k]) for k in range(3)])
    position, velocity = np.zeros(3), np.zeros(3)
    for i, h in enumerate(np.diff(t)):
        # Piecewise-linear a: p+=v*h+a0*h²/2+(a1-a0)*h²/6.
        # This kinematic integration is not a formula from the camera lecture.
        position += velocity * h + a[i] * h * h / 2 + (a[i + 1] - a[i]) * h * h / 6
        velocity += h * (a[i] + a[i + 1]) / 2
    return position, velocity


def fit_visual_scale(centers, elapsed, integrated_displacements):
    """Fit s*ΔC=v0*dt+integral(a), with a fixed assumed acceleration bias.

    Also report the rank when a free constant world acceleration bias is added.
    A positive fixed-bias solution is an experimental candidate, not certification.
    """
    centers, elapsed, displacement = [
        np.asarray(x, float) for x in (centers, elapsed, integrated_displacements)
    ]
    if centers.ndim != 2:
        raise ValueError("Centers must be an Nx3 array")
    n = len(centers)
    if (
        centers.shape != (n, 3)
        or n < 2
        or elapsed.shape != (n,)
        or displacement.shape != (n - 1, 3)
        or not all(np.isfinite(x).all() for x in (centers, elapsed, displacement))
        or abs(elapsed[0]) > 1e-12
        or np.any(np.diff(elapsed) <= 0)
    ):
        raise ValueError("Need >=2 ordered centers/times and aligned displacement integrals")
    dt = elapsed[1:]
    delta = centers[1:] - centers[0]
    A = np.column_stack([delta.ravel(), np.vstack([-t * np.eye(3) for t in dt])])
    result, _, rank, singular = np.linalg.lstsq(A, displacement.ravel(), rcond=None)
    # Two images give three equations for scale plus three unknown velocity
    # components. Return an explicit rank-deficient trial, never a metric scale.
    residual = A @ result - displacement.ravel()
    # If bias is free, s*ΔC-v0*dt+0.5*b*dt²=integral(a_measured).
    # Three poses supply six equations for seven unknowns: scale is generally
    # not observable jointly with velocity and bias from these constraints alone.
    augmented = np.c_[A, np.vstack([0.5 * t * t * np.eye(3) for t in dt])]
    bias_rank = int(np.linalg.matrix_rank(augmented))
    positive = bool(rank == 4 and result[0] > 0)
    return {
        "raw_scale_solution_m_per_unit": float(result[0]),
        "positive_scale_candidate_m_per_unit": float(result[0]) if positive else None,
        "initial_velocity_world_m_s": result[1:].tolist(),
        "fixed_bias_design_rank": int(rank),
        "fixed_bias_singular_values": singular.tolist(),
        "residual_rms_m": float(np.sqrt(np.mean(residual**2))),
        "free_bias_design_rank": bias_rank,
        "free_bias_parameter_count": 7,
        "scale_velocity_bias_jointly_observable": bias_rank == 7,
        "metric_scale_accepted": False,
        "rejection_reason": (
            "unobservable_with_free_acceleration_bias"
            if bias_rank < 7
            else "acceleration_bias_not_estimated"
        )
        if positive
        else ("nonpositive_scale" if rank == 4 else "unobservable_scale"),
    }


def three_pose_scale_diagnostics(centers, elapsed, integrated_displacements, reference_scale):
    """Explain a three-pose scale fit; reference scale is a comparison input only.

    Eliminate initial velocity by dividing each position equation by its elapsed
    time and subtracting. A constant WORLD acceleration error can then explain
    any chosen scale. This is a sensitivity calculation, not bias calibration.
    See docs/math/inertial_ruler_retry.md; kinematics extends the camera lecture.
    """
    centers, elapsed, displacement = [
        np.asarray(x, float) for x in (centers, elapsed, integrated_displacements)
    ]
    fit = fit_visual_scale(centers, elapsed, displacement)  # shared input validation
    if len(centers) != 3 or not np.isfinite(reference_scale) or reference_scale <= 0:
        raise ValueError("Need exactly three poses and a finite positive reference scale")
    dt = elapsed[1:]
    delta = centers[1:] - centers[0]
    visual = delta[1] / dt[1] - delta[0] / dt[0]
    inertial = displacement[1] / dt[1] - displacement[0] / dt[0]
    nv, ni = np.linalg.norm(visual), np.linalg.norm(inertial)
    observable = fit["fixed_bias_design_rank"] == 4
    # Lecture 002 p. 36: Euclidean norm/dot product in one orthonormal frame.
    # The remaining velocity-eliminated equation is s*visual=inertial.
    cosine = float(np.clip(visual @ inertial / (nv * ni), -1, 1)) if nv * ni > 0 else None
    family = []
    for scale in [0.0, fit["raw_scale_solution_m_per_unit"], float(reference_scale)]:
        bias = 2 * (inertial - scale * visual) / (dt[1] - dt[0])
        corrected = displacement - 0.5 * dt[:, None] ** 2 * bias
        velocity = (scale * delta[0] - corrected[0]) / dt[0]
        residual = scale * delta - dt[:, None] * velocity - corrected
        family.append(
            {
                "assumed_scale_m_per_unit": scale,
                "world_acceleration_error_to_subtract_m_s2": bias.tolist(),
                "acceleration_error_norm_m_s2": float(np.linalg.norm(bias)),
                "initial_velocity_world_m_s": velocity.tolist(),
                "position_residual_rms_m": float(np.sqrt(np.mean(residual**2))),
            }
        )
    return {
        "visual_velocity_difference_units_s": visual.tolist(),
        "inertial_velocity_difference_m_s": inertial.tolist(),
        "direction_difference_degrees": None
        if cosine is None or not observable
        else float(np.degrees(np.arccos(cosine))),
        "magnitude_ratio_m_per_unit": float(ni / nv) if observable else None,
        "projection_scale_m_per_unit": float(visual @ inertial / (nv * nv)) if observable else None,
        "reference_scale_m_per_unit_comparison_only": float(reference_scale),
        "constant_world_error_family": family,
        "bias_calibrated": False,
        "metric_scale_accepted": False,
    }
