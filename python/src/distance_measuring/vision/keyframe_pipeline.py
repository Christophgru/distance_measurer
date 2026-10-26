"""Label immutable P3 exports without rerunning tracking or full geometry."""

import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from time import perf_counter

import numpy as np

from distance_measuring.scene.model import COORDINATE_FRAME, intrinsics_from_dict

from .keyframes import DEFAULT_KEYFRAME, score_keyframe
from .pose_pipeline import _artifact
from .tracking import TrackingConfig, grid_cell_ids


def score_keyframes(parallax_directory, output, *, config=DEFAULT_KEYFRAME):
    start = perf_counter()
    root, output = Path(parallax_directory).resolve(), Path(output).resolve()
    raw = Path(__file__).resolve().parents[4] / "data/raw"
    if output.is_relative_to(root) or output.is_relative_to(raw.resolve()):
        raise ValueError("Keyframe output must be outside raw and P3 input directories")
    path = root / "parallax.json"
    manifest = json.loads(path.read_text())
    if manifest["schema_version"] != 1 or manifest["coordinate_frame"] != COORDINATE_FRAME:
        raise ValueError("Keyframes require a P3 schema-1 ideal working-pixel export")
    selected = manifest["selected_image_indices"]
    images = {r["index"]: r for r in manifest["images"]}
    if (
        len(selected) < 2
        or any(type(i) is not int or i < 0 for i in selected)
        or selected != sorted(set(selected))
        or set(images) != set(selected)
        or len(images) != len(manifest["images"])
        or [p["image_indices"] for p in manifest["pairs"]]
        != [list(p) for p in zip(selected[:-1], selected[1:], strict=True)]
    ):
        raise ValueError("P3 images/pairs must follow distinct selected capture order")
    cameras = {i: intrinsics_from_dict(images[i]["working_intrinsics"]) for i in selected}
    hashes = {path: hashlib.sha256(path.read_bytes()).hexdigest()}
    frames = [
        {
            "image_id": selected[0],
            "label": "skip",
            "candidate": False,
            "score": 0.0,
            "reasons": ["initial_frame"],
        }
    ]
    grid = TrackingConfig(grid_rows=config.grid_rows, grid_cols=config.grid_cols)
    for pair in manifest["pairs"]:
        i, j = pair["image_indices"]
        if type(pair["available"]) is not bool:
            raise ValueError("P3 rotation availability must be boolean")
        cells, median, count = [0, 0], None, pair["input_track_count"]
        if pair["available"]:
            source = _artifact(root, pair["arrays_file"])
            hashes[source] = hashlib.sha256(source.read_bytes()).hexdigest()
            with np.load(source, allow_pickle=False) as arrays:
                ids, first, second, parallax = [
                    arrays[k]
                    for k in ("track_ids", "source_pixels", "target_pixels", "parallax_px")
                ]
            if (
                ids.shape != (count,)
                or ids.dtype.kind not in "iu"
                or len(set(ids)) != count
                or np.any(ids < 0)
                or first.shape != (count, 2)
                or second.shape != (count, 2)
                or parallax.shape != (count,)
                or np.isinf(parallax).any()
                or (parallax[np.isfinite(parallax)] < 0).any()
            ):
                raise ValueError("P3 keyframe IDs, pixels, parallax and counts must align")
            valid = np.isfinite(parallax)
            cells = [
                len(set(grid_cell_ids(p[valid], (c.image_height, c.image_width), grid)))
                for p, c in ((first, cameras[i]), (second, cameras[j]))
            ]
            finite = parallax[np.isfinite(parallax)]
            median = float(np.median(finite)) if len(finite) else None
            count = len(finite)
        decision = score_keyframe(
            count,
            *cells,
            median,
            cameras[j].image_size,
            rotation_available=pair["available"],
            config=config,
        )
        frames.append(
            {
                "image_id": j,
                "reference_image_id": i,
                "surviving_optical_track_count": pair["input_track_count"],
                **decision,
                "compensation_increases_median_motion": pair.get(
                    "compensation_increases_median_motion"
                ),
                "approximate_image_timestamps": pair.get("approximate_image_timestamps"),
            }
        )
    if any(hashlib.sha256(p.read_bytes()).hexdigest() != h for p, h in hashes.items()):
        raise ValueError("P3 inputs changed while scoring keyframes")
    output.mkdir(parents=True, exist_ok=False)
    report = {
        "schema_version": 1,
        "session_id": manifest["session_id"],
        "coordinate_frame": COORDINATE_FRAME,
        "settings": asdict(config),
        "frames": frames,
        "candidate_count": sum(r["candidate"] for r in frames),
        "quality_status": "keyframe_candidates_unverified_by_geometry",
        "input_quality_status": manifest.get("quality_status"),
        "warnings": manifest.get("warnings", []),
        "input_files_sha256": {str(p.relative_to(root)): h for p, h in hashes.items()},
        "total_runtime_s": perf_counter() - start,
    }
    (output / "keyframes.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print("Keyframe thresholds: " + json.dumps(asdict(config), sort_keys=True))
    for row in frames:
        print(
            f"Image {row['image_id']}: {row['label']} ({', '.join(row['reasons'])}); "
            f"score={row['score']:.3f}"
        )
    return report
