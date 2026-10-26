"""Short-interval gyro rotation, reusing the existing midpoint/Slerp integrator."""

from bisect import bisect_left, bisect_right
from dataclasses import asdict, dataclass
from itertools import pairwise
from time import perf_counter

import numpy as np
from scipy.spatial.transform import Rotation

from distance_measuring.camera import CameraExtrinsics
from distance_measuring.dataset.schema import ImageMetadata, Session

from .experimental import back_camera_from_device, gyro_orientation_interpolator


@dataclass(frozen=True)
class GyroInterval:
    R_device_second_first: np.ndarray
    start_timestamp_ns: int
    end_timestamp_ns: int
    first_sample_timestamp_ns: int
    last_sample_timestamp_ns: int
    sample_count: int
    integration_sample_count: int
    interpolated_endpoint_count: int
    sample_span_s: float
    max_sample_gap_s: float
    runtime_s: float

    def to_dict(self):
        result = asdict(self)
        result["R_device_second_first"] = self.R_device_second_first.tolist()
        result["interval_duration_s"] = (self.end_timestamp_ns - self.start_timestamp_ns) / 1e9
        return result


def integrate_gyro_interval(
    session: Session,
    start_timestamp_ns: int,
    end_timestamp_ns: int,
    *,
    max_gap_s: float = 0.25,
) -> GyroInterval:
    """Map a fixed ray in first-device axes into second-device axes.

    Input timestamps must share the recorded gyro's monotonic clock. Clip the
    bracketing samples to the exact endpoints with linear rate interpolation.
    Subtract integer nanoseconds BEFORE float conversion. No bias/position/scale
    is estimated. The reused integrator returns device-to-world orientation;
    its inverse gives the passive first-to-second ray transform returned here.
    """
    start = perf_counter()
    if any(type(t) is not int or t < 0 for t in (start_timestamp_ns, end_timestamp_ns)):
        raise ValueError("Gyro interval timestamps must be nonnegative integer nanoseconds")
    if end_timestamp_ns <= start_timestamp_ns:
        raise ValueError("Gyro interval must have strictly increasing endpoints")
    if not np.isfinite(max_gap_s) or max_gap_s <= 0:
        raise ValueError("max_gap_s must be positive and finite")
    stream = session.sensor_streams.get("gyroscope", ())
    times = [sample.timestamp_ns for sample in stream]
    if len(times) < 2 or any(b <= a for a, b in pairwise(times)):
        raise ValueError("Need at least two strictly increasing gyro samples")
    if start_timestamp_ns < times[0] or end_timestamp_ns > times[-1]:
        raise ValueError(
            "Gyro samples must bracket both image timestamps; extrapolation is disabled"
        )
    left = bisect_right(times, start_timestamp_ns) - 1
    right = bisect_left(times, end_timestamp_ns)
    samples = stream[left : right + 1]
    sample_times = [(sample.timestamp_ns - start_timestamp_ns) / 1e9 for sample in samples]
    max_gap = max(b.timestamp_ns - a.timestamp_ns for a, b in pairwise(samples)) / 1e9
    if max_gap > max_gap_s:
        raise ValueError(f"Gyro sample gap {max_gap:.6f} s exceeds {max_gap_s:.6f} s")
    rates = np.array([[sample.x, sample.y, sample.z] for sample in samples])
    duration = (end_timestamp_ns - start_timestamp_ns) / 1e9
    interior = [t for t in sample_times if 0 < t < duration]
    integration_times = np.array([0.0, *interior, duration])
    interpolated_rates = np.column_stack(
        [np.interp(integration_times, sample_times, rates[:, axis]) for axis in range(3)]
    )
    orientation = gyro_orientation_interpolator(
        integration_times, interpolated_rates, Rotation.identity()
    )
    return GyroInterval(
        orientation(duration).inv().as_matrix(),
        start_timestamp_ns,
        end_timestamp_ns,
        samples[0].timestamp_ns,
        samples[-1].timestamp_ns,
        len(samples),
        len(integration_times),
        int(start_timestamp_ns not in times) + int(end_timestamp_ns not in times),
        (samples[-1].timestamp_ns - samples[0].timestamp_ns) / 1e9,
        float(max_gap),
        perf_counter() - start,
    )


def camera_ray_rotation(
    R_device_second_first,
    first: ImageMetadata,
    second: ImageMetadata,
) -> np.ndarray:
    """Passive C1 -> C2 rays: M2 R_d2_d1 M1.T, with nominal stored-image mounts.

    As in prepare_visual_heading, only unmirrored back cameras at native target
    rotation are supported. EXIF display rotation never rotates the working rays.
    A known camera/lens change invalidates this uncalibrated mounting assumption.
    """
    for metadata in (first, second):
        if (
            metadata.lens_facing != 1
            or metadata.target_rotation != 0
            or metadata.is_flipped is not False
        ):
            raise ValueError(
                "Nominal mounting requires an unmirrored back camera at native target rotation"
            )
        if metadata.sensor_orientation_degrees not in (0, 90, 180, 270):
            raise ValueError("Nominal mounting requires a recorded quarter-turn sensor orientation")
    for name in ("camera_id", "physical_camera_id"):
        a, b = getattr(first, name), getattr(second, name)
        if a is not None and b is not None and a != b:
            raise ValueError(f"{name} changed; nominal camera/IMU mounting is unvalidated")
    rotation = CameraExtrinsics(R_device_second_first, np.zeros(3)).R_cw
    first_mount = back_camera_from_device(first.sensor_orientation_degrees)
    second_mount = back_camera_from_device(second.sensor_orientation_degrees)
    return second_mount @ rotation @ first_mount.T
