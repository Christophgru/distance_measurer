"""Exercise the diagnostic against a fused heading jump absent from the gyro."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from numpy.testing import assert_allclose
from scipy.spatial.transform import Rotation


def test_heading_jump_is_detected_without_mistaking_it_for_physical_rotation(monkeypatch):
    directory = Path(__file__).resolve().parents[3] / "tools/validation"
    monkeypatch.syspath_prepend(str(directory))
    spec = importlib.util.spec_from_file_location(
        "scale_diagnostic", directory / "explain_scale_discrepancy.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    times = np.arange(0, 301) / 100
    orientation = Rotation.from_euler("z", np.where(times >= 1.5, 5.5, 0)[:, None], degrees=True)
    rotation = [
        SimpleNamespace(timestamp_ns=round(t * 1e9), x=q[0], y=q[1], z=q[2], w=q[3])
        for t, q in zip(times, orientation.as_quat(), strict=True)
    ]
    gyro = [SimpleNamespace(timestamp_ns=round(t * 1e9), x=0, y=0, z=0) for t in times]
    session = SimpleNamespace(
        start_ns=0,
        sensor_streams={"rotation_vector": rotation, "gyroscope": gyro},
        images=[
            SimpleNamespace(
                index=i, timestamp_ns=round(t * 1e9), callback_timestamp_ns=round((t + 0.1) * 1e9)
            )
            for i, t in enumerate([1, 2, 2.7])
        ],
    )
    result = module.orientation_jumps(session)
    event = result["largest_increment_disagreements"][0]
    assert_allclose([event["start_s"], event["end_s"]], [1.49, 1.5])
    assert_allclose(event["rotation_vector_increment_degrees"], 5.5)
    assert_allclose(event["gyro_increment_degrees"], 0)
    assert_allclose(event["axis_angle_to_vertical_degrees"], 0)
