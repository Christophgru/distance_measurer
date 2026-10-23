import json
from dataclasses import replace
from types import SimpleNamespace as Obj

import cv2
import numpy as np
from numpy.testing import assert_allclose
from scipy.spatial.transform import Rotation

from distance_measuring.camera import CameraExtrinsics
from distance_measuring.vision.reference_model import compare_visual_cameras, scene_from_colmap
from distance_measuring.vision.visual_reference import prepare_visual_images, verify_sources


def test_visual_preparation_does_not_read_sensor_csv_and_preserves_stored_pixels(capture, tmp_path):
    (capture.path / "sensors.csv").unlink()
    regions = {
        "coordinate_frame": "normalized_stored_pixels_ignore_exif",
        "exclude_images": {
            str(i): [
                [[0, 0], [0.1, 0], [0.1, 0.1], [0, 0.1]],
                [[0.9, 0.9], [1, 0.9], [1, 1], [0.9, 1]],
                [[0.4, 0.4], [0.6, 0.4], [0.6, 0.6], [0.4, 0.6]],
            ]
            for i in range(3)
        },
    }
    source = tmp_path / "regions.json"
    source.write_text(json.dumps(regions))
    output = tmp_path / "visual"
    manifest = prepare_visual_images(capture.path, output, source)
    assert not manifest["sensor_data_used"] and not manifest["ruler_lengths_used"]
    assert not any("sensors.csv" in p for p in manifest["input_sha256"])
    raw = cv2.imread(
        str(capture.path / "images/image_000.jpg"), cv2.IMREAD_COLOR | cv2.IMREAD_IGNORE_ORIENTATION
    )
    prepared = cv2.imread(str(output / "input/images/image_000.png"))
    assert np.array_equal(raw, prepared)
    mask = cv2.imread(str(output / "input/masks/image_000.png.png"), cv2.IMREAD_GRAYSCALE)
    assert mask[0, 0] == 0 and mask[24, 40] == 255  # ruler texture remains ordinary visual input
    verify_sources(manifest)
    assert prepare_visual_images(capture.path, output, source) == manifest


def test_camera_comparison_removes_only_global_visual_gauge(bundle_scene_factory):
    old, _, _ = bundle_scene_factory()
    Q = Rotation.from_euler("xyz", [23, -18, 40], degrees=True).as_matrix()
    ratio = 3.5
    t = np.array([4, -3, 2])
    new = replace(
        old,
        cameras=tuple(
            replace(
                c,
                extrinsics=CameraExtrinsics(
                    c.extrinsics.R_cw @ Q.T, ratio * Q @ c.extrinsics.C_w + t
                ),
            )
            for c in old.cameras
        ),
    )
    report = compare_visual_cameras(old, new)
    assert_allclose(report["display_similarity"]["rotation"], Q, atol=1e-12)
    assert_allclose(report["display_similarity"]["visual_unit_ratio"], ratio, atol=1e-12)
    assert max(r["relative_rotation_difference_degrees"] for r in report["relations"]) < 1e-10
    assert max(r["baseline_ratio_difference_percent"] for r in report["relations"]) < 1e-10
    assert not report["metric_scale_estimated"] and not report["reference_is_ground_truth"]
    cameras = list(new.cameras)
    c = cameras[-1]
    cameras[-1] = replace(
        c, extrinsics=CameraExtrinsics(c.extrinsics.R_cw, c.extrinsics.C_w + [2, 0, 0])
    )
    distorted = compare_visual_cameras(old, replace(new, cameras=tuple(cameras)))
    assert max(r["baseline_ratio_difference_percent"] for r in distorted["relations"]) > 5


def test_reference_import_keeps_pose_and_converts_pixel_origin_once():
    K = np.array([[100.0, 0.0, 50.5], [0.0, 100.0, 40.5], [0.0, 0.0, 1.0]])
    camera = Obj(
        model_name="PINHOLE",
        params=[100.0, 100.0, 50.5, 40.5],
        width=100,
        height=80,
        camera_id=1,
        calibration_matrix=lambda: K.copy(),
    )
    xyz = np.array([0.0, 0.0, 5.0])
    images, elements = {}, []
    for i, center in [(1, np.zeros(3)), (2, np.array([1.0, 0.0, 0.0]))]:
        pose = Obj(rotation=Obj(matrix=lambda: np.eye(3)), translation=-center)
        v = K @ (xyz - center)
        pixels = [Obj(xy=v[:2] / v[2]), Obj(xy=v[:2] / v[2] + [2.0, 0.0])]
        images[i] = Obj(
            name=f"image_{i}.png",
            has_pose=True,
            camera=camera,
            cam_from_world=lambda p=pose: p,
            points2D=pixels,
        )
        elements.append(Obj(image_id=i, point2D_idx=0))
    elements.append(Obj(image_id=1, point2D_idx=1))
    point = Obj(xyz=xyz, track=Obj(elements=elements))
    model = Obj(images=images, points3D={42: point}, image=lambda i: images[i])
    manifest = {
        "session_id": "synthetic",
        "input_sha256": {},
        "images": [{"name": f"image_{i}.png", "image_id": i + 10} for i in [1, 2]],
    }
    scene = scene_from_colmap(model, manifest)
    assert len(scene.points) == 1 and scene.points[0].point_id == 42
    assert len(scene.tracks[0].observations) == 2
    assert scene.metadata["duplicate_orientation_observations_collapsed"] == 1
    assert_allclose(scene.cameras[0].intrinsics.K[:2, 2], [50.0, 40.0])
    assert_allclose(scene.cameras[1].extrinsics.C_w, [1.0, 0.0, 0.0])
    for c, observation in zip(scene.cameras, scene.tracks[0].observations, strict=True):
        assert_allclose(c.intrinsics.project(xyz, c.extrinsics), observation.pixel)
        assert observation.reprojection_error_px == 0.0
