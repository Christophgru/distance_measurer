"""Find and correlate pairwise matches before pose/scale estimation.

Lecture 002 pp. 136–144 (PPTX order) motivates correspondence and epipolar
checks. Affine-SIFT, RootSIFT, bidirectional ratios and cycles are extensions.
"""

import argparse
import base64
import hashlib
import json
import sys
from itertools import combinations
from pathlib import Path

import cv2
import numpy as np

from distance_measuring.camera import resolve_intrinsics
from distance_measuring.dataset import load_session
from distance_measuring.vision.consistency import (
    GroupedFeatures,
    closed_three_view_tracks,
    group_features,
    match_grouped,
    rootsift,
)
from distance_measuring.vision.correlation import correlate_pair_matches
from distance_measuring.vision.epipolar import estimate_fundamental
from distance_measuring.vision.overlap import DisconnectedOverlapError, require_connected_overlap
from distance_measuring.vision.pipeline import working_camera


def write_image(path, image):
    if not cv2.imwrite(str(path), image):
        raise OSError(f"Cannot write {path}")


def search_mask(regions, image_id, size):
    if regions is None:
        return np.full(size[::-1], 255, np.uint8)
    if regions.get("coordinate_frame") != "normalized_stored_pixels_ignore_exif":
        raise ValueError("Regions must use normalized_stored_pixels_ignore_exif")

    def polygon(value):
        points = np.asarray(value, dtype=float)
        if (
            points.ndim != 2
            or points.shape[1] != 2
            or len(points) < 3
            or not np.isfinite(points).all()
            or np.any(points < 0)
            or np.any(points > 1)
        ):
            raise ValueError("Region polygons must contain >=3 finite normalized 2D points")
        return np.rint(points * (np.array(size) - 1)).astype(np.int32)

    mask = np.zeros(size[::-1], np.uint8)
    cv2.fillPoly(mask, [polygon(regions["images"][str(image_id)])], 255)
    for excluded in regions.get("exclude_images", {}).get(str(image_id), []):
        cv2.fillPoly(mask, [polygon(excluded)], 0)
    if not mask.any():
        raise ValueError("Search region is empty")
    return mask


def review_artifacts(images, tracks, output, report, rotate):
    display = [cv2.rotate(im, cv2.ROTATE_90_CLOCKWISE) if rotate else im for im in images]
    height = images[0].shape[0]
    for track in tracks:
        track["display_pixels"] = [
            None if pixel is None else ([height - 1 - pixel[1], pixel[0]] if rotate else pixel)
            for pixel in track["working_pixels"]
        ]
    overview = np.hstack([cv2.cvtColor(im, cv2.COLOR_GRAY2BGR) for im in display])
    display_width = display[0].shape[1]
    context_tracks = [t for t in tracks if t.get("cycle_closed")]
    if not context_tracks:
        context_tracks = [t for t in tracks if t.get("usable_pairwise")][:40]
    for track in context_tracks:
        color = (70, 230, 70) if track["pairwise_epipolar_consistent"] else (0, 170, 255)
        for i, pixel in enumerate(track["display_pixels"]):
            if pixel is None:
                continue
            xy = tuple(np.rint(np.array(pixel) + [i * display_width, 0]).astype(int))
            cv2.circle(overview, xy, 5, color, 1, cv2.LINE_AA)
            cv2.putText(
                overview,
                str(track["match_id"]),
                xy,
                cv2.FONT_HERSHEY_SIMPLEX,
                0.45,
                color,
                1,
                cv2.LINE_AA,
            )
    write_image(output / "tracks_overview.jpg", overview)
    # Static cards are a preview; every match remains accessible in HTML/JSON.
    preview = []
    for dataset in dict.fromkeys(t["dataset_id"] for t in tracks if t.get("usable_pairwise")):
        preview.extend(
            [t for t in tracks if t["dataset_id"] == dataset and t.get("usable_pairwise")][:20]
        )
    preview += [t for t in tracks if t.get("cycle_closed")][:30]
    for path in output.glob("match_cards_*.jpg"):
        path.unlink()
    for page in range((len(preview) + 9) // 10):
        chunk = preview[page * 10 : (page + 1) * 10]
        canvas = np.full((len(chunk) * 150, 220 * len(images), 3), 25, np.uint8)
        for row, track in enumerate(chunk):
            for i, pixel in enumerate(track["display_pixels"]):
                if pixel is None:
                    continue
                patch = cv2.getRectSubPix(display[i], (88, 60), tuple(map(float, pixel)))
                patch = cv2.cvtColor(cv2.resize(patch, (220, 150)), cv2.COLOR_GRAY2BGR)
                cv2.drawMarker(patch, (110, 75), (0, 255, 255), cv2.MARKER_CROSS, 12, 1)
                color = (70, 230, 70) if track["pairwise_epipolar_consistent"] else (0, 170, 255)
                cv2.putText(
                    patch,
                    f"#{track['match_id']} / image {report['image_ids'][i]}",
                    (5, 17),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.5,
                    color,
                    1,
                )
                canvas[row * 150 : (row + 1) * 150, i * 220 : (i + 1) * 220] = patch
        write_image(output / f"match_cards_{page:02}.jpg", canvas)
    # Self-contained local review: no server, network, camera model or 3D warp.
    encoded = []
    for image in display:
        ok, jpg = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 90])
        if not ok:
            raise OSError("Cannot encode review image")
        encoded.append("data:image/jpeg;base64," + base64.b64encode(jpg).decode())
    payload = {
        "images": encoded,
        "image_ids": report["image_ids"],
        "tracks": tracks,
        "session_id": report["session_id"],
        "source_sha256": report["input_sha256"],
    }
    report["review_id"] = hashlib.sha256(
        json.dumps(
            {"tracks": tracks, "sources": report["input_sha256"], "settings": report["settings"]},
            sort_keys=True,
        ).encode()
    ).hexdigest()
    payload["review_id"] = report["review_id"]
    template = Path(__file__).with_name("match_review.html").read_text()
    (output / "review.html").write_text(
        template.replace("__AUDIT_DATA__", json.dumps(payload).replace("<", "\\u003c"))
    )


def run(args):
    session_path, output = args.session.resolve(), args.output.resolve()
    raw = Path(__file__).resolve().parents[2] / "data/raw"
    if output.is_relative_to(session_path) or output.is_relative_to(raw):
        raise ValueError("Audit outputs must be outside raw/session directories")
    count = len(args.image_indices)
    if count < 2 or len(set(args.image_indices)) != count:
        raise ValueError("Select at least two distinct image IDs")
    if not 0 < args.ratio < 1 or not np.isfinite(args.ratio):
        raise ValueError("ratio must be in (0,1)")
    if args.max_tilt not in range(4) or args.max_features < 1 or args.max_image_size < 32:
        raise ValueError("Use max-tilt 0..3, positive max-features and max-image-size >=32")
    session = load_session(session_path)
    by_id = {event.index: event for event in session.images}
    events = [by_id[i] for i in args.image_indices]
    regions = json.loads(args.regions.read_text()) if args.regions else None
    sources = [session_path / "session.json", session_path / "events.jsonl"]
    sources += [session.image_path(event) for event in events]
    if args.regions:
        sources.append(args.regions.resolve())
    if args.calibration:
        sources.append(args.calibration.resolve())
    hashes = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
    cache = None
    if args.reuse_features:
        cache_root = args.reuse_features.resolve()
        if output.is_relative_to(cache_root):
            raise ValueError("Output must not overwrite the feature cache")
        cache = json.loads((cache_root / "correspondences.json").read_text())
        if any(cache["input_sha256"].get(p) != h for p, h in hashes.items()):
            raise ValueError("Feature cache source hashes do not match")
        for key, value in [
            ("backend", "Affine-SIFT + RootSIFT"),
            ("max_tilt", args.max_tilt),
            ("max_features_per_simulated_view", args.max_features),
            ("max_image_size", args.max_image_size),
            ("contrast_threshold", 0.02),
            ("group_link_radius_px", 1.5),
            ("group_max_radius_px", 2.0),
        ]:
            if cache["settings"].get(key) != value:
                raise ValueError(f"Feature cache setting differs: {key}")
    output.mkdir(parents=True, exist_ok=True)
    images, features, image_records = [], [], []
    cv2.setNumThreads(4)
    for event in events:
        original = resolve_intrinsics(event.metadata, calibration_path=args.calibration)
        factor = min(1, args.max_image_size / max(original.image_size))
        camera, _ = working_camera(original, tuple(round(v * factor) for v in original.image_size))
        image = cv2.imread(
            str(session.image_path(event)), cv2.IMREAD_GRAYSCALE | cv2.IMREAD_IGNORE_ORIENTATION
        )
        if image is None:
            raise ValueError(f"Cannot decode image {event.index}")
        image = cv2.resize(image, camera.image_size, interpolation=cv2.INTER_AREA)
        if any(camera.distortion):
            image = camera.undistort_image(image)
        mask = search_mask(regions, event.index, camera.image_size)
        # Lecture 002 pp. 136–137: observations must describe corresponding
        # scene points. Affine view synthesis (Yu/Morel 2011) addresses viewpoint
        # changes beyond the lecture's pp. 141–144 block-matching examples.
        if cache is None:
            backend = cv2.SIFT_create(nfeatures=args.max_features, contrastThreshold=0.02)
            if args.max_tilt:
                backend = cv2.AffineFeature_create(backend, maxTilt=args.max_tilt)
            keys, descriptors = backend.detectAndCompute(image, mask)
            raw_count = len(keys)
            xy = np.array([k.pt for k in keys], dtype=float).reshape(-1, 2)
            descriptors = descriptors if descriptors is not None else np.empty((0, 128), np.float32)
            # Mapped-back synthetic detections must remain in the search mask.
            rounded = np.rint(xy).astype(int)
            visible = np.all(rounded >= 0, axis=1) & np.all(rounded < camera.image_size, axis=1)
            valid = np.flatnonzero(visible)
            valid = valid[mask[rounded[valid, 1], rounded[valid, 0]] > 0]
            xy = xy[valid]
            grouped = group_features(xy, rootsift(descriptors[valid]))
        else:
            previous = next(r for r in cache["images"] if r["image_id"] == event.index)
            if previous["working_intrinsics"] != camera.without_distortion().to_dict():
                raise ValueError("Cached working camera differs")
            cache_file = cache_root / f"image_{event.index:03}_features.npz"
            hashes[str(cache_file)] = hashlib.sha256(cache_file.read_bytes()).hexdigest()
            with np.load(cache_file, allow_pickle=False) as archive:
                xy = archive["raw_points"]
                grouped = GroupedFeatures(
                    archive["points"],
                    archive["descriptors"],
                    archive["group_ids"],
                    archive["raw_rows"],
                    previous["diffuse_clusters_rejected"],
                )
            raw_count = previous["raw_feature_count"]
        images.append(image)
        features.append(grouped)
        np.savez_compressed(
            output / f"image_{event.index:03}_features.npz",
            points=grouped.points,
            descriptors=grouped.descriptors,
            group_ids=grouped.group_ids,
            raw_points=xy,
            raw_rows=grouped.raw_rows,
        )
        image_records.append(
            {
                "image_id": event.index,
                "raw_feature_count": raw_count,
                "localized_group_count": len(grouped.points),
                "diffuse_clusters_rejected": grouped.rejected_cluster_count,
                "working_intrinsics": camera.without_distortion().to_dict(),
            }
        )
        print(
            f"Image {event.index}: {raw_count} variants -> {len(grouped.points)} localized groups",
            flush=True,
        )
    if len({im.shape for im in images}) != 1:
        raise ValueError("This review requires equal working image dimensions")
    pairs, geometry, pair_records, pairwise_records = {}, {}, [], []
    for i, j in combinations(range(count), 2):
        matches = match_grouped(features[i], features[j], ratio=args.ratio)
        pairs[i, j] = matches
        xy1, xy2 = (
            features[i].points[matches.indices[:, 0]],
            features[j].points[matches.indices[:, 1]],
        )
        # Lecture 002 p. 138: F checks image correspondences, independently of
        # old camera poses. It is not a metric 3x4 M or a nonplanarity guarantee.
        fit = estimate_fundamental(xy1, xy2, threshold_px=1.5, seed=0)
        correlation = correlate_pair_matches(
            images[i], images[j], xy1, xy2, support_mask=fit.inliers
        )
        correlated_count = 0
        first_record = len(pairwise_records)
        for row, (keys, pixels1, pixels2, corr, ok, error) in enumerate(
            zip(
                matches.indices,
                xy1,
                xy2,
                correlation,
                fit.inliers,
                fit.sampson_error_px,
                strict=True,
            )
        ):
            gids = [None] * count
            gids[i], gids[j] = map(int, keys)
            pixels = [None] * count
            pixels[i], pixels[j] = pixels1.tolist(), pixels2.tolist()
            correlated_count += bool(ok and corr["accepted"])
            pairwise_records.append(
                {
                    "match_id": f"p{args.image_indices[i]}-{args.image_indices[j]}:{row}",
                    "dataset_id": f"pair {args.image_indices[i]}–{args.image_indices[j]}",
                    "group_ids": gids,
                    "working_pixels": pixels,
                    "support_count": 2,
                    "cycle_closed": False,
                    "pairwise_epipolar_consistent": bool(ok),
                    "correlation": corr,
                    "usable_pairwise": bool(ok and corr["accepted"]),
                    "sampson_errors_px": [float(error) if np.isfinite(error) else None],
                    "manual_review": "unreviewed",
                }
            )
        # Keep independent image locations rather than counting nearby synthetic
        # variants twice. Correlation results remain visible for every candidate.
        pair_entries = pairwise_records[first_record:]
        unique = []
        for entry in sorted(pair_entries, key=lambda t: -(t["correlation"].get("score") or -1)):
            if not entry["usable_pairwise"]:
                continue
            duplicate = next(
                (
                    other
                    for other in unique
                    if any(
                        np.linalg.norm(
                            np.subtract(entry["working_pixels"][k], other["working_pixels"][k])
                        )
                        < 4
                        for k in (i, j)
                    )
                ),
                None,
            )
            if duplicate is not None:
                entry["usable_pairwise"] = False
                entry["spatial_duplicate_of"] = duplicate["match_id"]
            else:
                unique.append(entry)
        geometry[i, j] = {
            tuple(row): (bool(ok), float(error) if np.isfinite(error) else None)
            for row, ok, error in zip(
                matches.indices, fit.inliers, fit.sampson_error_px, strict=True
            )
        }
        name = f"pair_{args.image_indices[i]:03}_{args.image_indices[j]:03}.npz"
        arrays = {
            "indices": matches.indices,
            "ratios_both_directions": matches.ratios,
            "inliers": fit.inliers,
            "sampson_error_px": fit.sampson_error_px,
            "correlation_accepted": np.array([t["correlation"]["accepted"] for t in pair_entries]),
            "usable_pairwise": np.array([t["usable_pairwise"] for t in pair_entries]),
        }
        if fit.F is not None:
            arrays["F"] = fit.F
        np.savez_compressed(output / name, **arrays)
        pair_records.append(
            {
                "image_ids": [args.image_indices[i], args.image_indices[j]],
                "mutual_bidirectional_ratio_matches": len(matches.indices),
                "F_inliers": int(fit.inliers.sum()),
                "correlation_and_epipolar_consistent_count": correlated_count,
                "usable_pairwise_unique_count": len(unique),
                "failure": fit.failure,
                "file": name,
            }
        )
        print(
            f"Pair {args.image_indices[i]}–{args.image_indices[j]}: "
            f"{len(matches.indices)} matches, {fit.inliers.sum()} epipolar inliers, "
            f"{correlated_count} also pass patch correlation ({len(unique)} unique)",
            flush=True,
        )
    if count == 3:
        tracks, summary = closed_three_view_tracks(pairs, [f.points for f in features])
    else:
        tracks, summary = np.empty((0, 2), int), {"status": "not_required_for_pairwise_matching"}
    records = []
    for match_id, track in enumerate(tracks):
        checks = [
            geometry[i, j][int(track[i]), int(track[j])] for i, j in combinations(range(3), 2)
        ]
        records.append(
            {
                "match_id": f"t:{match_id}",
                "dataset_id": "three-view bonus",
                "support_count": 3,
                "group_ids": track.tolist(),
                "working_pixels": [features[i].points[key].tolist() for i, key in enumerate(track)],
                "cycle_closed": True,
                "pairwise_epipolar_consistent": all(ok for ok, _ in checks),
                "sampson_errors_px": [error for _, error in checks],
                "manual_review": "unreviewed",
            }
        )
    report = {
        "schema_version": 1,
        "session_id": session.metadata.session_id,
        "image_ids": args.image_indices,
        "coordinate_frame": "undistorted_working_pixels_ignore_exif",
        "input_sha256": hashes,
        "settings": {
            "backend": "Affine-SIFT + RootSIFT",
            "max_tilt": args.max_tilt,
            "max_features_per_simulated_view": args.max_features,
            "max_image_size": args.max_image_size,
            "contrast_threshold": 0.02,
            "ratio_both_directions": args.ratio,
            "group_link_radius_px": 1.5,
            "group_max_radius_px": 2.0,
            "track_min_separation_px": 4.0,
            "F_threshold_px": 1.5,
            "seed": 0,
        },
        "search_regions": regions,
        "images": image_records,
        "pairs": pair_records,
        "cycle_summary": summary,
        "tracks": records,
        "pose_or_scale_updated": False,
        "quality_status": "correspondence_candidates_for_review",
    }
    report["pairwise_matches"] = pairwise_records
    report["pairwise_usable_count"] = sum(t["usable_pairwise"] for t in pairwise_records)
    report["third_view_required"] = False
    try:
        report["overlap"] = require_connected_overlap(
            args.image_indices,
            [p["image_ids"] for p in pair_records if p["usable_pairwise_unique_count"] >= 8],
            stage="pairwise correlation and geometry",
        )
    except DisconnectedOverlapError as error:
        report["overlap"] = {
            "connected": False,
            "components": error.components,
            "failure": str(error),
        }
        report["quality_status"] = "disconnected_overlap"
    review_artifacts(
        images, pairwise_records + records, output, report, not args.stored_orientation
    )
    report["epipolar_consistent_track_count"] = sum(
        t["pairwise_epipolar_consistent"] for t in records
    )
    report["occupied_grid_cells_4x4"] = [
        len(
            {
                tuple(
                    np.minimum(
                        np.array(t["working_pixels"][i]) / np.array(images[i].shape[::-1]) * 4, 3
                    ).astype(int)
                )
                for t in records
            }
        )
        for i in range(count)
    ]
    report["pairwise_occupied_grid_cells_4x4"] = [
        len(
            {
                tuple(
                    np.minimum(
                        np.array(t["working_pixels"][i]) / np.array(images[i].shape[::-1]) * 4, 3
                    ).astype(int)
                )
                for t in pairwise_records
                if t["usable_pairwise"] and t["working_pixels"][i] is not None
            }
        )
        for i in range(count)
    ]
    if any(hashlib.sha256(Path(p).read_bytes()).hexdigest() != h for p, h in hashes.items()):
        raise RuntimeError("An input changed during matching")
    (output / "correspondences.json").write_text(
        json.dumps(report, indent=2, allow_nan=False) + "\n"
    )
    print(
        f"Closed unique tracks: {len(records)}; all-pair epipolar consistent: "
        f"{report['epipolar_consistent_track_count']}"
    )
    print(f"Review: {output / 'review.html'}")
    if not report["overlap"]["connected"]:
        print(report["overlap"]["failure"], file=sys.stderr)
        return 1
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--image-indices", type=int, nargs="+", default=[0, 1, 2])
    parser.add_argument(
        "--reuse-features",
        type=Path,
        help="Reuse a prior audit's features after source/setting checks",
    )
    parser.add_argument(
        "--regions",
        type=Path,
        help="Optional explicit search polygons; no automatic screen detection",
    )
    parser.add_argument("--calibration", type=Path)
    parser.add_argument("--max-image-size", type=int, default=1600)
    parser.add_argument("--max-features", type=int, default=2500)
    parser.add_argument("--max-tilt", type=int, default=3)
    parser.add_argument("--ratio", type=float, default=0.75)
    parser.add_argument(
        "--stored-orientation",
        action="store_true",
        help="Disable measurement01's clockwise display rotation",
    )
    args = parser.parse_args()
    try:
        return run(args)
    except (ValueError, OSError, KeyError, cv2.error) as error:
        parser.exit(2, f"Cannot audit correspondences: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
