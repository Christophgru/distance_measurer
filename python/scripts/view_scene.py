"""View a sparse scene, list stable point IDs, or select one/two IDs for later measurements."""

import argparse
import hashlib
import json
import sys
from pathlib import Path

from distance_measuring.scene import Scene
from distance_measuring.visualization.scene_view import (
    SELECTION_NAMES,
    build_scene_view,
    selection_report,
)
from distance_measuring.visualization.viewer import save_preview, show_open3d


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scene", type=Path)
    parser.add_argument("--point-ids", type=int, nargs="+", default=[])
    parser.add_argument("--list-points", action="store_true")
    parser.add_argument("--no-window", action="store_true")
    parser.add_argument(
        "--preview", type=Path, help="Save a labeled PNG with a camera detail panel"
    )
    parser.add_argument("--selection-output", type=Path)
    parser.add_argument("--frustum-depth", type=float, help="Drawing length in scene units")
    args = parser.parse_args(argv)
    try:
        source = args.scene.resolve()
        raw_root = Path(__file__).resolve().parents[2] / "data" / "raw"
        destinations = [
            path.resolve() for path in (args.preview, args.selection_output) if path is not None
        ]
        if len(set(destinations)) != len(destinations):
            raise ValueError("Preview and selection report need different output paths")
        if any(
            path.is_relative_to(raw_root) or path.is_relative_to(source.parent)
            for path in destinations
        ):
            raise ValueError(
                "Viewer outputs must be outside data/raw and the input scene directory"
            )
        content = source.read_bytes()
        scene = Scene.from_dict(json.loads(content))
        view = build_scene_view(scene, tuple(args.point_ids), frustum_depth=args.frustum_depth)
        selection = (
            selection_report(scene, tuple(args.point_ids), hashlib.sha256(content).hexdigest())
            if args.selection_output
            else None
        )
        print(
            f"Scene: {len(view.points)} points, {len(view.cameras)} cameras; "
            "units: reconstruction units"
        )
        for camera in view.cameras:
            print(
                f"Camera {camera.image_id}: {camera.color_name}; "
                f"center={camera.vertices[0].tolist()}"
            )
        for warning in scene.metadata.get("warnings", []):
            print(f"Warning: {warning}")
        if args.list_points:
            tracks = {track.track_id: track for track in scene.tracks}
            print("point_id track_id views world_X world_Y world_Z")
            for point in scene.points:
                print(
                    f"{point.point_id} {point.track_id} {len(tracks[point.track_id].observations)} "
                    + " ".join(f"{value:.9g}" for value in point.position)
                )
        for i, point in enumerate(view.selected):
            print(
                f"{'AB'[i]}: point_id={point.point_id}, track_id={point.track_id}, "
                f"world={point.position}; highlight={SELECTION_NAMES[i]}"
            )
        if args.preview:
            save_preview(scene, view, args.preview)
            print(f"Preview: {args.preview}")
        if selection is not None:
            args.selection_output.parent.mkdir(parents=True, exist_ok=True)
            args.selection_output.write_text(
                json.dumps(selection, indent=2, allow_nan=False) + "\n"
            )
            print(f"Selection: {args.selection_output}")
        if not args.no_window:
            print(
                "Open3D controls: drag to rotate; wheel to zoom; H for help; Q/Esc to close",
                flush=True,
            )
            show_open3d(view)
    except (ValueError, OSError, RuntimeError, ImportError) as error:
        print(f"Cannot view scene: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
