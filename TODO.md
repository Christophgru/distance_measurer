# TODO — Fast camera-pose prototype

This TODO is intentionally scoped to **prototyping fast and robust camera-pose recovery** from a short, ordered phone capture sequence.

The coding agent should execute **one task at a time**, keep changes small, and prefer existing code over new implementations.

## Legend

- `[ ]` not started
- `[~]` in progress
- `[x]` complete
- `[R]` reuse an implementation that already exists in the repository; inspect it before changing or duplicating it
- **Acceptance** = objective completion criteria

---

# Agent rules

These rules apply to every task below.

1. **Inspect before implementing.** Search the repository for an existing implementation, helper, test, CLI, schema, or dependency before writing new code.
2. **Reuse first.** If the required capability already exists, adapt or call it instead of creating a parallel implementation.
3. **Dependency gate.** Before adding any dependency:
   - inspect `python/pyproject.toml`, Android Gradle files, and the current imports/dependency graph;
   - determine whether the task can reasonably be completed with dependencies already present;
   - prefer standard-library, Android/AndroidX, NumPy/SciPy, and OpenCV functionality already available;
   - add a dependency only when the current stack cannot provide the required capability without substantial custom implementation;
   - document the reason in the commit/PR/task notes.
4. **No speculative architecture.** Implement only what is required by the current task and its acceptance criteria.
5. **Preserve compatibility.** Do not rewrite working reconstruction modules merely to make them cleaner.
6. **Tests before expansion.** Add focused unit/regression tests for new math and data transformations.
7. **Measure performance.** New tracking/matching stages must report their runtime and counts so later optimizations are evidence-based.
8. **Keep the prototype debuggable.** Save compact diagnostics and visualizations where they materially help verify geometry.

## Current dependency policy

The Python project already has the core numerical/vision stack needed for this prototype, including NumPy, SciPy and OpenCV. Assume **no additional Python dependency is needed** unless repository inspection proves otherwise.

For Android, inspect the existing CameraX/Camera2 and AndroidX setup first. Do not add an Android computer-vision library until the task cannot reasonably be completed with the current app APIs or by keeping the relevant prototype logic on the Python side.

---

# P0 — Inventory and preserve existing building blocks

## P0.1 Map existing implementations

[x] Inspect the repository and write a short implementation map for the modules needed by this prototype.

Implementation map: [prototype reuse map](docs/architecture/prototype_reuse_map.md).

Mark at least the location and callable interface for:

- `[R]` session loading and timestamp validation
- `[R]` camera intrinsics and undistortion
- `[R]` feature extraction/matching infrastructure
- `[R]` essential/fundamental matrix estimation and RANSAC verification
- `[R]` relative pose recovery
- `[R]` triangulation
- `[R]` scene / track representation
- `[R]` reprojection residuals and bundle adjustment
- `[R]` Android sensor recording and image metadata

Do not change implementations in this task unless something is broken enough to prevent inspection.

**Acceptance**

Create `docs/architecture/prototype_reuse_map.md` containing the existing file/module/function names and any missing capability required by P1–P6.

## P0.2 Establish one prototype dataset and baseline command

[x] Select one existing short capture with at least 3 usable images and gyro data as the baseline dataset.

Baseline: [measurement01 command and results](docs/experiments/prototype_baseline.md).
Images 0/1/2; 31.5 s; 318 verified pair matches; 3 cameras; 206 sparse points;
reprojection RMSE 0.864250 → 0.269012 working pixels after BA. Repeat run reproduced
geometry exactly; calibration/timing/quality limitations are recorded in the notes.

Add or identify one command that runs the current visual reconstruction far enough to produce camera poses and sparse points.

Record:

- image count,
- runtime,
- verified match count,
- reconstructed camera count,
- sparse point count,
- reprojection RMSE if available.

**Acceptance**

The same dataset and command can be used before and after later tasks to compare speed and geometry.

---

# P1 — Make capture geometry predictable

Goal: reduce unnecessary variation before improving matching.

## P1.1 Record the camera settings that affect geometry

[x] Inspect the existing Android capture path and ensure every image records, where available:

Implemented in app 0.3 with exact-timestamp Camera2 result association and typed
Python metadata. The existing session inspector prints per-image settings and
warns on changes; [field definitions and verification](docs/architecture/capture_stability.md).

- `[R]` camera/lens ID
- `[R]` focal length
- `[R]` image dimensions
- `[R]` orientation
- `[R]` exposure time and ISO
- focus distance
- zoom/crop region or equivalent
- AE/AWB/AF mode and lock state
- sensor/frame timestamp when available

Do not duplicate metadata already present.

**Acceptance**

A Python inspection command can print the geometry-relevant settings for every captured image and warn when they change within a session.

## P1.2 Add a prototype capture-stability mode

[x] Add the smallest Android-side control needed to make consecutive captures more consistent.

Implemented as the prototype default: fixed camera/1× zoom, converged focus/crop
hold and supported AWB lock, with automatic fallback. Observed sequence stability
is reported separately from requested controls. Build, 30 Android JVM tests,
170 Python tests and lint pass. Physical capture/lock behavior remains unverified
because no device is attached; [device check](docs/architecture/capture_stability.md#verification-and-remaining-device-check).

Preferred behavior after the camera has converged:

- keep the same physical camera/lens,
- keep zoom/crop constant,
- avoid focus changes between images where the device API allows it reliably,
- optionally lock AWB,
- optionally lock AE only when it does not make the capture unusable.

Do not build a settings UI. A prototype default or one simple toggle is sufficient.

**Acceptance**

A captured sequence reports whether lens, focal length, focus and crop remained stable. Failure to lock an unsupported control must not break capture.

---

# P2 — Cheap, informative feature tracks

Goal: obtain stable 2D tracks without exhaustive descriptor matching.

Implemented offline; [command, interfaces, measured results and limitations](docs/architecture/temporal_tracking.md).
The sparse still-image baseline requires the explicit optional bounded SIFT warp
initializer; regular frame tracking uses pure LK by default. Baseline coverage is
48/48 cells, with 100 persistent tracks including 11 spanning images 0/1/2.

Implement this in Python first using the existing OpenCV dependency. Porting it into the Android preview path is a later task in this TODO only after the algorithm works on recorded images/frames.

## P2.1 Add spatially distributed track points

[x] Implement a feature-selection function for tracking.

First try existing repository extractors/utilities. If no suitable tracker seed selection exists, use OpenCV functionality already available, preferably Shi-Tomasi (`goodFeaturesToTrack`) or an equivalent existing detector.

Requirements:

- configurable maximum point count,
- image grid / tile quota so features cover the frame,
- minimum point spacing,
- retain response/quality score,
- deterministic enough for regression testing.

**Acceptance**

On the baseline dataset, selected points cover the image instead of collapsing into one high-texture region. Save one debug visualization.

## P2.2 Track points temporally with pyramidal optical flow

[x] Track the selected points between consecutive images or available preview frames.

Use existing OpenCV functionality if available in the current dependency set, preferably pyramidal Lucas-Kanade.

For every track observation store:

- image/frame ID,
- pixel coordinate,
- track ID,
- tracking error/status,
- track age.

**Acceptance**

The baseline sequence produces persistent multi-frame tracks and reports tracking runtime per image pair.

## P2.3 Reject unstable optical-flow tracks

[x] Add a forward/backward consistency check:

1. track image A → B,
2. track the result B → A,
3. reject points whose round-trip pixel error exceeds a configurable threshold.

Also reject points that leave valid image bounds or whose tracker status is invalid.

**Acceptance**

Debug output shows accepted/rejected counts and the median forward/backward error.

## P2.4 Replenish tracks only where needed

[x] When the active track count falls below a threshold, detect new points only in under-populated grid cells and away from active tracks.

Do not redetect the whole frame every time if enough tracks remain.

**Acceptance**

A longer sequence maintains approximately the requested active-track budget without resetting track IDs every frame.

---

# P3 — Use IMU rotation to separate useful parallax

Goal: distinguish image motion caused mostly by camera rotation from motion that provides translational baseline.

Implemented with the existing gyro integrator/mounting convention; see
[command, conventions, tests and baseline limitations](docs/architecture/gyro_parallax.md).
The baseline provides 311/206 gyro samples and 30 ranked tracks per selected pair.
Its large residuals are explicitly unverified and include timing/mounting/calibration
uncertainty; no metric translation or scale is estimated.

## P3.1 Reuse and expose gyro data at image/frame timestamps

[x] Use the existing sensor/session loader to obtain gyroscope samples around each image timestamp.

If interpolation/integration utilities already exist, reuse them. Otherwise add the minimum gyro integration needed to estimate **relative orientation only** over short intervals.

Do not attempt accelerometer-based position or metric scale.

**Acceptance**

For each consecutive image pair, output a relative rotation estimate and the number/time span of gyro samples used.

## P3.2 Define and test the device-to-camera rotation convention

[x] Identify the coordinate transform between Android sensor axes and the selected camera axes.

Reuse existing coordinate-convention documentation/tests if present.

Add a synthetic or controlled test that verifies the sign and axis convention for at least yaw, pitch and roll.

**Acceptance**

A known synthetic device rotation predicts the expected image-motion direction.

## P3.3 Predict rotation-only pixel motion

[x] Given intrinsics `K` and relative rotation `R`, predict where a tracked pixel would move if the camera only rotated.

For normalized image ray `x`:

```text
x' ~ K R K^-1 x
```

Compute the residual between the observed tracked position and this rotation-only prediction.

Call this residual `parallax_px` for now; do not interpret it as metric translation.

**Acceptance**

For a rotation-dominated synthetic test, median residual parallax is near zero. For a translated scene, residual parallax is measurably larger.

## P3.4 Rank tracks by geometric usefulness

[x] Add a lightweight score for pose initialization using only quantities available before a full 3D reconstruction.

Start with:

- track age,
- detector response,
- forward/backward flow error,
- residual parallax after rotation compensation,
- spatial cell occupancy.

Do not introduce a learned model.

**Acceptance**

The code can request the best `N` tracks and returns a spatially distributed set with non-trivial residual parallax.

---

# P4 — Bootstrap camera poses cheaply

Goal: obtain the first reliable camera pair without exhaustive matching.

## P4.1 Select a bootstrap pair from tracked parallax

[x] From the ordered images, choose the earliest pair that has:

- enough surviving tracks,
- sufficient spatial coverage,
- sufficient median residual parallax,
- no obvious degenerate pure-rotation behavior.

Thresholds must be configuration values and printed in diagnostics.

**Acceptance**

The baseline dataset either selects a pair and explains its score or fails with a clear reason.

## P4.2 Estimate the first relative pose

[x] Feed the selected 2D correspondences into the existing calibrated two-view geometry.

Reuse:

- `[R]` undistortion / normalized points,
- `[R]` essential-matrix estimation,
- `[R]` RANSAC geometric verification,
- `[R]` cheirality-based relative-pose recovery.

Do not create a second essential-matrix implementation.

**Acceptance**

The bootstrap reports:

- input track count,
- RANSAC inlier count,
- recovered relative rotation,
- translation direction,
- runtime.

## P4.3 Triangulate only strong bootstrap landmarks

[x] Use the existing triangulation code to initialize a small landmark set.

Keep only points satisfying configurable quality gates:

- positive depth / cheirality,
- reprojection-error limit,
- minimum triangulation angle,
- valid observations in both bootstrap views.

**Acceptance**

The bootstrap produces a compact 3D landmark set with per-point reprojection error and triangulation angle.

Implemented in `vision/bootstrap.py`, `vision/bootstrap_pipeline.py` and
`python/scripts/bootstrap_tracks.py`; see [P4 command and results](docs/architecture/tracked_bootstrap.md).
The fixed baseline rejects 0→1 for coverage and bootstraps 1→2 from 30 tracks:
22 essential inliers, 15 pose inliers, 11 landmarks, 0.335464 px RMSE.
Repeat scene geometry/masks are identical. Scale and physical geometry remain
unverified; no thresholds were relaxed and no dependency was added.

---

# P5 — Incremental pose estimation and guided correspondence

Goal: after the first two cameras, stop searching the whole image.

## P5.1 Add PnP pose estimation using existing OpenCV

[x] For a new image, collect 2D observations that correspond to already-triangulated 3D landmarks and estimate the new camera pose with PnP + RANSAC.

Before implementing, search for an existing PnP helper. If absent, use the OpenCV dependency already present; do not add a new solver library.

Prefer `solvePnPRansac` followed by an existing/refinement routine when justified.

**Acceptance**

A synthetic test recovers a known pose within defined rotation/translation-direction tolerances, and the baseline sequence can add at least one post-bootstrap camera when enough correspondences exist.

## P5.2 Predict landmark positions in the next image

[x] Project existing 3D landmarks into the next image using the current coarse pose.

For each projected landmark store:

- predicted pixel,
- visibility/in-bounds flag,
- current observation count,
- expected search radius.

Use gyro rotation as an optional prior before PnP when it is available and consistent.

**Acceptance**

Debug visualization overlays projected landmark positions on the new image.

## P5.3 Implement local descriptor/patch search around projections

[x] Search for each projected landmark only in a bounded local window instead of the full image.

First inspect and reuse the existing feature/descriptor implementation. Prefer using an already-supported descriptor or OpenCV primitive. Do not add SuperPoint, LightGlue or another ML dependency.

The search must support:

- configurable radius,
- best/second-best ambiguity check where applicable,
- optional mutual consistency,
- rejection when no convincing local candidate exists.

**Acceptance**

Report average candidates examined per landmark and runtime. On the baseline dataset, local search examines substantially fewer candidates than exhaustive pairwise matching.

## P5.4 Add epipolar-guided search for non-landmark features

[x] For features that do not yet correspond to a 3D landmark, use the current camera poses to restrict matching to an epipolar corridor.

Do not rectify the full image for sparse matching.

Search only within a configurable distance of the predicted epipolar line and within plausible image bounds.

**Acceptance**

A debug visualization shows the epipolar corridor and accepted/rejected candidate matches for a selected feature set.

## P5.5 Triangulate newly confirmed tracks

[x] Extend the scene with new landmarks only after guided correspondences pass geometric checks.

Reuse `[R]` track merging and `[R]` triangulation.

Retain per-landmark quality values:

- observation count,
- best/median triangulation angle,
- reprojection error.

**Acceptance**

Adding a camera can also grow the landmark set without rerunning global all-pairs matching.

Implemented with shared OpenCV PnP, existing NCC/SIFT matching, conflict-aware
tracks and triangulation; see [P5 command, outputs and evidence](docs/architecture/tracked_incremental.md).
Synthetic sufficient-support tests add cameras and landmarks, including local
patch recovery without temporal landmarks. The recorded five-image sequence
grows 11→20 landmarks; seed corridor search compares 1.977 descriptors/query
instead of 1,200. Later images have no verified 3D–2D support and remain explicitly
unregistered. Physical geometry/scale remain unverified; no dependency was added.

---

# P6 — Iterative refinement loop

Goal: use every improved pose estimate to make the next correspondence search narrower and more reliable.

## P6.1 Add local bundle adjustment around the newest camera

[x] Reuse the existing SciPy bundle-adjustment implementation.

Adapt it minimally so a local window can refine:

- the newest camera,
- a small configurable number of neighboring cameras,
- the landmarks observed by those cameras.

Keep the global gauge fixed using the existing convention.

Do not introduce Ceres, GTSAM or another optimizer.

**Acceptance**

Local BA lowers or preserves reprojection RMSE on the baseline sequence and reports runtime separately from matching/PnP.

## P6.2 Re-run guided correspondence after pose refinement

[x] After local BA:

1. reproject landmarks using the refined pose,
2. reduce the local search radius when uncertainty/reprojection error permits,
3. retry previously unmatched projected landmarks,
4. add newly confirmed observations,
5. triangulate newly complete tracks.

Limit the first prototype to a small fixed number of refinement rounds, e.g. 2.

**Acceptance**

The second guided-search round either adds valid observations/landmarks or exits because no meaningful improvement is available. It must not loop indefinitely.

## P6.3 Add point-confidence filtering for pose work

[x] Compute a simple landmark confidence score from existing geometric diagnostics.

Use only currently available measurements, initially:

- triangulation angle,
- reprojection error,
- observation count,
- consistency across views.

Use this score to decide which landmarks are trusted for PnP and pose refinement.

Do not implement dense surface reconstruction or interpolation here.

**Acceptance**

PnP can be run using only landmarks above a configurable confidence threshold, and diagnostics show how many points were excluded.

Implemented with selective variables in the existing SciPy solver,
`optimization/local_bundle.py`, `vision/refinement.py` and
`vision/landmark_confidence.py`; enable with `extend_tracked_scene.py --refine`.
See [P6 command, quality policy and measured results](docs/architecture/tracked_refinement.md).
The baseline local BA reduces same-set RMSE 0.338839→0.160348 px, adds two
landmarks through guided retries, and refines the expanded 22-point set
0.237967→0.172251 px. Two rounds stop when no new guided support appears.
Original gauge and raw inputs are preserved; later cameras still lack support.
Confidence thresholds gate both PnP and local BA without an untrusted fallback.

---

# P7 — Keyframes and on-device preview prototype

Do this only after P2–P6 work offline on recorded data.

## P7.1 Define keyframe criteria from the working offline pipeline

[x] Convert measured offline behavior into a keyframe score using:

- surviving track count,
- spatial coverage,
- gyro-compensated residual parallax,
- optionally image blur if an existing implementation already exists or OpenCV can provide it directly.

Avoid adding extra heuristics without evidence from the baseline sequence.

**Acceptance**

Running the score over a sequence labels frames as `skip` or `candidate keyframe` and prints the reason.

Implemented in `vision/keyframes.py`, `vision/keyframe_pipeline.py` and
`score_keyframes.py`, with the same Android `KeyframePolicy` and shared parity
fixtures. The baseline proposes image 2 and explains every skip; original gyro
quality warnings remain. See [policy and preview implementation notes](docs/architecture/preview_tracking.md).

## P7.2 Expose preview frames from Android capture

[~] Inspect the current CameraX/Camera2 pipeline and expose a low-resolution luminance image stream suitable for tracking while a session is active.

Keep full-resolution JPEG capture unchanged.

Do not add a new Android CV dependency in this task unless repository inspection demonstrates that the next task cannot reasonably be implemented without it.

**Acceptance**

The app can process or export timestamped low-resolution preview frames without interrupting sensor recording or still-image capture.

Implemented and build/JVM/lint validated: bounded cropped Y copies on a separate
CameraX analyzer queue, keep-only-latest frames, active-timeline gating, and a
fallback that preserves the original JPEG resolution. Physical-device stream
coexistence validation remains pending; see [P7 notes](docs/architecture/preview_tracking.md).

## P7.3 Prototype live feature tracking on Android

[~] Port only the already-validated minimum from P2/P3 needed to maintain tracks and estimate residual parallax on preview frames.

Before adding any library, explicitly check whether the current Android dependencies already provide the required operations. If not, document why a new dependency is necessary before adding it.

The live prototype only needs to output diagnostics such as:

```text
tracks: 742
median FB error: 0.8 px
rotation-compensated parallax: 31 px
keyframe: yes
```

No polished UI is required.

**Acceptance**

On a physical phone, the diagnostic values update during motion and a still image can still be captured normally.

Implemented in app 0.4 with bounded native OpenCV Shi–Tomasi/LK, P3-compatible
gyro integration, approximate crop-aware K, shared keyframe policy and live
UI/log diagnostics. The OpenCV Android dependency is justified in
[P7 dependency/validation notes](docs/architecture/preview_tracking.md).
Both APKs build; 40 JVM and 95 Python tests plus lint pass. Three native device
tests compile but are unexecuted. No phone is connected, so the required physical
motion/JPEG/sensor coexistence checks for P7.2/P7.3 remain pending.

---

# P8 — One prototype command and benchmark

## P8.1 Add the incremental reconstruction command

[ ] Add one command for the prototype, for example:

```bash
python python/scripts/reconstruct_incremental.py data/raw/<session>
```

Pipeline:

1. `[R]` load and validate session,
2. `[R]` load intrinsics / undistort,
3. select and track spatially distributed features,
4. use gyro to estimate relative rotation,
5. rank residual-parallax tracks,
6. choose bootstrap pair,
7. `[R]` estimate essential matrix and relative pose,
8. `[R]` triangulate strong initial landmarks,
9. add later cameras with PnP,
10. perform projected/local and epipolar-guided correspondence search,
11. `[R]` triangulate new landmarks,
12. `[R]` run local bundle adjustment,
13. repeat guided matching/refinement for a bounded number of rounds,
14. export scene and diagnostics.

**Acceptance**

One real short session completes without manual intermediate editing and reconstructs all cameras that have sufficient geometric support.

## P8.2 Add timing and quality diagnostics

[ ] Print and save per-stage metrics:

- feature-selection time,
- optical-flow time,
- gyro integration time,
- bootstrap RANSAC time,
- PnP time per camera,
- guided-search time per camera,
- local-BA time,
- total runtime,
- active track count,
- RANSAC/PnP inlier counts,
- landmark count,
- reprojection RMSE,
- triangulation-angle statistics.

**Acceptance**

A single report makes it possible to see whether runtime is dominated by tracking, matching, pose estimation or BA.

## P8.3 Compare against the existing baseline

[ ] Run the P0.2 baseline and the new incremental prototype on the same dataset.

Compare only prototype-relevant properties:

- total runtime,
- camera poses recovered,
- pose/reprojection consistency,
- number of descriptor candidates examined,
- number and quality of sparse landmarks.

Do not optimize for point-cloud density.

**Acceptance**

Create one short experiment note stating whether the incremental approach is faster and whether pose quality is preserved or improved.

---

# Definition of the prototype milestone

The current prototype is successful when:

1. a short ordered Android capture contains stable camera metadata plus gyro timestamps,
2. spatially distributed points are tracked cheaply across frames,
3. gyro rotation is used to distinguish rotation from useful residual parallax,
4. one strong image pair bootstraps the reconstruction,
5. later cameras are added primarily through 3D→2D correspondences and PnP,
6. correspondence search is local/projected or epipolar-guided rather than exhaustive,
7. local bundle adjustment improves the pose and enables a narrower second matching pass,
8. unreliable landmarks are excluded from pose estimation using geometric quality metrics,
9. the entire short sequence runs from one command with timing and quality diagnostics.

The prototype does **not** need metric scale, dense reconstruction, surface interpolation, measurement UI, cloud sync, polished Android UI, deep learned features, or production-ready visual-inertial odometry.
