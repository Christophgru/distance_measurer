# Python prototype

Requires Python 3.12 or newer. From the repository root:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
cd python
python -m pip install -e '.[dev]'
python -c "import distance_measuring"
pytest
cd ..
ruff check .
ruff format --check .
```

The base install (`python -m pip install -e .`) includes the initial scientific
dependencies and pytest. The `dev` extra also installs Ruff. Ruff configuration
is at the repository root so the same rules apply to package code, scripts, and
supporting tools.

The package uses a `src/` layout. Subpackages separate dataset loading, camera
models, computer vision, optimization, IMU processing, scene data, and viewing.
The dataset package loads and validates captures; later milestones add the
reconstruction modules. Tests are organized into unit, integration, and synthetic
geometry suites.

## One-command tracked reconstruction

From the repository root, run the P2–P6 prototype with its consolidated P8 report:

```bash
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 python python/scripts/reconstruct_incremental.py \
    data/raw/2026-10-27_115100_304_3ca6165e \
    --image-indices 0 1 3 4 5 --initialize-motion
```

Results go to `data/processed/<session>/prototype_incremental_p8/`; `--output`
chooses a fresh destination. Read `REPORT.md`, `reconstruction.json` and
`scene.json`. Exit 0 requires every selected camera; exit 1 retains geometric
failure/partial results; exit 2 indicates an input or processing error. Omit the
subset option to assess the full capture. The sparse-still SIFT initializer is
explicitly enabled above; pure LK remains the default. See the
[command contract](../docs/architecture/incremental_command.md) and
[measured comparison](../docs/experiments/p8_incremental.md).

## Inspect a captured session

With the environment active, run from the repository root:

```bash
python python/scripts/inspect_session.py data/raw/measurement01
python python/scripts/inspect_session.py data/raw/measurement01 \
    --plot data/processed/measurement01/acceleration.png
```

Use `--json` for machine-readable output. Exit status is 0 for valid sessions
(warnings are allowed), 1 for consistency errors, and 2 for malformed files or
command options. Sensor coverage requires samples on both sides of each image;
`--max-sensor-gap-s` changes the default 0.25-second bracketing interval limit.
Validation fully decodes every referenced image and checks its stored dimensions
before EXIF rotation. It accepts any number of images and keeps the folder name
independent of the recorded session ID.

Inspection also prints each image's geometry settings and warns when they change.
JSON includes `capture_geometry` with observed lens, focal-length, focus and crop
stability; older captures report unknown where metadata was not recorded. See
[capture stability](../docs/architecture/capture_stability.md) for fields and controls.

```python
from distance_measuring.dataset import load_session, validate_session

session = load_session("data/raw/measurement01")  # validates by default
print(session.metadata.session_id, session.duration_s)
print(len(session.images), len(session.sensor_streams["accelerometer"]))
report = validate_session(session)
for warning in report.warnings:
    print(warning.code, warning.message)
```

See [dataset_inspection.md](../docs/architecture/dataset_inspection.md) for error
handling, schema details, and interpretation of diagnostics.

P9.1 also supports optional timestamped preview-frame sidecars through the same
session loader and inspector. Preview frames remain separate from still images;
inspection reports saved-frame cadence, explicit drops/resets, capture decisions
and unavailable clocks/intrinsics. See the
[tracking recording contract](../docs/architecture/tracking_capture_format.md).
Android export and automatic still capture are implemented in P9.2 (app 0.5.0).
Reconstruction from the hybrid stream is the subsequent P9.3 task; P8 continues
to reconstruct the still-image selection. The inspector also reports aggregated
missed/skipped photo ticks from P9.2.

## Camera model and calibration

```bash
python tools/calibration/synthetic_demo.py
python python/scripts/inspect_camera.py data/raw/measurement01
```

Run those commands from the repository root with the environment active. The
camera package models internal K, world-to-camera extrinsics, homogeneous points,
the 3×4 projection matrix M, and radial/tangential distortion. It implements the
lecture's known-3D eigenvector/RQ calibration plus multi-view checkerboard
calibration. Image/point undistortion preserves the stored-pixel coordinate frame.

[Calibration instructions](../tools/calibration/README.md) explain target capture,
YAML export, calibration profile matching, and explicitly approximate metadata or
heuristic fallbacks. [Camera model](../docs/math/camera_model.md) and
[calibration math](../docs/math/camera_calibration.md) map equations and module
names to the local lecture without distributing lecture material.

## Feature matching and rectification

For spatially distributed Shi–Tomasi points, pyramidal LK with forward/backward
checks, persistent IDs and selective replenishment, see
[P2 temporal tracking](../docs/architecture/temporal_tracking.md). The command
exports per-observation history, pair timing and debug images. Its optional
bounded SIFT initializer supports the large gaps in the still-image baseline;
ordinary frame tracking uses pure LK by default.

[P3 gyro/parallax analysis](../docs/architecture/gyro_parallax.md) reads those
saved tracks, reports gyro windows and relative camera-ray rotations, predicts
rotation-only pixels and ranks tracks using age, response, FB error, residual
parallax and coverage. Its output includes explicit timing/mounting assumptions;
pixel residuals are not metric translation or validated pose accuracy.

[P4 tracked bootstrap](../docs/architecture/tracked_bootstrap.md) selects the
earliest viable P3 pair, reuses calibrated essential/pose/triangulation helpers,
and saves two cameras plus quality-filtered landmarks in the existing scene
schema. Run `python python/scripts/bootstrap_tracks.py P3_DIRECTORY --output NEW_DIRECTORY`.
Its diagnostics explain rejected pairs, thresholds, inlier counts and timing;
the scene retains arbitrary scale and input quality limitations.

[P5 incremental registration](../docs/architecture/tracked_incremental.md) extends
the bootstrap using shared PnP, bounded local patches and epipolar descriptor
corridors. `python python/scripts/extend_tracked_scene.py SESSION P2_DIRECTORY P4_DIRECTORY --output NEW_DIRECTORY`
saves projection/corridor overlays, correspondence costs, rejected-frame reasons
and a compatible scene with per-landmark quality. Insufficient support leaves
later cameras unregistered and returns exit status 1 with a retained scene.

Add `--refine` for [P6 local refinement](../docs/architecture/tracked_refinement.md):
the existing SciPy BA solver refines a small window with the original gauge,
then retries guided correspondence for at most two rounds. Landmark confidence
filters PnP/BA support, and reports separate BA/matching costs and excluded points.
With refinement enabled, exit 0 also covers accepted BA without a new camera;
the report distinguishes refinement from registration success.

[P9.3 hybrid replay](../docs/architecture/hybrid_replay.md) uses the same
`reconstruct_incremental.py SESSION` command for captures with saved tracking
frames. It carries preview track identities between stills, verifies actual
JPEG pixels against both exposure-bracketing previews, and runs the existing
P3–P6 geometry. `--tracking-source stills` explicitly retains direct JPEG
tracking. Reports distinguish complete, partial and failed geometry and include
capture cadence, gaps, resets, cross-stream support, runtime and quality.

[P7 keyframe scoring](../docs/architecture/preview_tracking.md) labels saved P3
frames with the same track/coverage/parallax policy used by the Android prototype:
`python python/scripts/score_keyframes.py P3_DIRECTORY --output NEW_DIRECTORY`.
Candidate labels retain input-quality warnings and still require geometric verification.

For a repeatable three-image run through matching, poses, triangulation and BA,
see the [P0.2 prototype baseline](../docs/experiments/prototype_baseline.md).
The [implementation reuse map](../docs/architecture/prototype_reuse_map.md)
lists existing helpers and the remaining P1–P6 adaptations.

```bash
python python/scripts/match_session.py data/raw/measurement01 \
    --output data/processed/measurement01/m4
```

This selects images 0, 1, 2, extracts SIFT features, applies the Lowe ratio and
mutual nearest-neighbor filters, verifies F with RANSAC, and saves rectified
diagnostics for pairs 0–1, 1–2, 0–2. Green and red rows distinguish accepted and
rejected matches. JSON and NPZ exports retain calibration sources, coordinate
frames, feature IDs, complete matches and masks. Raw data stays untouched.

See [command/output details](../docs/architecture/feature_matching.md) and
[math with lecture pages](../docs/math/feature_matching.md).

## Relative camera poses

```bash
python python/scripts/estimate_poses.py data/processed/measurement01/m4 \
    --output data/processed/measurement01/m5
```

M5 reuses M4's undistorted coordinates and K, estimates E, chooses the valid R/t
through explicit SVD and cheirality, and chains adjacent poses. JSON/NPZ exports
retain mask alignment and failures. A camera-frame figure labels the unresolved
relative baseline scales; loop disagreement is reported as a quality warning.

See [pose usage](../docs/architecture/relative_pose.md) and
[equations with lecture pages](../docs/math/relative_pose.md).

## Sparse tracks and scene

```bash
python python/scripts/reconstruct_scene.py \
    data/processed/measurement01/m4 data/processed/measurement01/m5 \
    --output data/processed/measurement01/m6
```

M6 merges pose-inlier correspondences without conflicting image features, then
triangulates all views of each track. It rejects poor depth, reprojection and
intersection geometry. JSON retains cameras, points, observations, rejected
tracks, metadata and M5's scale/quality warnings. Load with
`distance_measuring.scene.load_scene`.

See [usage/schema](../docs/architecture/sparse_reconstruction.md) and
[math with lecture pages](../docs/math/sparse_reconstruction.md).

## Our streaming-pose direction (P10)

First prototype offline reference-scaled measurements with reviewable endpoints,
fixed-scale repeatability and localization sensitivity. The new
`python/scripts/measure_scene.py` command can also export a standalone photo
annotation page. See [measurement workflow](../docs/architecture/measurement_prototype.md).

The ARCore evaluation is withdrawn. P10 will reuse our P2–P6 geometry and the
existing P9 previews in a bounded streaming estimator, then move the supported
pose loop into the Android analyzer. The current offline command and P9 readers
remain the working reference. No new SDK or recording format is required to
begin that work on the October 28 capture. Current visual geometry has arbitrary
scale; metric measurement requires an explicit reference or further sensor
fusion. See [scope and acceptance](../docs/architecture/live_pose.md).

The old ARCore log inspector, its tests and Android experiment were archived
under `data/processed/p10_own_tracking/withdrawn_arcore/source/`. They are outside
the active application and Python package.

## Bundle adjustment

```bash
python python/scripts/refine_scene.py data/processed/measurement01/m6/scene.json \
    --output data/processed/measurement01/m7
```

M7 uses sparse nonlinear least squares with configurable robust loss, keeping
the first camera and first baseline length fixed. The output preserves IDs and
observations, updates reprojection errors, and records solver/quality diagnostics.
See [usage](../docs/architecture/bundle_adjustment.md) and
[equations with lecture pages](../docs/math/bundle_adjustment.md).

## Desktop viewer and stable point selection

```bash
python python/scripts/view_scene.py data/processed/measurement01/m7/scene.json
python python/scripts/view_scene.py data/processed/measurement01/m7/scene.json \
    --no-window --list-points
```

The Open3D viewer draws all sparse points and colored camera frustums/centers.
`--point-ids A B` selects persistent IDs, with optional `--selection-output PATH`.
For headless use, add `--no-window --preview PATH.png` to export the labeled scene
and camera-detail panels. See [usage](../docs/architecture/scene_viewer.md) and
[geometry with lecture pages](../docs/math/scene_viewer.md).
