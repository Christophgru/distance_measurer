"""Compare recorded phone orientations with gyro-derived acceleration.

Lecture 002 pp. 36–45/81–83: compose orthonormal coordinate changes. Sensor
fusion, quaternion interpolation, gravity removal and gyro integration extend
that camera geometry; see docs/math/inertial_ruler_retry.md.
"""

import numpy as np
from scipy.spatial.transform import Rotation

from .experimental import gyro_orientation_interpolator, orientation_interpolator
from .heading import stationary_calibration


def align_heading_only(reference, other):
    """One constant world-Z alignment; preserve the other estimate's tilt.

    Project the relative rotation onto SO(2) by maximizing the horizontal trace.
    A full 3D alignment would conceal initial tilt disagreement.
    """
    relative = (reference * other.inv()).as_matrix()
    x = relative[0, 0] + relative[1, 1]
    y = relative[1, 0] - relative[0, 1]
    if np.hypot(x, y) < 1e-8:
        raise ValueError("Initial orientations do not supply a well-defined heading alignment")
    return Rotation.from_rotvec([0, 0, np.arctan2(y, x)])


def vector_statistics(vectors):
    lengths = np.linalg.norm(vectors, axis=1)
    return {
        "vector_rms": float(np.sqrt(np.mean(lengths**2))),
        "norm_p95": float(np.percentile(lengths, 95)),
        "norm_max": float(lengths.max()),
        "component_mean": np.mean(vectors, axis=0).tolist(),
    }


def increment_comparison(times, orientations, gyro_orientation):
    """Compare each native quaternion increment to gyro over the SAME interval."""
    observed = orientations[:-1].inv() * orientations[1:]
    propagated = gyro_orientation(times)
    predicted = propagated[:-1].inv() * propagated[1:]
    errors = np.degrees((observed * predicted.inv()).magnitude())
    events = []
    for i in np.argsort(errors)[-10:][::-1]:
        world_increment = (orientations[i + 1] * orientations[i].inv()).as_rotvec()
        norm = np.linalg.norm(world_increment)
        events.append(
            {
                "start_s": float(times[i]),
                "end_s": float(times[i + 1]),
                "recorded_increment_degrees": float(np.degrees(observed[i].magnitude())),
                "gyro_increment_degrees": float(np.degrees(predicted[i].magnitude())),
                "difference_degrees": float(errors[i]),
                "axis_angle_to_world_vertical_degrees": None
                if norm < 1e-12
                else float(np.degrees(np.arccos(np.clip(abs(world_increment[2]) / norm, 0, 1)))),
            }
        )
    return (
        {
            "difference_p95_degrees": float(np.percentile(errors, 95)),
            "difference_p99_degrees": float(np.percentile(errors, 99)),
            "difference_max_degrees": float(errors.max()),
            "increments_over_one_degree_disagreement": int(np.sum(errors > 1)),
            "largest_events": events,
        },
        0.5 * (times[:-1] + times[1:]),
        errors,
    )


def compare_recorded_imu(session, *, stationary_window=(0.1, 1.6)):
    """Same accelerometer and gravity, varied orientation; no visual/metric fit.

    The gyro is seeded once by the first common phone quaternion. The game
    vector has arbitrary heading: align it once about world up, not separately
    at each photo. Report disagreement, never designate a source ground truth.
    """
    streams = session.sensor_streams
    for name in ["accelerometer", "gyroscope", "rotation_vector"]:
        if len(streams.get(name, ())) < 2:
            raise ValueError(f"Comparison needs at least two {name} samples")

    def arrays(name):
        stream = streams.get(name, ())
        return (
            np.array([(s.timestamp_ns - session.start_ns) / 1e9 for s in stream]),
            np.array([[s.x, s.y, s.z] for s in stream]),
        )

    def interpolator(name):
        return orientation_interpolator(
            arrays(name)[0], [[s.x, s.y, s.z, s.w] for s in streams[name]]
        )

    at, raw = arrays("accelerometer")
    gt, rates = arrays("gyroscope")
    rt, _ = arrays("rotation_vector")
    starts, ends = [at[0], gt[0], rt[0]], [at[-1], gt[-1], rt[-1]]
    have_game = len(streams.get("game_rotation_vector", ())) >= 2
    if have_game:
        game_times, _ = arrays("game_rotation_vector")
        starts.append(game_times[0])
        ends.append(game_times[-1])
    start, end = max(starts), min(ends)
    if end <= start:
        raise ValueError("Sensor streams have no common time interval")
    grid = np.r_[start, gt[(gt > start) & (gt < end)], end]
    if np.max(np.diff(grid)) > 0.25:
        raise ValueError("Gyro gaps exceed 0.25 seconds")
    sampled_rates = np.column_stack([np.interp(grid, gt, rates[:, k]) for k in range(3)])
    phone = interpolator("rotation_vector")
    seed = phone(start)
    gyro = gyro_orientation_interpolator(grid, sampled_rates, seed)
    keep = (at >= start) & (at <= end)
    times, acceleration = at[keep], raw[keep]
    if len(times) < 2:
        raise ValueError("Too few accelerometer samples in common sensor coverage")
    calibration = stationary_calibration(at, raw, gt, rates, stationary_window)
    methods = {"integrated_gyro": gyro(times), "phone_rotation_vector": phone(times)}
    source = {
        "integrated_gyro": "Gyro integrated; no additional bias fit",
        "phone_rotation_vector": "Phone rotation vector (compass fusion)",
    }
    game_alignment = None
    if have_game:
        game = interpolator("game_rotation_vector")
        game_alignment = align_heading_only(seed, game(start))
        methods["phone_game_rotation_vector"] = game_alignment * game(times)
        source["phone_game_rotation_vector"] = "Phone game rotation vector (one heading alignment)"
    if calibration["passed"]:
        corrected = gyro_orientation_interpolator(
            grid, sampled_rates - calibration["gyro_bias_rad_s"], seed
        )
        methods["bias_corrected_gyro"] = corrected(times)
        source["bias_corrected_gyro"] = "Gyro integrated; declared-rest bias correction"
    gravity = np.array([0, 0, 9.80665])
    baseline = methods["integrated_gyro"]
    baseline_acceleration = baseline.apply(acceleration) - gravity
    arrays_out = {"time_s": times, "device_acceleration_including_gravity_m_s2": acceleration}
    method_reports = {}
    for name, orientation in methods.items():
        linear = orientation.apply(acceleration) - gravity
        # Equivalent device-frame equation: a_linear = a_measured - R.T*g.
        # Lecture 002 pp. 38–45: rotate the reference vector into device axes.
        predicted_gravity = orientation.inv().apply(gravity)
        arrays_out[name + "_gravity_device_m_s2"] = predicted_gravity
        arrays_out[name + "_linear_acceleration_device_m_s2"] = acceleration - predicted_gravity
        angle = np.degrees((baseline.inv() * orientation).magnitude())
        # Lecture 002 p. 36: compare up vectors in the same device basis.
        up0, up1 = baseline.inv().apply([0, 0, 1]), orientation.inv().apply([0, 0, 1])
        tilt = np.degrees(
            np.arctan2(np.linalg.norm(np.cross(up0, up1), axis=1), np.sum(up0 * up1, axis=1))
        )
        arrays_out[name + "_xyzw"] = orientation.as_quat()
        arrays_out[name + "_linear_acceleration_enu_m_s2"] = linear
        arrays_out[name + "_orientation_difference_degrees"] = angle
        arrays_out[name + "_tilt_difference_degrees"] = tilt
        method_reports[name] = {
            "label": source[name],
            "orientation_vs_gyro_degrees": {
                "p95": float(np.percentile(angle, 95)),
                "max": float(angle.max()),
                "end": float(angle[-1]),
            },
            "tilt_vs_gyro_degrees": {
                "p95": float(np.percentile(tilt, 95)),
                "max": float(tilt.max()),
                "end": float(tilt[-1]),
            },
            "estimated_linear_acceleration_m_s2": vector_statistics(linear),
            "linear_acceleration_difference_vs_gyro_m_s2": vector_statistics(
                linear - baseline_acceleration
            ),
        }
    increments = {}
    for name in ["rotation_vector", "game_rotation_vector"]:
        if len(streams.get(name, ())) < 2:
            continue
        native, _ = arrays(name)
        native = native[(native >= start) & (native <= end)]
        if len(native) < 2:
            continue
        orientation = interpolator(name)(native)
        if name == "game_rotation_vector":
            orientation = game_alignment * orientation
        increments[name], midpoint, difference = increment_comparison(native, orientation, gyro)
        arrays_out[name + "_increment_time_s"] = midpoint
        arrays_out[name + "_increment_difference_degrees"] = difference
    game_vs_phone = None
    if have_game:
        p, g = methods["phone_rotation_vector"], methods["phone_game_rotation_vector"]
        up0, up1 = p.inv().apply([0, 0, 1]), g.inv().apply([0, 0, 1])
        tilt = np.degrees(
            np.arctan2(np.linalg.norm(np.cross(up0, up1), axis=1), np.sum(up0 * up1, axis=1))
        )
        game_vs_phone = {
            "tilt_difference_max_degrees": float(tilt.max()),
            "estimated_linear_acceleration_difference_m_s2": vector_statistics(
                p.apply(acceleration) - g.apply(acceleration)
            ),
        }
    photos = []
    for e in session.images:
        frame = e.metadata.sensor_timestamp_ns
        request = e.request_timestamp_ns
        photos.append(
            {
                "image_id": e.index,
                "time_s": (e.timestamp_ns - session.start_ns) / 1e9,
                "timestamp_source": e.timestamp_source,
                "request_time_s": None if request is None else (request - session.start_ns) / 1e9,
                "camera_frame_time_s": None if frame is None else (frame - session.start_ns) / 1e9,
                "camera_frame_clock": e.metadata.sensor_timestamp_clock_domain,
                "camera_frame_minus_request_s": None
                if frame is None or request is None
                else (frame - request) / 1e9,
                "request_to_callback_s": None
                if request is None or e.callback_timestamp_ns is None
                else (e.callback_timestamp_ns - request) / 1e9,
            }
        )
    magnetic = None
    if streams.get("magnetometer"):
        mt, mv = arrays("magnetometer")
        arrays_out["magnetometer_time_s"] = mt
        arrays_out["magnetic_field_microtesla"] = mv
        strength = np.linalg.norm(mv, axis=1)
        magnetic = {
            "field_norm_microtesla": {
                "min": float(strength.min()),
                "median": float(np.median(strength)),
                "max": float(strength.max()),
            },
            "accuracy_statuses": sorted({s.accuracy for s in streams["magnetometer"]}),
        }
    report = {
        "schema_version": 1,
        "session_id": session.metadata.session_id,
        "common_coverage_s": [float(start), float(end)],
        "sample_count": len(times),
        "gravity_enu_m_s2": gravity.tolist(),
        "comparison_reference": "integrated_gyro_seeded_once_by_phone_orientation",
        "game_constant_heading_alignment_degrees": None
        if game_alignment is None
        else float(game_alignment.as_rotvec()[2] * 180 / np.pi),
        "stationary_calibration": calibration,
        "methods": method_reports,
        "native_increment_comparison": increments,
        "phone_game_vs_phone_rotation_vector": game_vs_phone,
        "magnetometer": magnetic,
        "photos": photos,
        "phone_linear_acceleration_stream_recorded": False,
        "orientation_accuracy_ground_truth_available": False,
        "visual_reconstruction_performed": False,
        "metric_scale_accepted": False,
    }
    return report, arrays_out
