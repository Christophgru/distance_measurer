import json
from pathlib import Path

import numpy as np
from numpy.testing import assert_allclose
from scipy.spatial.transform import Rotation

from distance_measuring.camera import CameraIntrinsics
from distance_measuring.inertial.experimental import (
    back_camera_from_device,
    gyro_orientation_interpolator,
)
from distance_measuring.vision.parallax import rotation_compensated_parallax


def test_android_contract_fixtures_match_existing_p3_helpers():
    path = Path(__file__).resolve().parents[3] / "tools/validation/preview_gyro_cases.json"
    for case in json.loads(path.read_text())["cases"]:
        timestamps = np.array(case["timestamps_ns"], np.int64)
        start, end = case["start_ns"], case["end_ns"]
        times = np.r_[
            0.0,
            (timestamps[(timestamps > start) & (timestamps < end)] - start) / 1e9,
            (end - start) / 1e9,
        ]
        rates = np.column_stack(
            [
                np.interp(times, (timestamps - start) / 1e9, np.array(case["rates"])[:, i])
                for i in range(3)
            ]
        )
        device = (
            gyro_orientation_interpolator(times, rates, Rotation.identity())(times[-1])
            .inv()
            .as_matrix()
        )
        assert_allclose(device.ravel(), case["R_device_second_first"], atol=1e-12)
        mount = back_camera_from_device(case["sensor_orientation"])
        camera = mount @ device @ mount.T
        assert_allclose(camera.ravel(), case["R_camera_second_first"], atol=1e-12)

        def intrinsics(name, case=case):
            fx, fy, cx, cy, skew = case[name]
            return CameraIntrinsics(fx, fy, cx, cy, 640, 480, skew=skew)

        result = rotation_compensated_parallax(
            case["source_pixels"],
            case["source_pixels"],
            intrinsics("first_intrinsics"),
            intrinsics("second_intrinsics"),
            camera,
        )
        assert_allclose(result.predicted_pixels, case["predicted_pixels"], atol=1e-12)
