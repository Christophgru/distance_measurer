# M8 desktop viewer and point selection

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
