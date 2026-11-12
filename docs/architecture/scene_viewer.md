# Scene viewers and point selection

For a new recording, start with the
**[dataset-to-web-UI walkthrough](run_dataset.md)**. It covers the current sparse
reconstruction and export path. Older dense and desktop examples are retained
below for reproduction.

## Large point-list files

Scene documents keep their existing schema. `save_scene()` writes JSON through
100,000 points and BSON above that boundary, returning the actual output path
(`scene.bson` when given `scene.json` for a larger scene). Both `load_scene()`
and the viewer/export commands accept either format. BSON uses PyMongo's
standalone codec; no MongoDB server is required. Integer timestamps retain
their full 64-bit precision.

Large fused clouds use bounded BSON point-list batches with a small JSON
manifest. Each record retains its ID, position, color, normal and supporting
image IDs. `.bson` files are authoritative; matching `.bson.gz` files reduce
browser transfer size. The browser decodes batches in a worker and renders
typed GPU buffers. Those multi-file viewers need a local HTTP server.

## Interactive browser viewer

The earlier dense image-only reference uses local BSON sidecars; follow the
[reference workflow](visual_reference.md) for that export. The command below
regenerates the earlier standalone 20k viewer.

For smooth GPU inspection without a desktop graphics window or Matplotlib,
export a self-contained Three.js/WebGL viewer:

```bash
.venv/bin/python python/scripts/export_scene_web.py \
  data/processed/2026-10-23_091847_034_be3e7ada/dense_20k/scene.json \
  --session data/raw/2026-10-23_091847_034_be3e7ada \
  --output data/processed/2026-10-23_091847_034_be3e7ada/interactive_scene/index.html
```

Open that HTML file in a WebGL 2 browser. The scene, renderer and optional JPEG
previews are embedded; no internet connection or server is needed. This earlier
export shows **20,000 image-supported points and cameras 2, 3, 7**: 941 original
sparse points plus 19,059 stereo samples. It displays the saved reconstruction
and added stereo measurements, without inventing a surface or repeating points.
Camera positions and point coordinates remain in **arbitrary reconstruction
units**. The separate [measurement prototype](measurement_prototype.md) uses
an explicit known-length reference; physical accuracy remains unaccepted.

Use these controls:

- Left-drag to orbit; right-drag or Shift-drag to pan; wheel to zoom. Touch uses
  one finger to orbit and two fingers to pan/zoom.
- **Overview** fits the central 98% of geometry to avoid extreme outliers
  setting the view. **Fit all** includes every point; **Cameras** fits their
  positions. These controls move the inspection viewpoint only.
- Click a point, or enter its persisted ID, to inspect its world position,
  track ID, observations and reprojection errors. Yellow lines show its
  observation rays to the cameras. Double-click a point to orbit around it.
- Select a camera by its numbered button or center marker. **Focus camera**
  puts the orbit center there. **View from camera** uses its saved center,
  optical direction, photo-up orientation and vertical field of view. The
  browser aspect changes the horizontal viewport; this is not a full-frame
  pixel-registration tool. The frustum itself uses exact saved `K/R/C` corners.
- Change point size, photo/error/view-count coloring, maximum reprojection
  error and minimum observation count. Filters change visibility only.
- **Added stereo samples** toggles the new samples; turning it off returns to
  the original 941-point cloud. Point inspection identifies each point's source.
  Dense stereo reprojection errors check consistency against fixed cameras and
  do not independently validate their positions.
- Toggle points, camera frustums, connecting lines, photo planes and world
  axes. Connecting lines join photo positions, not a continuous IMU path.
  Frustum/photo-plane size is a drawing depth, not a recovered surface distance.
- With source photos embedded, the photo panel marks the selected point's
  recorded observation. Click the photo for full screen. EXIF orientation is
  applied only to its preview; geometry remains in stored-pixel coordinates.

`--session` is optional. Without it the viewer has neutral point colors and
no photo planes/previews. With calibrated input, supply the same original
`--calibration FILE` used during matching. The exporter checks working `K`,
resizes/undistorts the photo consistently and samples a point's color from its
best-error observation. Source hashes, retained/excluded IDs and quality status
remain available under **Source and quality**.

The exporter never refits geometry, decimates the cloud, applies scale or writes
to its source scene/raw session. It stores float32 GPU positions relative to a
display origin and restores world coordinates in inspection labels. Persisted
IDs and observation associations remain exact; unsigned IDs above 32 bits are
rejected rather than truncated. The renderer supports up to 16,777,214 points.

If a browser/file launcher needs an HTTP URL, serve just the export folder:

```bash
.venv/bin/python -m http.server 8765 --bind 127.0.0.1 \
  --directory data/processed/2026-10-23_091847_034_be3e7ada/interactive_scene
```

Then open `http://localhost:8765/`. Renderer development and browser regression
instructions are in [tools/scene_viewer](../../tools/scene_viewer/README.md).
The GPU cloud is one draw call; controls redraw on demand. Real-browser tests
also render a synthetic 100,000-point cloud with the same total draw-call count
as this three-camera scene, without making a hardware frame-rate claim.

## More image-supported point samples

The earlier 20k input can be reproduced before the browser export above:

```bash
.venv/bin/python tools/validation/densify_scene.py \
  data/raw/2026-10-23_091847_034_be3e7ada \
  --scene data/processed/2026-10-23_091847_034_be3e7ada/visual_cameras_usable/scene.json \
  --regions tools/validation/measurement_20261023_091847_regions.json \
  --target-points 20000 \
  --output data/processed/2026-10-23_091847_034_be3e7ada/dense_20k
```

This keeps all original points and camera parameters, rectifies overlapping
images, checks bidirectional stereo and patch appearance, then triangulates
new point samples. It never pads the requested count with duplicates. Source
files remain unchanged. See [20k experiment results](../experiments/dense_20k_validation.md).

## Existing desktop viewer

From the repository root with the Python environment active, open the M7 scene:

```bash
python python/scripts/view_scene.py data/processed/measurement01/m7/scene.json
```

The Open3D window shows all accepted sparse points, colored camera-center markers,
wireframe frustums and forward-axis arrows. The title and console map recorded
camera IDs to colors: for `measurement01`, **0 red, 1 blue, 2 green**. Drag to
rotate, use the wheel to zoom, press H for Open3D help, and Q/Esc to close.
The viewer does not edit the scene.

Linux window mode needs an X/XWayland display and working OpenGL context. A
missing display/context produces an actionable error. Other desktop platforms
use Open3D's native window backend. For a server or exported preview, use:

```bash
python python/scripts/view_scene.py data/processed/measurement01/m7/scene.json \
    --no-window --preview data/processed/measurement01/m8/scene_preview.png
```

The headless preview uses Matplotlib, with the complete cloud and a separate
camera-detail panel showing camera IDs. It does not need Open3D's graphics
context. `--frustum-depth 0.3` optionally sets the drawing length in scene units.
Source scale and quality statuses appear in the preview; full warning text is
printed by the command.

## Choose stable point IDs

```bash
python python/scripts/view_scene.py data/processed/measurement01/m7/scene.json \
    --no-window --list-points
```

The table lists `point_id track_id views world_X world_Y world_Z`. IDs refer to
accepted persisted points, not renderer row numbers. Rejected M6 tracks have no
point to select. Choose one or two distinct IDs:

```bash
python python/scripts/view_scene.py data/processed/measurement01/m7/scene.json \
    --point-ids 0 1 \
    --selection-output data/processed/measurement01/m8/selection.json
```

Point A is gold and point B magenta. The title/console identify their IDs, and
the selection file preserves the supplied order. Add `--no-window --preview ...`
to highlight and label the same selection in a PNG. There is no mouse-picking
mode in this milestone; the point-ID CLI is the supported selection fallback.

The selection schema has version 1, source-scene SHA-256, world coordinate frame,
reconstruction units, source scale/quality/warnings, and one or two records with
A/B labels, highlight colors, point IDs, track IDs, world positions and original
image/feature/pixel observations. It contains no computed distances; M9 supplies
the measurement engine. A consumer must associate it with the same scene hash.

Exit **0** means listing/selection/preview completed or the desktop window closed
normally. Exit **2** means invalid IDs/options/input, I/O or display failure.
Unknown IDs, duplicate IDs, more than two IDs, invalid drawing depth, a selection
output without IDs, and colliding output paths fail before writing artifacts.
Preview/selection destinations under repository `data/raw/` or the input scene
directory are rejected, including symlink aliases. Outputs with the same names
are replaced on rerun. If window mode is requested, exports happen before the
window opens; on a headless host pass `--no-window` to finish without a display.

The scene is read once, and the selection hash identifies those exact bytes.
No camera, point, observation, ID or metadata is changed. See
[geometry and lecture pages](../math/scene_viewer.md) and
[M8 validation](../experiments/m8_viewer_validation.md).
