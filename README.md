# Distance Measuring

Experimental Android + Python project for reconstructing a small 3D scene from a few phone images and estimating real-world distances.

The long-term goal is an Android app that lets a user:

1. start a sensor timeline,
2. take several pictures while moving the phone,
3. reconstruct the camera poses and sparse 3D scene,
4. select reconstructed points,
5. measure:
   - camera-to-point distance,
   - point-to-point distance,
   - distances in a chosen camera coordinate frame.

The project starts deliberately small:

- a **minimal Android capture app**,
- exactly a few images per session (initially three),
- a timestamped IMU timeline,
- an offline **Python reconstruction pipeline**,
- visualization and measurement on a desktop.

Once the geometry and scale estimation work reliably, the reconstruction code can be moved into or behind a polished Android app.

---

## Why this project exists

The project is intended to grow alongside a university lecture on **Digital Image Processing**.

Lecture material may be kept locally in the repository for reference, but it must **not be committed or uploaded** because the material may be copyrighted. The recommended local folder is:

```text
local/lecture/
```

That path is ignored by Git.

The implementation should favor methods that are understandable from first principles before replacing them with larger black-box frameworks. For example:

- feature detection and descriptors,
- feature matching,
- epipolar geometry,
- essential/fundamental matrices,
- camera calibration,
- triangulation,
- pose estimation,
- bundle adjustment,
- inertial measurements,
- coordinate transformations,
- scale recovery.

---

## Important geometric limitation

Images alone normally recover scene structure and camera motion only **up to an unknown global scale**.

A visual reconstruction can therefore tell us that one segment is, for example, `2.3x` another segment, but not automatically whether it is `2.3 cm`, `2.3 m`, or another scale.

The project will investigate metric scale recovery from the phone IMU.

For the first reliable baseline, the pipeline should support both:

1. **visual reconstruction with arbitrary scale**, and
2. **manual known-distance scale calibration** for validation.

After that works, IMU-based metric scale estimation is added and compared against the known-distance baseline.

---

## Why record more than accelerometer data?

The Android capture app should record at least:

- accelerometer,
- gyroscope,
- preferably Android rotation-vector/orientation data,
- image/event timestamps.

An accelerometer measurement is expressed in the phone's changing device coordinate system and includes gravity. To use acceleration for translation estimation we need to know the phone orientation well enough to rotate measurements into a common frame and remove gravity.

The UI can still remain minimal.

---

# High-level architecture

```text
Android capture app
        |
        |  images + timestamped sensor/session metadata
        v
Raw dataset
        |
        v
Python dataset loader
        |
        +--> camera calibration / intrinsics
        |
        +--> feature detection + matching
        |
        +--> relative camera pose estimation
        |
        +--> triangulation / track building
        |
        +--> bundle adjustment
        |
        +--> sparse 3D scene
        |
        +--> IMU processing / scale estimation
        |
        v
Metric 3D scene
        |
        +--> visualization
        +--> point selection
        +--> distance queries
```

---

# Proposed repository layout

```text
distance-measuring/
├── README.md
├── TODO.md
├── .gitignore
├── LICENSE
│
├── android/
│   ├── app/
│   ├── build.gradle.kts
│   ├── settings.gradle.kts
│   └── README.md
│
├── python/
│   ├── pyproject.toml
│   ├── src/
│   │   └── distance_measuring/
│   │       ├── __init__.py
│   │       ├── dataset/
│   │       │   ├── loader.py
│   │       │   ├── schema.py
│   │       │   └── validation.py
│   │       ├── camera/
│   │       │   ├── calibration.py
│   │       │   ├── intrinsics.py
│   │       │   └── geometry.py
│   │       ├── vision/
│   │       │   ├── features.py
│   │       │   ├── matching.py
│   │       │   ├── epipolar.py
│   │       │   ├── pose.py
│   │       │   ├── tracks.py
│   │       │   └── triangulation.py
│   │       ├── optimization/
│   │       │   └── bundle_adjustment.py
│   │       ├── imu/
│   │       │   ├── preprocessing.py
│   │       │   ├── orientation.py
│   │       │   ├── integration.py
│   │       │   └── scale.py
│   │       ├── scene/
│   │       │   ├── model.py
│   │       │   ├── measurements.py
│   │       │   └── serialization.py
│   │       └── visualization/
│   │           └── viewer.py
│   │
│   ├── scripts/
│   │   ├── inspect_session.py
│   │   ├── reconstruct_three_images.py
│   │   ├── estimate_scale.py
│   │   └── view_scene.py
│   │
│   └── tests/
│       ├── unit/
│       ├── integration/
│       └── synthetic/
│
├── data/
│   ├── raw/
│   │   └── .gitkeep
│   ├── processed/
│   │   └── .gitkeep
│   └── README.md
│
├── docs/
│   ├── architecture/
│   ├── math/
│   ├── experiments/
│   └── decisions/
│
├── tools/
│   ├── calibration/
│   └── dataset/
│
└── local/
    └── lecture/          # ignored by Git
```

### Why this layout?

`android/` is only responsible for capturing trustworthy data.

`python/` contains the actual reconstruction research and is kept independent of Android so algorithms can be tested quickly.

`data/raw/` contains immutable capture sessions. Generated data belongs in `data/processed/`.

`docs/` stores our own notes, derivations, architecture decisions, and experiment results.

`local/lecture/` is intentionally outside the project documentation tree so copyrighted lecture material is less likely to be committed accidentally.

---

# Dataset format

Each recording session should be self-contained:

```text
data/raw/2026-10-21_134500/
├── session.json
├── sensors.csv
├── events.jsonl
└── images/
    ├── image_000.jpg
    ├── image_001.jpg
    └── image_002.jpg
```

## `session.json`

Example:

```json
{
  "schema_version": 1,
  "session_id": "2026-10-21_134500",
  "device": {
    "manufacturer": "Example",
    "model": "ExamplePhone"
  },
  "sensors": {
    "accelerometer": true,
    "gyroscope": true,
    "rotation_vector": true
  },
  "notes": ""
}
```

Later this file should also contain camera metadata such as image dimensions, focal length/intrinsic calibration source, sensor identity, and Android/app version.

## `sensors.csv`

Recommended columns:

```text
timestamp_ns,sensor_type,x,y,z,w,accuracy
```

Examples of `sensor_type`:

```text
accelerometer
gyroscope
rotation_vector
```

`w` can be empty for three-component sensors.

All timestamps should use one documented monotonic clock domain wherever possible.

## `events.jsonl`

Example:

```json
{"type":"timeline_start","timestamp_ns":123000000000}
{"type":"image","index":0,"filename":"images/image_000.jpg","timestamp_ns":124100000000}
{"type":"image","index":1,"filename":"images/image_001.jpg","timestamp_ns":126800000000}
{"type":"image","index":2,"filename":"images/image_002.jpg","timestamp_ns":129400000000}
{"type":"timeline_end","timestamp_ns":130000000000}
```

For the first app version, exact camera exposure timestamps may be approximated if necessary, but obtaining and validating the true camera sensor timestamp is an explicit later task.

---

# Minimal Android app

The first Android app is not intended to look polished.

## UI

One screen:

```text
[ Start timeline ]

Status: idle / recording
Images: 0

[ Take picture ]

[ End timeline ]
```

Optional small text fields may display:

- session ID,
- sensor sample count,
- image count,
- output directory.

## Required behavior

### Start timeline

- Create a new session directory.
- Open `sensors.csv`.
- Start sensor listeners.
- Write a `timeline_start` event.
- Disable another start until the session ends.

### Take picture

- Capture the highest practical full-resolution JPEG.
- Save it using an increasing index.
- Record the image event and timestamp.
- Do not stop sensor collection during image capture.

### End timeline

- Write `timeline_end`.
- Stop sensor listeners.
- Flush and close files.
- Write/finalize `session.json`.
- Make the session easy to copy manually from the phone.

No networking, accounts, cloud sync, database, login, or elaborate navigation is needed in the prototype.

---

# Python prototype

The first target is a command like:

```bash
python python/scripts/reconstruct_three_images.py \
    data/raw/example_session
```

It should produce:

```text
data/processed/example_session/
├── matches/
├── scene.json
├── sparse_points.ply
├── cameras.json
└── debug/
```

---

# Reconstruction pipeline

## Phase 1: load and validate data

Validate that:

- exactly three images exist for the initial prototype,
- timestamps are strictly ordered,
- sensor samples cover the complete interval,
- images can be decoded,
- camera intrinsics are available or explicitly approximated.

## Phase 2: feature detection and matching

Initial implementation:

- OpenCV SIFT if available,
- descriptor matching,
- Lowe ratio test,
- mutual/cross-check where useful,
- RANSAC geometric verification.

Create debug images showing accepted and rejected matches.

Later alternatives can include:

- ORB,
- AKAZE,
- SuperPoint,
- LightGlue.

The classical OpenCV path should remain available as an understandable baseline.

## Phase 3: relative pose

For image pairs with enough verified correspondences:

1. normalize image coordinates using the camera intrinsics,
2. estimate the essential matrix,
3. recover relative rotation and translation direction,
4. reject geometrically inconsistent matches.

Translation from the essential matrix has unknown magnitude.

## Phase 4: tracks across three images

Do not treat every pairwise match as an independent 3D point.

Build feature tracks such as:

```text
image 0 feature 120
    <->
image 1 feature 87
    <->
image 2 feature 151
```

Each valid track represents one candidate world point.

## Phase 5: triangulation

Using camera projection matrices and matched normalized coordinates:

- triangulate 3D points,
- reject points behind cameras,
- reject points with poor triangulation angle,
- reject high reprojection-error points.

## Phase 6: bundle adjustment

Jointly refine:

- camera poses,
- 3D point positions,
- optionally selected camera parameters.

Objective: minimize reprojection error over all visible point observations.

For the first Python version, `scipy.optimize.least_squares` is sufficient.

## Phase 7: scale

Support two scale modes.

### A. Known-distance baseline

The user selects two reconstructed points and supplies their true physical distance.

Then:

```text
scale = true_distance / reconstructed_distance
```

Apply that one global factor to all translations and 3D points.

This is important for validating the complete measurement pipeline.

### B. IMU-derived scale

Research target:

1. estimate phone orientation over time,
2. rotate accelerometer samples to a common/world frame,
3. estimate/remove gravity,
4. estimate accelerometer bias,
5. integrate between image timestamps,
6. compare inertial displacement constraints with visually recovered camera translations,
7. jointly optimize the global visual scale and inertial nuisance parameters.

Pure double integration is expected to drift significantly. The IMU result should therefore be treated as an optimization constraint, not blindly trusted ground truth.

---

# Camera calibration

Reliable metric reconstruction needs reasonable camera intrinsics.

The project should eventually support:

1. a calibration file generated from a checkerboard/Charuco capture,
2. phone-provided focal length metadata as a fallback,
3. explicit approximate intrinsics only for early experiments.

Recommended calibration output:

```yaml
image_width: 4000
image_height: 3000
camera_matrix:
  - [fx, 0, cx]
  - [0, fy, cy]
  - [0, 0, 1]
distortion:
  - k1
  - k2
  - p1
  - p2
  - k3
```

---

# Coordinate systems

The code must name coordinate frames explicitly.

Suggested conventions:

- `C_i`: camera center of image `i` in world coordinates,
- `R_wc`: camera-to-world rotation,
- `R_cw`: world-to-camera rotation,
- `P_w`: reconstructed point in world coordinates.

Never use an unnamed variable `R` when crossing module boundaries.

For any reconstructed point `P_w` and camera center `C_i`:

```text
camera-to-point distance = ||P_w - C_i||
```

For two reconstructed points:

```text
point-to-point distance = ||P1_w - P2_w||
```

The optical camera center is the origin of the corresponding camera coordinate frame. If a future UI uses “image origin” to mean the top-left pixel or principal point, that must be named separately because it is not a 3D world-space origin.

---

# Visualization

First desktop viewer requirements:

- show sparse 3D points,
- show the three camera centers,
- show camera viewing directions/frustums,
- allow selecting one or two points,
- print selected point IDs and coordinates,
- compute distances.

A simple first implementation may use Open3D.

The visualization module must not contain reconstruction logic.

---

# Testing strategy

The project should have three layers of tests.

## Unit tests

Examples:

- homogeneous coordinate conversions,
- projection and back-projection,
- rigid transforms,
- triangulation,
- distance calculations,
- timestamp interpolation.

## Synthetic geometry tests

Generate known:

- camera intrinsics,
- camera poses,
- 3D points.

Project them into artificial images/measurements and verify that reconstruction recovers the expected geometry up to scale.

These tests should be written before relying on real phone data.

## Recorded-session regression tests

Keep at least one small, redistributable test dataset in the repository or provide a script to generate it.

Do not commit large personal photo datasets by default.

---

# Suggested Python dependencies

Initial:

```text
numpy
scipy
opencv-python
pandas
pyyaml
open3d
matplotlib
pytest
```

Possible later additions:

```text
torch
kornia
lightglue
```

Avoid introducing GPU/deep-learning dependencies until the classical baseline works.

---

# Development principles

1. **Preserve raw data.** Never mutate files under `data/raw/`.
2. **Keep timestamps explicit.** Never infer ordering only from filenames.
3. **Keep coordinate systems explicit.**
4. **Make uncertainty visible.** Reprojection error and match quality should be inspectable.
5. **Build a classical baseline first.**
6. **Keep Android capture independent from reconstruction.**
7. **Prefer reproducible command-line scripts before building UI.**
8. **Test geometry with synthetic data before debugging real scenes.**
9. **Do not claim metric accuracy until scale has been validated against known distances.**
10. **Do not commit copyrighted lecture material.**

---

# First milestone

The first meaningful milestone is complete when:

- the Android app records one session containing three images plus IMU data,
- the session can be copied manually into `data/raw/`,
- Python detects and matches visual features,
- the three camera poses are reconstructed up to scale,
- a sparse point cloud is triangulated,
- bundle adjustment improves or preserves reprojection error,
- the scene can be visualized,
- two reconstructed points can be selected,
- a known-distance calibration converts the scene into metric scale,
- measured distances can be compared against physical ground truth.

After this baseline is solid, IMU scale recovery becomes the next major milestone.
