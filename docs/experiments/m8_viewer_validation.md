# M8 desktop-viewer verification

M8 adds an Open3D point-cloud/camera viewer, a headless PNG preview, stable-ID
listing/highlighting and one/two-point selection export. Code comments retain
lecture 002 slide/page references: homogeneous pixels on 65–68, K on 70–76,
R/C/full 3×4 projection on 81–83, and optical rays on 133–134. Frustum construction
inverts those equations; UI and selection behavior are explicit extensions.
See [original geometry notes](../math/scene_viewer.md). Earlier references and
local lecture assets remain unchanged.

## Automated checks

The Python suite passes **375 tests**, including **22 new M8 tests**. Repository
Ruff lint and formatting checks pass. Generated fixtures require neither the
lecture deck nor private captures.

- Frustum corners reproject to ideal pixel outer edges within `1e-10` pixels
  with unequal focal lengths/skew and rotated cameras. Camera centers and
  forward axes use the correct world-to-camera inverse; corner depth equals
  the requested drawing depth.
- Point IDs resolve correctly with gaps, reordered point arrays and reversed
  camera order. A/B order and highlight colors remain associated with persistent
  IDs; camera colors remain associated with recorded image IDs.
- Actual Open3D point-cloud and line/mesh objects are built without a window.
  Their coordinates/colors, explicit row-to-ID mapping and distinct camera colors
  agree with the pure geometry. Empty point clouds can still display cameras.
- Invalid IDs/depth, duplicate or excessive selections, missing selection IDs,
  colliding outputs, raw/input destinations and symlink aliases fail explicitly.
  Linux window mode without DISPLAY reports the headless fallback.
- Headless CLI listing, labeled PNG and selection JSON preserve exact source
  hashes, IDs/positions, source warnings and A/B labels. No distances are
  fabricated. Input scene bytes remain unchanged.

## measurement01 and actual Open3D rendering

```bash
.venv/bin/python python/scripts/view_scene.py \
    data/processed/measurement01/m7/scene.json --no-window --point-ids 0 1 \
    --preview data/processed/measurement01/m8/scene_preview.png \
    --selection-output data/processed/measurement01/m8/selection.json
```

The preview includes all **206 points**, camera centers/frustums for IDs **0,1,2**,
and selected point IDs **0,1** as A/B. Colors are 0 red, 1 blue, 2 green, A gold
and B magenta. The full-scene and camera-detail panels were visually inspected.
Frustum depth is approximately 0.3082 scene units, chosen only for drawing.
The final run verified the selection's source hash and unchanged SHA-256 hashes
for all **30** raw/M4/M5/M6/M7 input files.

An additional smoke run used **Open3D 0.20.0**, Xvfb and software OpenGL. It created
a real 1280×900 visualizer window, added one cloud, three frustum/center pairs
and two highlight markers, rendered five frames, captured `open3d_smoke.png` and
destroyed the window successfully. The screenshot was inspected, confirming the
point cloud, all three camera frustums and both highlighted points. This tests
actual rendering/window lifecycle; a human mouse-navigation session on a physical
desktop was not performed in this environment. Point selection uses the tested
CLI fallback rather than interactive picking.

The selection artifact retains source scale
`arbitrary_visual_scale_unverified`, quality `bundle_adjusted_unverified`,
and the inherited approximate-calibration/pose warnings. A/B coordinates are
reconstruction units. The viewer adds no new geometric validation or metric
scale. Preview, selection JSON and Open3D screenshot remain ignored under
`data/processed/measurement01/m8/`; no captured data or lecture material is committed.
