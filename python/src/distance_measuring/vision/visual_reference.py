"""Image-only SfM/MVS reference, independent of inertial/metric-scale research.

Lecture 002 pp. 65–76, 81–83, 98/101, 133–144 supplies K[R|-RC], homogeneous
least squares, ray geometry and correspondence. COLMAP SIFT, robust bundle
adjustment, radial self-calibration and PatchMatch MVS extend the lecture.
No sensor CSV, compass, known ruler length or metric scale is an input.
"""

import hashlib
import json
from pathlib import Path

import cv2
import numpy as np

from distance_measuring.camera import resolve_intrinsics
from distance_measuring.dataset.schema import ImageMetadata


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def verify_sources(manifest):
    for name, expected in manifest["input_sha256"].items():
        if hashlib.sha256(Path(name).read_bytes()).hexdigest() != expected:
            raise ValueError(f"Visual input changed: {name}")


def prepare_visual_images(session_path, output, regions_path):
    """Read only capture JSON/photos; prepare native stored-orientation images."""
    session_path, output = Path(session_path).resolve(), Path(output).resolve()
    if output.is_relative_to(session_path):
        raise ValueError("Visual outputs must be outside the raw capture")
    if (output / "manifest.json").exists():
        manifest = json.loads((output / "manifest.json").read_text())
        verify_sources(manifest)
        return manifest
    session_file, events_file = session_path / "session.json", session_path / "events.jsonl"
    session = json.loads(session_file.read_text())
    events = [json.loads(line) for line in events_file.read_text().splitlines() if line.strip()]
    events = [e for e in events if e["type"] == "image"]
    if len(events) < 2:
        raise ValueError("Visual recovery requires at least two images")
    regions = json.loads(Path(regions_path).read_text())
    if regions["coordinate_frame"] != "normalized_stored_pixels_ignore_exif":
        raise ValueError("Display masks must use normalized stored-pixel coordinates")
    images, masks = output / "input/images", output / "input/masks"
    images.mkdir(parents=True, exist_ok=True)
    masks.mkdir(parents=True, exist_ok=True)
    paths = [session_file, events_file, Path(regions_path).resolve()]
    manifest = {
        "schema_version": 1,
        "session_id": session["session_id"],
        "images": [],
        "scale_status": "not_estimated_visual_only",
        "sensor_data_used": False,
        "ruler_lengths_used": False,
        "ruler_texture_allowed": True,
        "mask_policy": "first_two_display_exclusions_only",
        "image_coordinate_frame": "COLMAP_corner_origin_pixels",
    }
    for event in sorted(events, key=lambda e: e["index"]):
        index = event["index"]
        source = (session_path / event["filename"]).resolve()
        if not source.is_relative_to(session_path):
            raise ValueError("Image filename escapes raw capture directory")
        paths.append(source)
        image = cv2.imread(str(source), cv2.IMREAD_COLOR | cv2.IMREAD_IGNORE_ORIENTATION)
        if image is None:
            raise ValueError(f"Cannot decode photo {index}")
        metadata = ImageMetadata.from_dict(event["metadata"])
        camera = resolve_intrinsics(metadata)
        if image.shape[:2] != camera.image_size[::-1]:
            raise ValueError("Stored photo dimensions differ from metadata")
        mask = np.full(image.shape[:2], 255, np.uint8)
        # These recording-specific masks list the two displays before the ruler.
        # Ordinary ruler texture can match, but its cm labels are never read.
        for polygon in regions["exclude_images"][str(index)][:2]:
            xy = np.asarray(polygon, float)
            if (
                xy.ndim != 2
                or xy.shape[1] != 2
                or not np.isfinite(xy).all()
                or np.any((xy < 0) | (xy > 1))
            ):
                raise ValueError("Invalid display exclusion polygon")
            cv2.fillPoly(
                mask, [np.rint(xy * (np.array(camera.image_size) - 1)).astype(np.int32)], 0
            )
        name = f"image_{index:03}.png"
        if not cv2.imwrite(str(images / name), image) or not cv2.imwrite(
            str(masks / (name + ".png")), mask
        ):
            raise OSError("Cannot write prepared visual image/mask")
        # COLMAP uses pixel corners as origin, unlike OpenCV's zero-based centers.
        manifest["images"].append(
            {
                "image_id": index,
                "name": name,
                "width": camera.image_width,
                "height": camera.image_height,
                "initial_focal_px": float((camera.fx + camera.fy) / 2),
                "exif_orientation": metadata.exif_orientation or 1,
                "timestamp_ns": event["timestamp_ns"],
                "source": str(source),
            }
        )
    sizes = {(r["width"], r["height"]) for r in manifest["images"]}
    if len(sizes) != 1:
        raise ValueError("This shared-lens reference requires equal native image dimensions")
    manifest["input_sha256"] = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    verify_sources(manifest)
    write_json(output / "manifest.json", manifest)
    return manifest


def recover_visual_reference(output, manifest, *, gpu_index="0"):
    """Joint image-only camera/point recovery; one arbitrary similarity gauge."""
    import pycolmap as pc

    output = Path(output)
    verify_sources(manifest)
    model_path = output / "sfm"
    if (model_path / "cameras.bin").exists():
        return pc.Reconstruction(model_path)
    if not pc.has_cuda:
        raise RuntimeError("Reference GPU pipeline requires pycolmap-cuda12")
    image_path, database = output / "input/images", output / "features.db"
    first = manifest["images"][0]
    reader = pc.ImageReaderOptions(
        camera_model="SIMPLE_RADIAL",
        mask_path=output / "input/masks",
        camera_params=f"{first['initial_focal_px']},{first['width'] / 2},{first['height'] / 2},0",
    )
    extraction = pc.FeatureExtractionOptions(
        max_image_size=4096, num_threads=8, gpu_index=gpu_index
    )
    extraction.sift.max_num_features = 32768
    extraction.sift.first_octave = 0  # Native 4K samples; avoid a 4x-area GPU upsample.
    extraction.sift.peak_threshold = 0.004
    pc.extract_features(
        database,
        image_path,
        camera_mode=pc.CameraMode.SINGLE,
        reader_options=reader,
        extraction_options=extraction,
        device=pc.Device.cuda,
    )
    matching = pc.FeatureMatchingOptions(
        num_threads=8, gpu_index=gpu_index, guided_matching=True, max_num_matches=32768
    )
    matching.sift.max_ratio = 0.75
    verification = pc.TwoViewGeometryOptions(min_num_inliers=30)
    verification.ransac.max_error = 2.0
    verification.ransac.confidence = 0.99999
    verification.ransac.random_seed = 0
    pc.match_exhaustive(
        database,
        matching_options=matching,
        verification_options=verification,
        device=pc.Device.cuda,
    )
    options = pc.IncrementalPipelineOptions(
        min_model_size=2,
        num_threads=8,
        random_seed=0,
        ba_global_max_num_iterations=100,
        ba_refine_focal_length=True,
        ba_refine_extra_params=True,
        ba_refine_principal_point=False,
    )
    options.mapper.abs_pose_max_error = 3.0
    options.mapper.filter_max_reproj_error = 2.5
    options.mapper.abs_pose_min_num_inliers = 30
    options.triangulation.ignore_two_view_tracks = False
    maps = pc.incremental_mapping(database, image_path, output / "models", options=options)
    if not maps:
        raise ValueError("Image-only mapping could not recover a connected scene")
    model = max(maps.values(), key=lambda m: (m.num_reg_images(), m.num_points3D()))
    if model.num_reg_images() < 2:
        raise ValueError("Fewer than two visual cameras registered")
    model_path.mkdir(exist_ok=True)
    model.write(model_path)
    model.export_PLY(output / "sparse.ply")
    registered = sorted(im.name for im in model.images.values() if im.has_pose)
    report = {
        "backend": f"COLMAP {pc.__version__}",
        "registered_images": registered,
        "unregistered_images": sorted({r["name"] for r in manifest["images"]} - set(registered)),
        "sparse_point_count": model.num_points3D(),
        "mean_reprojection_error_px": model.compute_mean_reprojection_error(),
        "calibration": [
            {"camera_id": c.camera_id, "model": c.model_name, "parameters": c.params.tolist()}
            for c in model.cameras.values()
        ],
        "scale_status": "not_estimated_visual_only",
        "metric_scale_used": False,
        "sensor_data_used": False,
        "ruler_lengths_used": False,
        "intrinsics_refined_from_images": True,
        "shared_lens": True,
        "feature_count_limit": 32768,
        "guided_bidirectional_matching": True,
        "input_sha256": manifest["input_sha256"],
    }
    verify_sources(manifest)
    write_json(output / "sparse_report.json", report)
    print(model.summary(), flush=True)
    return model


def recover_dense_reference(
    output,
    manifest,
    *,
    gpu_index="0",
    max_image_size=4096,
    min_ncc=0.5,
    min_support_views=3,
    max_reproj_error=1.0,
    max_depth_error=0.005,
    max_fusion_pixels=10000,
):
    """PatchMatch depth/normal estimation and strict multi-view fusion, no scale."""
    import pycolmap as pc

    if not np.isfinite(min_ncc) or not 0 < min_ncc < 1 or min_support_views not in [2, 3]:
        raise ValueError("NCC must lie between 0 and 1; support must be two or three views")
    if (
        not np.isfinite([max_reproj_error, max_depth_error]).all()
        or min(max_reproj_error, max_depth_error) <= 0
    ):
        raise ValueError("Fusion error limits must be positive and finite")
    if type(max_fusion_pixels) is not int or max_fusion_pixels < min_support_views:
        raise ValueError("Fusion aggregation limit must be an integer >= minimum support")
    output = Path(output)
    verify_sources(manifest)
    workspace = output / "mvs"
    report_file = output / "dense_report.json"
    if report_file.exists():
        cached = json.loads(report_file.read_text())
        if (
            cached["fused_point_count"] > 0
            and cached["patch_match"]["max_image_size"] == max_image_size
            and abs(cached["patch_match"]["filter_min_ncc"] - min_ncc) < 1e-7
            and cached["fusion"]["min_num_pixels"] == min_support_views
            and np.isclose(cached["fusion"]["max_reproj_error"], max_reproj_error)
            and np.isclose(cached["fusion"]["max_depth_error"], max_depth_error)
            and cached["fusion"]["max_num_pixels"] == max_fusion_pixels
            and cached["input_sha256"] == manifest["input_sha256"]
        ):
            verify_sources(manifest)
            return cached
        report_file.rename(output / "dense_report.previous.json")
    if not (workspace / "sparse/cameras.bin").exists():
        pc.undistort_images(
            workspace,
            output / "sfm",
            output / "input/images",
            num_patch_match_src_images=6,
            num_threads=4,
            undistort_options=pc.UndistortCameraOptions(max_image_size=max_image_size),
        )
    native = pc.Reconstruction(output / "sfm")
    ideal = pc.Reconstruction(workspace / "sparse")
    masks = workspace / "masks"
    masks.mkdir(exist_ok=True)
    native_images = {im.name: im for im in native.images.values() if im.has_pose}
    for image in ideal.images.values():
        if not image.has_pose:
            continue
        old = native_images[image.name].camera
        new = image.camera
        if old.model_name != "SIMPLE_RADIAL":
            raise ValueError("Reference mask conversion expects the declared SIMPLE_RADIAL lens")
        Kold = old.calibration_matrix()
        Kold[:2, 2] -= 0.5
        Knew = new.calibration_matrix()
        Knew[:2, 2] -= 0.5
        # Lecture 002 pp. 70–76/81–83: undistort masks into precisely the same
        # saved pinhole K as the photographs. Half-pixel origins are explicit.
        distortion = np.array([old.params[3], 0, 0, 0, 0], float)
        mapx, mapy = cv2.initUndistortRectifyMap(
            Kold, distortion, None, Knew, (new.width, new.height), cv2.CV_32FC1
        )
        mask = cv2.imread(str(output / "input/masks" / (image.name + ".png")), cv2.IMREAD_GRAYSCALE)
        mask = cv2.remap(mask, mapx, mapy, cv2.INTER_NEAREST, borderValue=0)
        mask = cv2.erode(mask, np.ones((11, 11), np.uint8))
        if not cv2.imwrite(str(masks / (image.name + ".png")), mask):
            raise OSError("Cannot save undistorted display mask")
    # The visibility file uses the MVS model's RegImageIds order, not raw IDs.
    order = [{"colmap_image_id": i, "name": ideal.image(i).name} for i in ideal.reg_image_ids()]
    write_json(output / "mvs_image_order.json", order)
    patch = pc.PatchMatchOptions(
        max_image_size=max_image_size,
        gpu_index=gpu_index,
        geom_consistency=True,
        num_iterations=5,
        filter=True,
        filter_min_ncc=min_ncc,
        filter_min_num_consistent=min_support_views - 1,
        filter_min_triangulation_angle=3,
        filter_geom_consistency_max_cost=1.0,
        cache_size=8,
        num_threads=4,
    )
    settings_file = output / "dense_settings.json"
    if settings_file.exists():
        old = json.loads(settings_file.read_text())["patch_match"]
        if old != patch.todict():
            # COLMAP skips existing maps even when options change. Preserve the
            # diagnostic maps, then recompute geometry with consistent settings.
            for folder in ["depth_maps", "normal_maps", "consistency_graphs"]:
                for path in (workspace / "stereo" / folder).glob("*.geometric.bin"):
                    path.rename(path.with_suffix(".bin.previous"))
    write_json(
        output / "dense_settings.json",
        {
            "patch_match": patch.todict(),
            "minimum_distinct_support_views": min_support_views,
            "scale_status": "not_estimated_visual_only",
        },
    )
    print("Starting native-resolution geometric multi-view stereo", flush=True)
    pc.patch_match_stereo(workspace, options=patch)
    fusion = pc.StereoFusionOptions(
        mask_path=masks,
        num_threads=8,
        min_num_pixels=min_support_views,
        max_num_pixels=max_fusion_pixels,
        max_reproj_error=max_reproj_error,
        max_depth_error=max_depth_error,
        max_normal_error=10,
        cache_size=8,
    )
    dense = pc.stereo_fusion(
        output / "fused.ply", workspace, input_type="geometric", options=fusion, output_type="ply"
    )
    if dense.num_points3D() == 0:
        raise ValueError("Dense fusion produced no supported points; no model was published")
    report = {
        "backend": f"COLMAP {pc.__version__}",
        "fused_point_count": dense.num_points3D(),
        "patch_match": patch.todict(),
        "fusion": fusion.todict(),
        "scale_status": "not_estimated_visual_only",
        "sensor_data_used": False,
        "metric_scale_used": False,
        "ruler_lengths_used": False,
        "registered_image_count": ideal.num_reg_images(),
        "minimum_distinct_support_views": min_support_views,
        "input_sha256": manifest["input_sha256"],
    }
    report["fusion"]["mask_path"] = str(masks)
    report["fusion"]["bounding_box"] = [v.tolist() for v in fusion.bounding_box]
    verify_sources(manifest)
    write_json(report_file, report)
    return report
