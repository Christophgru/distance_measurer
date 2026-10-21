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
