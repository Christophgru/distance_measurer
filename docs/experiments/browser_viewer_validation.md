# GPU reconstruction inspection

The latest usable visual reconstruction is exported as a single offline file:

`data/processed/2026-10-23_091847_034_be3e7ada/interactive_scene/index.html`

It contains **941 accepted sparse points**, cameras **2, 3, 7**, calibrated
frustum drawing geometry, original photo context, observation associations and
source hashes. The output is about **1.09 MB**, including renderer and JPEGs.
No camera, point, calibration or metric scale is changed. Source hashes match
all six inputs after export. The source scene SHA-256 remains
`07f3a0bc21ed850d50216f18ea76d64631002cb6e7445f486156ef1657d1678f`.

The browser renderer uses Three.js 0.186.1 and one GPU point-cloud buffer.
Packed data retains stable point/track IDs and the full observation mapping.
Shader filters and ID-color picking avoid creating one render object per point.
Rendering occurs on interaction, resizing and data changes rather than in an
idle animation loop. Photo planes sit at a drawing depth and are not surfaces.

## Checks performed

**28 Python viewer/export tests pass**, including existing desktop viewer
checks and new coverage of packed geometry/IDs/observations, calibrated camera
frustums, local detail at large world offsets, safe inline JSON, source
immutability, photo colors, EXIF metadata and protected output paths.

The Playwright/Chromium smoke test loads the real artifact with network access
disabled and verifies actual GPU point picking, stable-ID search, orbit, pan,
zoom, filter counts, camera focus/view, photo planes and mobile controls.
There are no page/console errors or HTTP(S) requests. The rendered three-camera
scene uses **9 draw calls**. A separate temporary **synthetic 100,000-point**
fixture also uses **9 draw calls**. This confirms batching, not a hardware frame
rate: this headless host uses SwiftShader. The synthetic data is not added to
the user's scene. The real artifact's browser screenshot was inspected.

The Python wheel builds successfully and includes all three offline assets:
HTML template, renderer bundle and MIT license. Ruff lint/format and
`git diff --check` pass. A local HTTP preview of the exported file returns 200.

See [usage and controls](../architecture/scene_viewer.md) and
[browser build/test instructions](../../tools/scene_viewer/README.md).
