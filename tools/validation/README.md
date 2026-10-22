# Capture geometry and matching diagnostics

## Explain the ruler/acceleration size difference

After the pairwise ruler retry, run:

```bash
.venv/bin/python tools/validation/explain_scale_discrepancy.py
```

Open `data/processed/measurement01/scale_discrepancy/scale_discrepancy.png` and
the companion JSON. The experiment compares fused and gyro-integrated
orientation, checks heading jumps against gyro increments, decomposes the scale
fit into magnitude and direction, and quantifies timing/bias sensitivity.
It reproduces the original retry scales and checks input hashes before writing
to a separate directory. No metric scale is applied. See
[the numerical findings](../../docs/experiments/measurement01_scale_discrepancy.md).

## Check point matches before changing camera poses

Two overlapping photos are enough. Run the pairwise matcher and normalized
patch-correlation checks on recorded images 0 and 1:

```bash
.venv/bin/python tools/validation/audit_correspondences.py data/raw/measurement01 \
    --image-indices 0 1 \
    --regions tools/validation/measurement01_matching_regions.json \
    --output data/processed/measurement01/pairwise_matching
```

Select three images to check all three pairs independently, with optional
three-view cycles as a bonus:

```bash
.venv/bin/python tools/validation/audit_correspondences.py data/raw/measurement01 \
    --image-indices 0 1 2 \
    --regions tools/validation/measurement01_matching_regions.json \
    --output data/processed/measurement01/matching_review
```

The region file is a **manual desk/laptop/ruler search mask** for this recording.
It excludes the monitor-dominated upper areas. It contains no point
correspondences and supplies no calibration. Omit `--regions` to search entire
images. Defaults use a 1600-pixel longest edge, affine-SIFT through tilt index 3,
RootSIFT normalization and ratio 0.75 in both directions. `--max-tilt 0` uses
ordinary SIFT without affine simulation. Extraction and exact descriptor matching
can take several minutes on these photos.

Open the generated **`review.html`** directly in a browser. Choose an image pair,
inspect the same marked point in unwarped context and zoomed crops, then optionally
mark **Same point [G]** or **Wrong / uncertain [B]**. Arrow keys navigate; **U**
clears a decision. The default filter shows spatially distinct candidates that
pass both epipolar geometry and patch correlation. Uncheck it to inspect rejected,
unverified or duplicate candidates. An unused third image is explicitly marked
as unnecessary. Three-view cycles have their own optional selector.

The page works offline with embedded photos and makes no network requests.
Decisions are stored in the browser when local storage is available. **Download
review** exports decisions bound to the report's review ID and source hashes.
Export does not automatically change any reconstruction or camera poses.

Other outputs are:

- `correspondences.json`: full pairwise candidates, correlation scores/reasons,
  proposed refined locations, duplicate links, optional cycles and source hashes.
- `pair_*.npz`: aligned group IDs, forward/reverse ratios, F inliers, correlation
  acceptance and the final `usable_pairwise` mask. These are the new audit's
  group IDs, not M4/M7 feature or point IDs.
- `image_*_features.npz`: grouped image locations and their affine-view descriptors.
- `match_cards_*.jpg`: up to 20 passing pairwise matches per pair plus up to 30
  optional cycle tracks. These are previews; HTML/JSON retain every candidate.
- `tracks_overview.jpg`: optional cycle locations, or up to 40 passing pairwise
  matches when only two images are selected.

`--reuse-features PREVIOUS_AUDIT_DIRECTORY` can reuse extracted descriptors after
checking source hashes, working intrinsics and extraction settings. It recomputes
pair matching and correlation. Use a separate output directory. Source capture,
feature-cache and calibration files remain unchanged. Raw/session output paths
and overwriting a selected cache directory are rejected.

Patch correlation follows lecture 002 **pp. 141–144**, with explicit rejection
of flat texture and ambiguous peaks. Oblique patches use a local homography
estimated from neighboring matches in **that pair only**, with the query held
out of its own fit. A two-patch API also works directly without any neighboring
matches or camera geometry. See [the equations and API](../../docs/math/correspondence_consistency.md)
and [measurement01 results](../../docs/experiments/measurement01_matching_review.md).

These checks improve inspectability and reject common inconsistencies; passing
them does not prove physical identity, correct camera alignment or metric scale.
Existing M4–M9 artifacts and the M9 accuracy gate are preserved.

## Red/blue overlays and motion plots

Generate the red/blue image overlaps and Matplotlib phone-motion plot:

```bash
.venv/bin/python tools/validation/visualize_capture.py
```

The tool reads the existing raw recording and M4/M5/M7 results. It writes to
`data/processed/measurement01/visual_diagnostics/` by default:

- `overlays_overview.png`: all three image pairs, with original overlaps,
  M4 image-match rectification and M7 pose-based rectification in separate columns.
- `pair_000_001_raw.png`, `pair_000_001_m4_matches.png`,
  `pair_000_001_m7_poses.png`, and equivalents for pairs 1–2 and 0–2:
  individual full-resolution working-image overlays without guide lines.
- `phone_estimated_motion.png` and `.svg`: 3D path with camera axes, XY/XZ
  projections with viewing arrows, XYZ coordinates over capture time, and
  rotation angle relative to the first photo. The gray dashed path is the
  M5 initial chain; the solid path is the M7 bundle-adjusted estimate.
- `diagnostics.json`: source hashes, display rotation, homographies, camera
  coordinates/orientations, elapsed times and inherited scale/quality status.

To zoom/pan the plots interactively on a desktop with a Matplotlib GUI backend:

```bash
.venv/bin/python tools/validation/visualize_capture.py --show
```

The default display rotates 90° clockwise for this recording's EXIF orientation.
All geometry is computed first in the original stored-orientation working
pixels. `--stored-orientation` disables only this final display rotation.
`--session`, `--matches`, `--scene`, `--poses` and `--output` can override paths;
the inputs must describe the same three-photo recording and intrinsic matrices.
Output destinations under the raw or source artifact directories are rejected.

## Reading the overlays

The first image in each pair is **red**, the second **blue**. Original colors
are converted to grayscale before coloring, so object colors do not confuse
the comparison. Coincident equal intensities appear **magenta**. Magenta alone
does not prove a correct correspondence.

Original overlaps expose the overall apparent motion. A single whole-image
alignment cannot register a general 3D scene at different depths. Rectification
preserves depth disparity: separation **along** the stated scanline direction
can be normal; compare the **same physical feature across** scanlines to inspect
geometric inconsistency. Occlusion and exposure differences can also change
the appearance. Green guides occur only in the overview, not the saved overlays.

M4 rectification is estimated from image matches independently of the M7 poses.
M7 rectification uses only the saved K/R/C values, without fitting new
correspondences. A common viewport adjustment keeps both warped images visible
and preserves epipolar alignment. Rays near/behind the virtual image horizon
and extreme 2% image-support tails do not set the viewport; some border content
may be clipped. Black regions represent absent warped image coverage.

## Geometry and verification

Code comments retain lecture 002 slide/page references, in one-based PPTX order:
**pp. 81–83** for `M=K[R|-RC]`, world/camera transforms and relative pose;
**pp. 70–76, 133–134** for pixel rays and a virtual camera;
**pp. 138–140** for rectification and epipolar scanlines; **pp. 36–39** for
rotation. OpenCV rectification, viewport sampling, color channels and the
trace-to-rotation-angle diagnostic are implementation choices/extensions.

Synthetic checks cover both horizontal and vertical stereo with rotated,
translated cameras, verifying exact common scanlines after rectification.
Additional checks verify red/blue channel identity, equal-intensity magenta,
invalid image inputs, and rejection of coincident camera centers. The real
recording's generated plots were visually inspected, and source hashes are
checked unchanged after rendering. Image data and plots remain ignored locally.

The motion connects three photo poses at their capture times. It is not a
reconstructed continuous IMU trajectory. Positions use **arbitrary reconstruction
units**, and physical accuracy remains unverified. These diagnostics do not
change the failed M9 accuracy gate or apply a metric scale.

## Retry ruler geometry and acceleration scale

```bash
.venv/bin/python tools/validation/retry_ruler.py
```

This reads the pairwise matching audit, rebuilds poses using the strongest pair
and PnP overlap, refines the scene, and repeats the unchanged held-out ruler check.
It separately tries acceleration-derived scale with gravity and timing sensitivity
tests. Outputs are in `data/processed/measurement01/ruler_retry/`:
`scene.json`, `ruler_retry.json`, `ruler_comparison.png`,
`acceleration_diagnostics.png` and the intermediate pairwise/scene/IMU artifacts.
Existing inputs stay unchanged. A completed experiment does not imply accepted
metric scale; inspect the report's quality flags.

The ruler-reference geometry now passes. Independent acceleration scale remains
unreliable and is not applied to the exported scene. See
[retry results](../../docs/experiments/ruler_pairwise_acceleration_retry.md) and
[inertial equations](../../docs/math/inertial_ruler_retry.md).

The separate ruler gate can still be reproduced with
`tools/validation/measurement01_ruler.py`; see the
[M9 validation record](../../docs/experiments/m9_ruler_validation.md).
