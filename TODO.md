# TODO

This TODO is intentionally written so individual tasks can be handed to a coding agent.

Legend:

- `[ ]` not started
- `[~]` in progress
- `[x]` complete
- **Acceptance** = objective completion criteria

---

# M0 — Repository bootstrap

## T0.1 Create repository skeleton

[x] Create the folder structure described in `README.md`.

**Acceptance**

- `android/`, `python/`, `data/`, `docs/`, `tools/`, and `local/` exist.
- `local/lecture/` is ignored by Git.
- Empty tracked data folders contain `.gitkeep` where necessary.

## T0.2 Add Python project metadata

[x] Add `python/pyproject.toml`.

Use Python 3.12+ and initial dependencies:

- numpy
- scipy
- opencv-python
- pandas
- pyyaml
- open3d
- matplotlib
- pytest

Add a package named `distance_measuring`.

**Acceptance**

```bash
cd python
python -m pip install -e .
python -c "import distance_measuring"
pytest
```

all succeed.

## T0.3 Add formatting/linting

[x] Configure Ruff.

**Acceptance**

```bash
ruff check .
```

passes on a clean checkout.

---

# M1 — Minimal Android capture app

Goal: create trustworthy raw datasets, not a polished UI.

Implementation lives in `android/`; build/export instructions and the physical-device
acceptance checklist are in `android/README.md`. Tasks marked `[~]` have an
implementation but still require validation on a physical phone.

Automated verification: `:app:assembleDebug`, `:app:testDebugUnitTest` (15 tests),
and `:app:lintDebug` pass through the Gradle wrapper on JDK 17 / Android SDK 35.

## T1.1 Create Android project

[~] Create a minimal Kotlin Android application in `android/`.

Preferred stack:

- Kotlin
- one Activity
- CameraX unless a Camera2 requirement becomes necessary
- no Compose dependency unless it genuinely keeps the app simpler

**Acceptance**

- App builds from command line.
- App launches on a physical Android phone.
- One screen is shown.

## T1.2 Implement minimal UI

[x] Add:

- `Start timeline`
- `Take picture`
- `End timeline`
- recording state text
- image counter

Behavior:

- `Take picture` disabled outside an active timeline.
- `Start timeline` disabled while recording.
- `End timeline` disabled while idle.

**Acceptance**

State transitions cannot accidentally create overlapping sessions.

## T1.3 Create session directory

[x] On `Start timeline`, create a unique directory containing:

```text
session.json
sensors.csv
events.jsonl
images/
```

Use a readable session ID based on UTC or local date/time plus a collision-safe suffix if needed.

**Acceptance**

Starting and ending two sessions creates two independent directories.

## T1.4 Record accelerometer

[~] Register the Android accelerometer sensor for the complete active timeline.

Write:

```text
timestamp_ns,sensor_type,x,y,z,w,accuracy
```

Use the sensor event monotonic timestamp.

**Acceptance**

- Samples exist continuously between start and end.
- Timestamps are increasing.
- No sample is written after timeline end.

## T1.5 Record gyroscope

[~] Add gyroscope samples to the same timeline.

**Acceptance**

The CSV contains distinguishable `gyroscope` samples with valid monotonic timestamps.

## T1.6 Record rotation vector

[x] Record Android rotation-vector sensor when available.

Fallback:

- mark it unavailable in `session.json`,
- continue recording accelerometer and gyroscope.

**Acceptance**

The app does not fail on a device without rotation-vector support.

## T1.7 Capture JPEG image

[~] Implement `Take picture`.

Requirements:

- save full-resolution/high-quality JPEG,
- stable increasing filename,
- do not interrupt sensor capture,
- append an image event to `events.jsonl`.

**Acceptance**

Three button presses create exactly three readable JPEG files and three corresponding events.

## T1.8 Timestamp image capture

[x] Establish the best available timestamp for each image.

First implementation may store:

- button-request timestamp,
- callback timestamp.

Prefer additionally storing the actual camera sensor/frame timestamp if CameraX/Camera2 exposes it reliably.

Document the clock domain of every timestamp.

**Acceptance**

- Every image event contains at least one monotonic timestamp.
- The implementation contains a short comment/doc explaining whether the timestamp is request time, callback time, or sensor exposure time.

## T1.9 Persist camera metadata

[x] Store per-image metadata needed by reconstruction where available:

- width
- height
- focal length
- orientation
- camera/lens ID
- exposure time
- ISO
- timestamp

**Acceptance**

A Python script can read the metadata without Android-specific tooling.

## T1.10 Finalize session safely

[~] On `End timeline`:

- stop listeners,
- flush/close files,
- write final session metadata,
- record `timeline_end`.

Handle app pause/close gracefully where possible.

**Acceptance**

A normally ended session is internally consistent and readable after reconnecting the phone.

## T1.11 Manual export workflow

[x] Document the phone path from which a session should be copied into:

```text
data/raw/<session_id>/
```

Do not add network/cloud export yet.

**Acceptance**

A new developer can capture a session and manually copy it into the repository using only the instructions.

---

# M2 — Dataset contract and inspection

Verified against the local `data/raw/measurement01` capture: five decodable images,
all three sensor streams, and no validation errors. The Python suite has 121 passing
tests. See `docs/experiments/measurement01_inspection.md` for aggregate results.

## T2.1 Define Python schema models

[x] Implement schema/data classes for:

- session
- sensor sample
- image event
- image metadata

**Acceptance**

Malformed required fields produce clear errors.

## T2.2 Implement session loader

[x] Implement:

```python
load_session(path)
```

Return one coherent in-memory session object.

**Acceptance**

A valid captured Android session loads with one call.

## T2.3 Validate timelines

[x] Validate:

- timeline start < image timestamps < timeline end,
- monotonically increasing sensor timestamps,
- sensor coverage around all image timestamps,
- filenames exist,
- images decode successfully.

**Acceptance**

Each invalid condition has a regression test.

## T2.4 Session inspection script

[x] Add:

```bash
python python/scripts/inspect_session.py data/raw/<session>
```

Print:

- duration
- sensor counts
- sensor rates
- image timestamps
- time differences between images
- missing metadata warnings

Optionally plot acceleration magnitude and image timestamps.

**Acceptance**

The script provides enough information to detect an obviously bad recording.

---

# M3 — Camera model and calibration

For this milestone use the variable names and comment references of the Matrices as given in local/lecture/002 - Image Formation and Acquisition.pptx

Implemented with lecture-slide references and original notes under `docs/math/`:
homogeneous coordinates, K, external T_scene, 3×4 M, normalized 3D DLT using the
minimum eigenvector, and RQ decomposition. The checkerboard tool uses the related
multi-view planar method and nonlinear lens-distortion refinement. The Python
suite passes 189 tests; see `docs/experiments/m3_calibration_validation.md`.
`measurement01` currently uses approximate metadata-derived K until a matching
physical checkerboard calibration is captured.

## T3.1 Implement camera intrinsic model

[x] Add a camera-intrinsics class containing:

- `fx`
- `fy`
- `cx`
- `cy`
- distortion coefficients
- image size

**Acceptance**

Projection/unit tests cover known points.

## T3.2 Calibration utility

[x] Add a checkerboard or Charuco calibration tool under:

```text
tools/calibration/
```

**Acceptance**

It can create a YAML calibration file from a folder of calibration images.

## T3.3 Undistortion

[x] Implement image/point undistortion.

**Acceptance**

Synthetic distortion/undistortion test stays below defined numerical error.

## T3.4 Metadata fallback intrinsics

[x] Estimate intrinsics from phone metadata if no calibration file exists.

Mark them as approximate.

**Acceptance**

The pipeline clearly prints which intrinsic source was used:

- calibrated
- metadata-derived
- heuristic

---

# M4 — Feature extraction and matching

Implemented with lecture page references in code and original notes under
`docs/math/feature_matching.md`: sparse correspondence, epipolar verification,
and projective rectification. SIFT/ratio/RANSAC are identified as extensions to
the deck's block-matching examples. The full Python suite passes 237 tests.
All three requested pairs in `measurement01` produce rectified accepted/rejected
match previews; see `docs/experiments/m4_matching_validation.md` and
`docs/architecture/feature_matching.md`. Intrinsics remain explicitly approximate
until a matching physical camera calibration is supplied.

## T4.1 Feature extraction interface

[x] Implement a backend interface.

First backend: SIFT.

Return:

- keypoint pixel coordinates,
- descriptor matrix,
- response/scale metadata.

**Acceptance**

Running twice on the same image is deterministic enough for regression testing.

## T4.2 Pairwise matcher

[x] Match descriptors for image pairs.

Initial filters:

- nearest-neighbor descriptor match,
- Lowe ratio test,
- optional mutual consistency.

**Acceptance**

Debug output reports raw and retained match counts.

## T4.3 Geometric verification

[x] Use RANSAC with fundamental or essential matrix estimation.

**Acceptance**

Outlier matches are marked separately in a debug visualization.

## T4.4 Match visualization

[x] Write rectified debug images for:

- image 0 ↔ 1
- image 1 ↔ 2
- image 0 ↔ 2

**Acceptance**

Each output distinguishes geometrically accepted matches.

---

# M5 — Relative camera pose

## T5.1 Essential matrix estimation

[ ] Given calibrated points, estimate `E`.

**Acceptance**

Synthetic test with known camera motion recovers a valid essential matrix up to scale/sign.

## T5.2 Relative pose recovery

[ ] Recover:

- relative rotation,
- translation direction.

Use cheirality to choose the valid decomposition.

**Acceptance**

Synthetic camera rotation is recovered within a defined angular tolerance.

## T5.3 Three-camera pose chaining

[ ] Establish a first world frame:

```text
camera 0:
    position = [0, 0, 0]
    rotation = identity
```

Add camera 1 and camera 2.

Do not pretend chained translation magnitudes are metrically correct.

**Acceptance**

All three camera frames can be rendered in one coordinate system.

---

# M6 — Feature tracks and sparse reconstruction

## T6.1 Track builder

[ ] Merge pairwise matches into multi-image feature tracks.

Prevent invalid tracks containing:

- two different features from the same image,
- contradictory unions.

**Acceptance**

Unit tests cover track merging and conflict rejection.

## T6.2 Triangulation

[ ] Implement linear triangulation.

Reject points:

- behind cameras,
- with high reprojection error,
- with poor triangulation angle.

**Acceptance**

Synthetic 3D points reconstruct up to expected numerical tolerance.

## T6.3 Scene model

[ ] Implement persistent scene objects for:

- cameras,
- 3D points,
- 2D observations,
- track IDs,
- reconstruction metadata.

**Acceptance**

Scene can be serialized to JSON and loaded back without meaningful loss.

---

# M7 — Bundle adjustment

## T7.1 Reprojection residuals

[ ] Implement reprojection residual vector.

**Acceptance**

Residual is approximately zero on perfect synthetic observations.

## T7.2 Optimize cameras and points

[ ] Use `scipy.optimize.least_squares`.

Fix gauge freedoms:

- camera 0 pose fixed,
- one scale convention fixed before metric calibration.

**Acceptance**

On noisy synthetic data, optimization lowers total reprojection RMSE.

## T7.3 Robust loss

[ ] Add configurable robust loss.

**Acceptance**

A small set of outliers does not catastrophically move the whole reconstruction.

## T7.4 Bundle-adjusted scene export

[ ] Save:

- optimized cameras
- optimized points
- observation reprojection errors
- summary metrics

**Acceptance**

Before/after reprojection error is printed.

---

# M8 — Desktop 3D visualization

## T8.1 Open3D viewer

[ ] Show:

- sparse point cloud,
- camera centers,
- camera view directions/frustums.

**Acceptance**

A user can visually distinguish all three cameras.

## T8.2 Point IDs

[ ] Preserve stable point IDs through scene serialization and visualization.

**Acceptance**

A selected/logged point can be referenced in later measurement commands.

## T8.3 Point selection

[ ] Allow selecting one or two reconstructed points.

If direct interactive selection is awkward in the first version, a CLI fallback using point IDs is acceptable.

**Acceptance**

The user can unambiguously specify point A and point B.

---

# M9 — Measurement engine

## T9.1 Point-to-point distance

[ ] Implement:

```python
distance_points(point_a, point_b)
```

**Acceptance**

Unit tests cover known 3D points.

## T9.2 Camera-to-point distance

[ ] Implement:

```python
distance_camera_point(camera, point)
```

Use the camera center in world coordinates.

**Acceptance**

Unit test uses analytically known distance.

## T9.3 Coordinate-frame conversion

[ ] Support reporting point coordinates in:

- world frame,
- selected camera frame.

**Acceptance**

Round-trip world → camera → world stays within numerical tolerance.

## T9.4 Measurement report

[ ] Add CLI output including:

- measurement type,
- involved IDs,
- raw reconstruction units,
- scale source,
- metric result when scale is available.

**Acceptance**

The program never labels an arbitrary-scale value as meters.

---

# M10 — Manual scale calibration baseline

## T10.1 Set scale from known point distance

[ ] Add:

```bash
python python/scripts/estimate_scale.py \
    <scene> \
    --point-a 123 \
    --point-b 456 \
    --distance-m 0.500
```

**Acceptance**

All camera translations and 3D points are scaled consistently.

## T10.2 Scale provenance

[ ] Store scale metadata in `scene.json`.

Example:

```json
{
  "scale": {
    "value": 0.042,
    "units_per_reconstruction_unit": "m",
    "source": "known_distance",
    "reference": {
      "point_a": 123,
      "point_b": 456,
      "distance_m": 0.5
    }
  }
}
```

**Acceptance**

Metric measurements can state exactly how the scale was obtained.

## T10.3 Ground-truth experiment

[ ] Record a simple scene containing several physically measured distances.

Compare reconstructed measurements against ground truth.

**Acceptance**

Create an experiment note containing absolute and relative measurement errors.

---

# M11 — IMU preprocessing

## T11.1 Load IMU streams

[ ] Split `sensors.csv` into typed streams.

**Acceptance**

Accelerometer, gyro, and rotation-vector samples are individually accessible.

## T11.2 Interpolate orientation at image timestamps

[ ] Convert the recorded orientation representation into rotations.

Use quaternion interpolation where appropriate.

**Acceptance**

Each image timestamp has an estimated device orientation.

## T11.3 Coordinate convention document

[ ] Document:

- Android sensor axes,
- camera axes,
- image axes,
- world axes,
- handedness,
- quaternion ordering.

**Acceptance**

A synthetic transform test verifies the documented convention.

## T11.4 Gravity handling

[ ] Rotate acceleration into a consistent frame and estimate/remove gravity.

Do not assume Android axes equal camera axes.

**Acceptance**

During a stationary interval, gravity-removed acceleration is close to zero relative to raw gravity magnitude.

## T11.5 Bias estimation

[ ] Estimate accelerometer bias from stationary or optimization constraints.

**Acceptance**

Bias is exposed in debug output and stored with an IMU solution.

---

# M12 — IMU scale estimation research

This milestone is experimental. Keep it separate from the reliable visual baseline.

## T12.1 Naive double-integration experiment

[ ] Integrate acceleration between image timestamps.

Purpose: establish how bad uncorrected drift is.

**Acceptance**

An experiment report compares predicted displacement against one physically measured motion.

## T12.2 Visual/IMU displacement alignment

[ ] Given visual camera positions up to scale and IMU-derived displacement constraints, estimate one global scale factor.

Start with the simplest least-squares formulation.

**Acceptance**

On a controlled dataset, estimated scale is finite and reproducible.

## T12.3 Joint scale/bias optimization

[ ] Optimize at least:

- global visual scale,
- accelerometer bias,
- optionally initial velocity.

**Acceptance**

Compare result against manual known-distance scale.

## T12.4 Rotation-vector vs gyro-integrated orientation

[ ] Compare both orientation sources.

**Acceptance**

Experiment report states which gives lower scale error on recorded test sessions.

## T12.5 Uncertainty / quality gate

[ ] Produce a quality score using indicators such as:

- reprojection RMSE,
- match count,
- triangulation angle,
- IMU residual,
- estimated scale consistency.

**Acceptance**

The tool can refuse to label a reconstruction metric when evidence is too weak.

---

# M13 — End-to-end three-image command

## T13.1 Implement one-command pipeline

[ ] Make this work:

```bash
python python/scripts/reconstruct_three_images.py \
    data/raw/<session>
```

Pipeline:

1. load session,
2. validate,
3. load/calibrate camera,
4. extract features,
5. match,
6. estimate poses,
7. build tracks,
8. triangulate,
9. bundle adjust,
10. export scene,
11. optionally estimate scale,
12. write diagnostics.

**Acceptance**

One real three-image session finishes without manual intermediate file editing.

## T13.2 Deterministic output layout

[ ] Store outputs below:

```text
data/processed/<session_id>/
```

**Acceptance**

Re-running does not scatter temporary files around the repository.

## T13.3 Summary report

[ ] Print and save:

- number of features per image,
- verified matches,
- track count,
- triangulated point count,
- reprojection RMSE before/after BA,
- scale source,
- measurement readiness.

**Acceptance**

A failed reconstruction explains the stage and likely reason.

---

# M14 — Robustness improvements

Do these only after the classical three-image path works.

## T14.1 More than three images

[ ] Generalize the pipeline to `N >= 2` images.

## T14.2 Keyframe selection

[ ] Reject frames with too little baseline or excessive blur.

## T14.3 Better feature matching

[ ] Evaluate SuperPoint + LightGlue against SIFT.

Keep SIFT as baseline.

## T14.4 Incremental SfM

[ ] Add cameras progressively instead of relying on one fixed three-view chain.

## T14.5 Loop/track consistency

[ ] Improve global feature-track consistency.

## T14.6 Better bundle adjustment backend

[ ] Evaluate Ceres, GTSAM, or another sparse optimizer only if SciPy becomes a bottleneck.

---

# M15 — Android productization

Do not start until Python reconstruction is objectively useful.

## T15.1 Port or package reconstruction

[ ] Decide between:

- native/on-device implementation,
- embedded Python,
- local service,
- server backend.

Document the decision and tradeoffs.

## T15.2 Capture guidance

[ ] Give live user guidance for:

- enough translation/parallax,
- excessive blur,
- too-fast motion,
- poor lighting,
- insufficient texture.

## T15.3 On-device reconstruction status

[ ] Show stages without exposing unnecessary algorithm details.

## T15.4 3D viewer

[ ] Add an Android 3D scene view.

## T15.5 Touch point selection

[ ] Select 3D points from the reconstructed scene.

## T15.6 Measurement UI

[ ] Support:

- camera ↔ point,
- point ↔ point,
- coordinates in chosen camera frame.

## T15.7 Accuracy display

[ ] Display estimated uncertainty / confidence.

Never present false precision.

---

# M16 — Documentation tied to the lecture

## T16.1 Personal lecture index

[ ] Create a local-only index under:

```text
local/lecture/
```

This may reference local lecture files but is never committed.

## T16.2 Project-owned concept notes

[ ] Write original notes under:

```text
docs/math/
```

Suggested topics:

- pinhole camera model
- homogeneous coordinates
- feature descriptors
- epipolar geometry
- fundamental matrix
- essential matrix
- triangulation
- PnP
- bundle adjustment
- IMU coordinate transforms
- visual-inertial scale

Do not copy lecture material verbatim.

## T16.3 Trace implementation to concepts

[ ] Each major module should link to the relevant project-owned math note.

Example:

```text
vision/epipolar.py
    -> docs/math/epipolar_geometry.md
```

---

# Suggested coding-agent order

Give the coding agent one milestone at a time.

Recommended order:

```text
M0
M1
M2
M3
M4
M5
M6
M7
M8
M9
M10
M13
M11
M12
M14
M15
M16
```

The reason `M10` comes before IMU scale work is that a known-distance baseline gives us a trustworthy way to evaluate whether the IMU-derived scale is actually improving the result.

---

# Definition of first useful prototype

The project is already useful before the final Android app exists.

The first useful prototype is reached when a user can:

1. record a three-image session on Android,
2. copy the session into `data/raw/`,
3. run one Python command,
4. inspect the reconstructed camera positions and sparse point cloud,
5. define scale using one known distance,
6. select reconstructed points,
7. obtain physically meaningful distances,
8. inspect reprojection/quality diagnostics.

IMU-derived metric scale is the next research goal, not a prerequisite for validating the rest of the system.
