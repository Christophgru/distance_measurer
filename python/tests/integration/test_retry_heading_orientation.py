import importlib.util
from dataclasses import replace
from pathlib import Path

import pytest

from distance_measuring.dataset import load_session


@pytest.mark.parametrize(
    "frame_times,rotation_vector", [(True, True), (False, True), (True, False)]
)
def test_retry_uses_frame_times_and_visual_heading_without_requiring_fused_orientation(
    capture_factory, bundle_scene_factory, monkeypatch, frame_times, rotation_vector
):
    directory = Path(__file__).resolve().parents[3] / "tools/validation"
    monkeypatch.syspath_prepend(str(directory))
    spec = importlib.util.spec_from_file_location("heading_retry", directory / "retry_ruler.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    capture = capture_factory(rotation_vector=rotation_vector)
    if not rotation_vector:
        capture.metadata["sensors"]["magnetometer"] = True
        capture.metadata["sensor_counts"]["magnetometer"] = 99
        for row in list(capture.rows):
            if row["sensor_type"] == "accelerometer":
                capture.rows.append(dict(row, sensor_type="magnetometer", x=0, y=30, z=-40, w=""))
        capture.rows.sort(key=lambda r: r["timestamp_ns"])
    for e in capture.events:
        if e["type"] != "image":
            continue
        e["metadata"].update(lens_facing=1, target_rotation=0, sensor_orientation_degrees=90)
        if frame_times:
            e["timestamp_ns"] += 1_000_000
            e["timestamp_source"] = "camera_frame"
            e["metadata"].update(
                sensor_timestamp_ns=e["timestamp_ns"],
                sensor_timestamp_clock_domain="android_elapsed_realtime_ns",
            )
    capture.write()
    scene, _, _ = bundle_scene_factory()
    mapping = {4: 0, 7: 1, 9: 2}
    scene = replace(
        scene,
        cameras=tuple(replace(c, image_id=mapping[c.image_id]) for c in scene.cameras),
        tracks=tuple(
            replace(
                t,
                observations=tuple(
                    replace(o, image_id=mapping[o.image_id]) for o in t.observations
                ),
            )
            for t in scene.tracks
        ),
        metadata={**scene.metadata, "session_id": capture.metadata["session_id"]},
    )
    seed = {"image_ids": [1, 2], "interval_lengths_reconstruction_units": [1, 1, 1]}
    result, _, _ = module.imu_experiment(
        load_session(capture.path),
        scene,
        seed,
        orientation_source="visual_anchored_gyroscope",
        stationary_window=[0, 1],
    )
    assert result["exposure_timestamps_available"] == frame_times
    assert {t["timing"] for t in result["scale_trials"]} == (
        {"camera_frame"}
        if frame_times
        else {"button_request", "request_callback_midpoint", "callback"}
    )
    assert not result["rotation_comparison_independent"] and not result["metric_scale_accepted"]
    assert result["visual_heading"]["visual_correction_axes"] == "world_vertical_only"
    if not rotation_vector:
        assert result["visual_heading"]["compass"]["used"]
