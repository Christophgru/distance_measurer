"""Refine a persisted M6 scene and export the current scene plus solver metrics."""

import hashlib
import json
from pathlib import Path

import scipy

from distance_measuring.scene import Scene, save_scene

from .bundle import bundle_adjust


def refine_scene(scene_path: str | Path, output: str | Path, **options) -> dict:
    source, output = Path(scene_path).resolve(), Path(output).resolve()
    raw_root = Path(__file__).resolve().parents[4] / "data" / "raw"
    if output.is_relative_to(raw_root) or output.is_relative_to(source.parent):
        raise ValueError(
            "Bundle-adjustment outputs must be outside data/raw and the input scene directory"
        )
    source_bytes = source.read_bytes()
    original = Scene.from_dict(json.loads(source_bytes))
    result = bundle_adjust(original, **options)
    result.report["input_scene_sha256"] = hashlib.sha256(source_bytes).hexdigest()
    result.report["software"] = {"scipy": scipy.__version__}
    output.mkdir(parents=True, exist_ok=True)
    save_scene(output / "scene.json", result.scene)
    (output / "bundle_adjustment.json").write_text(
        json.dumps(result.report, indent=2, allow_nan=False) + "\n"
    )
    return result.report
