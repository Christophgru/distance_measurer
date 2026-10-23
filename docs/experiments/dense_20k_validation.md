# 20,000 image-supported inspection points

The browser artifact at
`data/processed/2026-10-23_091847_034_be3e7ada/interactive_scene/index.html`
now contains **20,000 points**: the original **941** sparse points plus
**19,059** stereo samples from photos **2–3**. Cameras **2, 3, 7** remain in the
viewer with exactly their original `K`, `R` and `C`. Every original point,
track, observation and ID is preserved. There is no metric-scale update or
change to the previous ruler accuracy rejection.

The original 941-point HTML remains available as
`interactive_scene/sparse_941.html`. The new self-contained artifact is about
**2.79 MB**. The live local preview serves it at `http://localhost:8765/`;
hard-refresh an existing browser tab to replace the cached older file.

## How additional samples are obtained

The saved cameras rectify overlapping image pairs, using a shared sampling
viewport at up to **1600×1200** pixels. New coordinates are mapped back to the
saved **2400×1800** working image frame before triangulation. No camera pose,
calibration, heading, acceleration, ruler length or scale is fitted here.
The original screen and ruler exclusion masks are retained and eroded around
patch boundaries. Changing screen content and repeated Dell logos therefore
do not enter this densification pass.

Bidirectional semi-global stereo uses 5×5 blocks, a uniqueness ratio of 15,
speckle filtering and a **0.75 px** left/right consistency check. An additional
11×11 normalized-correlation check requires **NCC ≥0.85** and grayscale standard
deviation **≥6** in both patches. Textureless areas cannot supply points.
Triangulation requires positive depth in both cameras, at least **1°** acute
ray angle and at most **3 px** reprojection error in the original working frame.

The final pair 2–3 pass produces:

| Stage | Pixel samples |
| --- | ---: |
| Bidirectional consistency and exclusion masks | 109,660 |
| Normalized correlation and texture checks | 45,147 |
| Triangulation checks | 44,532 |
| Added after image-space deduplication and target selection | **19,059** |

One-working-pixel cells avoid adding multiple observations at the same sampled
location. Candidates are interleaved across image tiles to distribute the
budget, rather than selecting only one small textured region. Neighboring
stereo samples can be correlated; they are not independent calibration evidence.
The requested total is never filled by repeating points or creating artificial
surface positions. Unsupported targets are reported as shortfalls.

Pairs 2–7 and 3–7 require a disparity span beyond this implementation's
512-disparity search bound and are skipped, with reasons in the report. Camera
7 and its original sparse observations are retained. This does not declare
photo 7 unusable for other matching methods.

## Interpretation

These are real image-correspondence-based samples for inspection, not newly
certified metric measurements. Rectified stereo already constrains matches
to the fixed cameras' epipolar geometry. Its small reprojection residual is
therefore a consistency check and **does not independently certify camera
positions, distortion calibration or absolute accuracy**. The densified scene
has quality status `densified_inspection_unverified` and retains arbitrary units.

The browser's **Added stereo samples** toggle hides all 19,059 added points,
returning to the original 941. The point inspector identifies sparse-track vs
stereo-sample provenance. Picking and shader filters use the same visibility
rules for both types.

## Reproduction and verification

The [viewer guide](../architecture/scene_viewer.md) and
[validation tool guide](../../tools/validation/README.md) contain the exact
densification/export commands. Ignored outputs are `dense_20k/scene.json`,
`dense_20k/densification.json` and the regenerated browser HTML.

**29 targeted Python tests pass** across densification, triangulation and
browser export. New synthetic tests recover a known textured plane, compare
batched homogeneous SVD against the existing solver, reject backward/poor-angle
rays, reject masked/textureless inputs without padding and verify preservation
of original geometry, IDs and observations. Additional real-data checks compare
every saved camera parameter and all original tracks/points exactly; sampled
new points agree with the existing single-point triangulator to `1e-8` units.
Input hashes also match the previous IMU-comparison capture hashes.

Offline Chromium tests pass for all 20,000 points, GPU picking, filters, the
sparse/dense toggle, camera controls and mobile layout. Rendering still uses
**9 total draw calls**, including the single point-cloud draw. The temporary
synthetic 100,000-point renderer check also passes. No specific hardware frame
rate is inferred from the headless software renderer. The new screenshot was
visually inspected. The live HTTP export was checked to contain 20,000 points.
Ruff lint/format and `git diff --check` pass.

Lecture 002 comments preserve **pp. 65–76** (homogeneous image coordinates and
`K`), **81–83** (`K[R|-RC]`), **98/101** (homogeneous least squares), **133–136**
(triangulation), **138–140** (rectification) and **141–144** (normalized
correlation and texture limitations). Bidirectional SGBM, sampling and rejection
thresholds are implementation extensions. See the primary
[OpenCV StereoSGBM documentation](https://docs.opencv.org/4.x/d2/d85/classcv_1_1StereoSGBM.html).
