# TODO — Fast camera-pose prototype

This TODO is intentionally scoped to **prototyping fast and robust camera-pose recovery** from a short, ordered phone capture sequence.

The coding agent should execute **one task at a time**, keep changes small, and prefer existing code over new implementations.

Capture direction (2026-10-27): the intended input is continuous handheld video
tracking with occasional high-quality stills. Widely separated still images
remain stress cases; recovering disconnected views is not the next priority.
P9 adds recorded low-resolution tracking frames and approximately one still per
second, building on the existing P7 preview path and P8 offline pipeline. Offline
and implementation work can continue before another phone test. P7 physical
acceptance stays pending until the combined P9 capture has been checked on a
phone; compiled/native or synthetic tests do not close that requirement.

Project direction (2026-10-28, supersedes the earlier ARCore evaluation): keep
the project slim and demonstrate our own tracking/reconstruction work. Remove
ARCore and the Google Play Services for AR requirement. Retain native Kotlin,
CameraX/Camera2 and the existing OpenCV numerical/feature primitives. Reuse our
P2–P6 algorithms and P7/P9 capture rather than adding another complete SLAM SDK.
OpenCV remains useful even though its native library dominates APK size; measure
future packaging improvements without replacing algorithm work with a service.

P10 now develops our own bounded streaming pose estimator, first on the existing
recorded previews and then in the current Android analysis path. P9 is the
offline reference. A new ARCore phone recording is no longer a prerequisite.
Distinguish current live 2D tracking from future live 6-DoF pose, and visual
reconstruction units from measured metric scale. Preserve raw data and existing
P9 readers. Keep processing budgets and failures observable.

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

Offline continuation evidence: [nine-image P2–P6 refinement](docs/experiments/p26_sequence_refinement.md)
registers all nine cameras with verified PnP, bounded local BA and confidence
filtering. P7 physical-device acceptance remains pending below.

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

Implemented offline: [command and report contract](docs/architecture/incremental_command.md),
[measured comparison](docs/experiments/p8_incremental.md). One-command runs recover
9/9 reference cameras and 5/5 explicitly selected October 27 cameras; both
reproduce prior staged geometry exactly. The full October 27 sequence remains
2/9 and correctly exits as partial. The fresh fixed P0.2 comparison shows no
speed/quality improvement: 34.47 s and 2/3 cameras versus 33.19 s and 3/3.
49 focused tests, 114 stage regressions and 58 artifact checks pass. P7 physical
and native-device acceptance remains pending.

## P8.1 Add the incremental reconstruction command

[x] Add one command for the prototype, for example:

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

[x] Print and save per-stage metrics:

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

[x] Run the P0.2 baseline and the new incremental prototype on the same dataset.

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

# P9 — Continuous tracking frames with periodic high-quality stills

This is the next stage after P8, added for the intended video-tracking use case.
The implementation contract and validation order are in
[hybrid capture plan](docs/architecture/hybrid_capture.md). The existing roadmap
previously ended at P8; this stage concerns capture/tracking, independent of the
older M9 metric-measurement work.

Start with the existing requested 640×480 luminance analysis stream and current
maximum processing rate of 10 Hz. Measure actual frame cadence and gaps before
changing that budget. Request a high-quality JPEG approximately every second
during an active session, with a configurable interval and one capture in flight.
These are initial budgets, not promises of device throughput or sufficient
parallax. Higher-rate analysis and motion/quality-based still selection follow
measured evidence. A compressed video file is optional; timestamped replayable
tracking frames are required for the first prototype.

## P9.1 Define the recording contract and offline ingestion

[x] Specify a versioned optional tracking-frame sidecar and add its Python loader/
validator using existing capture schemas, camera models and test fixtures.

Implemented: [tracking-frame contract](docs/architecture/tracking_capture_format.md),
typed optional `session.tracking`, consistency/pixel/clock validation and existing
inspector integration. Preview/still identities remain separate. New Android
builds record `BuildConfig` version name/code. 273 Python tests, 40 Android JVM
tests, Kotlin compilation and Android lint pass. Three existing real sessions
retain identical inspection summaries and raw hashes. Synthetic example and
validation evidence: `data/processed/p9_contract/`. Subsequent capture, replay
and physical acceptance evidence is tracked in P9.2–P9.4 below.

- `[R]` Keep existing still-image/session/sensor recordings readable and immutable.
- Record stable frame IDs, actual camera timestamps and clock domain, saved pixel
  data, dimensions, crop/sample/rotation transforms, lens/intrinsics provenance,
  and capture/drop/reset diagnostics. Keep preview IDs distinct from still IDs.
- Associate streams through actual timestamps and image evidence; never equate a
  timer request, callback, or encoded frame number with an exposure timestamp.
- Export enough metadata to detect unsupported preview/still geometry or clocks.
- Record the actual build version rather than the current hardcoded `0.4.0`
  session label, so the next phone test identifies its capture implementation.

**Acceptance**

Original sessions load unchanged. Generated valid/invalid hybrid fixtures verify
ordering, safe paths, clock availability, frame dimensions/transforms and explicit
drops. A capture-format document distinguishes implemented fields from proposed
ones before the phone writer uses them.

## P9.2 Record tracking frames and schedule periodic stills

[x] Extend the existing P7 analyzer and capture lifecycle with bounded frame
export and optional automatic still requests at a configurable default of 1 s.

Implemented in app 0.5.0 (code 6): [capture flow and lifecycle](docs/architecture/hybrid_capture.md#p92-implementation-and-use).
The existing luminance/gyro processor exports native grayscale PNGs and full
diagnostics through a bounded writer, with explicit drop/reset/failure records.
Automatic photos default to 1 s (configurable 0.25–60 s); manual capture shares
the existing one-photo limit. Delayed ticks, stale UI callbacks, End/background,
writer drain and session isolation have focused checks. 57 JVM and 296 Python
tests pass; main/native-test APKs build and lint passes. A JVM-generated session
and nine actual writer-output cases pass independent Python format validation.
Four native tests compile but are unexecuted. APKs, signatures, source hashes and
evidence: `data/processed/p9_capture/`. The first combined phone recording is
assessed under P9.4; P9.3 replay and remaining phone checks are tracked below.

- `[R]` Reuse `PreviewAnalyzer`, `PreviewLumaSampler`, `LivePreviewProcessor`,
  `CaptureRecorder`, `RecordingSession` and their independent workers.
- Persist replayable frames plus tracking/gyro diagnostics on a bounded writer
  queue. Close camera buffers promptly; report drops and storage failures rather
  than queueing unlimited frames or blocking sensor/JPEG recording.
- Allow one still in flight; record delayed/skipped ticks and actual exposures.
  Stop scheduling on end/background, preserve manual capture, and prevent late
  callbacks or frames leaking into another session.
- Preserve the existing JPEG-resolution fallback and explicitly report when
  hybrid recording is unavailable. Reuse current dependencies; evaluate an
  encoded-video path only if measured storage/throughput requires it.
- Keep timed capture separate from the current consecutive-preview keyframe
  hint. Future adaptive capture must evaluate motion since the last selected
  keyframe, not assume per-frame residuals accumulate automatically.

**Acceptance**

Build/JVM/native compilation and lifecycle/queue/scheduler tests pass. Output uses
the P9.1 contract; slow storage and a busy camera produce bounded, explained
losses. Actual simultaneous camera evidence and remaining device checks are
tracked in P9.4.

## P9.3 Replay continuous tracks and register the high-quality stills

[x] Extend the existing P8 command to consume the hybrid recording, maintaining
temporal tracks between stills and verifying still-image poses with image evidence.

Implemented [hybrid replay](docs/architecture/hybrid_replay.md) in the existing
command: globally distinct preview track IDs, explicit continuity segments,
actual-exposure brackets and independently measured JPEG observations checked
from both previews. The existing P3–P6 geometry and thresholds register/refine
JPEG cameras. Default `auto` uses hybrid sidecars; `stills` keeps P8 behavior.
Reports include separate capture timing, offline/device continuity, per-still
image support, costs, runtime and quality. Full-suite validation: 901 Python
tests pass; after tightening exposure-time diagnostics, all 29 focused hybrid/
report tests pass. Ruff lint/formatting pass. Real replay retains all 29 stills
and 435 previews: one preview segment, 28/29 verified 2D associations, **24/29
registered cameras**, 3,632 points, 10,204 observations, **1.0566 px RMSE** at
1600-pixel working resolution. Stills 4/5/26/27 fail PnP; 28 lacks a following
preview. Cameras 21/22 initially register with only 8/9 PnP inliers, a low-margin
acceptance before subsequent refinement.
The command correctly exits 1 with a partial scene. All 469 raw files are
unchanged; exported depths/gauge/residuals and unsupported-camera exclusion
pass verification. [Results and limitations](data/processed/2026-10-27_153337_135_48fb0956/REPORT.md).

Runtime remains a limitation: 2,331.9 s inside the pipeline, including 2,031.9 s
for incremental registration/refinement; local BA takes 750.6 s and patch search
609.0 s. This is an offline prototype, with no real-time or metric-accuracy claim.
Further pose robustness and bounded matching/refinement cost need tuning before
treating this as a validated distance-measurement workflow.

[x] Focused refinement follow-up: app 0.5.1 (7) preserves the foreground End
preview tail through an in-flight JPEG, with a bounded 2 s post-photo wait and
explicit end diagnostics. Background/destruction still interrupt promptly.
64 JVM tests, a clean main/native-test APK build and Android lint pass. The
Python pass removes repeated full-image work, batches existing projection/
confidence measurements and avoids repeated BA parameter/report bookkeeping.
908 full-suite Python tests and 101 focused checks pass. Unchanged-selection
replay takes **1,313.9 s / 21.90 min versus 2,331.9 s / 38.86 min** (1.77×);
BA is 4.38× and local patch search 2.24× faster. Camera rotations/centers, point
coordinates and all observation pixels/IDs match the baseline exactly: still
24/29 cameras, 3,632 points, 10,204 observations and 1.056617 px RMSE. All raw
hashes, geometry/gauge and configuration checks pass. Pose failures/weak views
remain explicitly supported by the audit; no gate is loosened. The signed ARM64
APK has the same certificate and verified launcher. Native execution and
physical End/restart behavior remain P9.4 checks.
[Results and artifacts](data/processed/2026-10-27_153337_135_48fb0956/refinement_p9/REPORT.md).

- `[R]` Reuse P2/P3 tracking/gyro, P4 bootstrap, P5 PnP/guided matching and P6 BA.
- Handle preview and JPEG intrinsics, crop, distortion and capture time
  separately. Pixel scaling or choosing the nearest preview pose alone cannot
  establish a valid still pose; verify cross-stream correspondences geometrically.
- Report track continuity, frame gaps/resets, JPEG-induced tracking pauses,
  request/exposure cadence, still registration support, runtime and quality.
- Retain all captured frames/stills in raw data and explicitly label unsupported
  segments. Do not force connections through a lost track interval.

**Acceptance**

Synthetic hybrid sequences exercise translation, rotation, differing crops/
resolutions, timestamp offsets, missing frames and still capture pauses. One
command produces an honest complete/partial report. Existing still-only P8
results retain their behavior. Real-video continuity claims require P9.4 data.

## P9.4 Validate the combined capture on a physical phone

[~] After the new capture build is ready, record one short continuous session
with periodic JPEGs and export the complete session for offline replay.

First physical recording supplied: `2026-10-27_153337_135_48fb0956`, OnePlus
CPH2581 / API 36 / app 0.5.0 (6), complete 58.398 s. Inspection confirms 435
readable 640×480 previews and 29 readable 4096×3072 JPEGs with camera exposure
times, sensors and stored live diagnostics. Saved rate is 7.498 Hz, maximum gap
166.64 ms, zero writer drops and no all-track loss after initialization.
JPEG-window gaps remain regular. A 1 s timer produces ~2 s exposures because
29 alternate ticks are `camera_busy`; median callback latency is 1.306 s.
Six sensor samples were reported dropped; one initial preview is not gyro-bracketed.
The final JPEG follows the last preview by 99.98 ms and cannot pass dual-preview
association. [Capture assessment](data/processed/2026-10-27_153337_135_48fb0956/REPORT.md).

This supplies real P7 stream-coexistence and stored tracking-update evidence.
On-screen diagnostics, the hold/rotation/translation protocol, end/restart
isolation and four native instrumentation executions remain unverified.
Do not equate recording inspection with these device checks.

Second physical capture: `2026-10-28_152754_143_84510fbf`, the same phone on app
0.6.0 (8), complete 32.369 s. It passes inspection with 16 readable 4096×3072
JPEGs and 242 readable 640×480 previews, 7.501 Hz saved rate, maximum gap
133.308 ms and no explicit frame export drops. Recorded track survival stays
173–240 after initialization. All 16 JPEGs now have before/after timing brackets;
the last saved preview follows the final exposure by 233.29 ms, independently
supporting the stored `following_preview_queued` End decision. Median JPEG
exposure cadence remains 2.000 s; 15 ticks skip while busy. Nine sensor samples
are reported dropped, with no validation coverage error. All 263 raw hashes are
unchanged. This validates capture/tail evidence; UI protocol, End-during-photo,
restart/background and native execution checks remain open.
[Assessment](data/processed/2026-10-28_152754_143_84510fbf/REPORT.md).

The same October 28 capture also completes an unchanged-settings P9 replay:
**16/16 cameras**, one continuous preview segment, all 16 dual-preview JPEG
associations (564–751 tracks), 2,604 landmarks, 7,703 observations and **1.1597 px
RMSE**. The final JPEG registers with 190/389 PnP inliers. Independent depth,
rotation, gauge, source/settings and all 263 raw-hash checks pass. CLI exit 0;
process runtime 680.23 s / 11.34 min. This closes the missing final-image timing
and geometric-support issue for this run; it does not validate physical scale,
native UI/lifecycle checks or P10 latency.
[Full replay report](data/processed/2026-10-28_152754_143_84510fbf/prototype_hybrid_p9/REPORT.md).

Current-app physical capture: `2026-10-28_225648_021_a0d054e8`, app 0.6.1 (9),
complete 30.383 s with 15 readable 4096×3072 JPEGs and 227 readable 640×480
previews. Inspection passes with zero reported sensor or frame-export drops,
7.501 Hz saved rate, max gap 143.307 ms, no complete 2D track loss and a saved
preview 233.29 ms after the final JPEG. JPEG exposure intervals remain about
1.995 s despite 1 s timer requests. Unchanged-settings image replay verifies
all 15 JPEGs with 500–685 dual-preview correspondences in one segment (98.65 s).
All 247 raw hashes and algorithm source are unchanged. This is capture and 2D
image support evidence; 3D camera registration was not run at that inspection.
[Assessment](data/processed/2026-10-28_225648_021_a0d054e8/REPORT.md).

Experimental follow-up (2026-10-29) completes unchanged-settings reconstruction
on that 22:56 capture: **15/15 cameras**, 1,872 landmarks, 5,232 observations and
**1.064 px RMSE**; CLI exit 0, process time 540.77 s / 9.01 min. Seven camera
registrations are flagged for <=12 PnP inliers or <20% inlier fraction (indices
2, 5, 6, 7, 9, 10, 11). Independent rotations/depth/gauge, prior image-support,
source and all 247 raw-hash checks pass. This is an experimental sparse visual
map with arbitrary scale, not the P10 bounded streaming implementation or a
physical-accuracy pass. The exported offline viewer retains all points and
cameras. [Experiment and viewer](data/processed/2026-10-28_225648_021_a0d054e8/EXPERIMENT.md).

Follow-up tuning: measure low-latency still capture versus the current quality
mode before promising 1 Hz. App 0.5.1 introduced the bounded End preview tail;
the October 28 app 0.6.0 recording supplies saved-tail and final-image evidence.
End-during-photo UI and restart/background behavior still need the phone checks.
Existing raw recordings remain unchanged, including the missing final bracket
in the October 27 session.

**Acceptance**

For roughly 20–30 s of gentle motion over a textured scene, live diagnostics
update, tracking frames and readable full-quality JPEGs are saved, sensors remain
covered, and actual cadence/gaps (especially during JPEG captures) are reported.
Include a brief hold, rotation and translation, then an end/restart check. Record
phone/build identity and native instrumentation results separately from observed
camera behavior. Combine this with the pending P7 motion/JPEG/sensor checks;
mark each acceptance only from its corresponding evidence. Poor throughput or
unsupported streams produce a measured limitation and tuning task, not a pass.

Another still-only recording is **not** a prerequisite for P9.1–P9.3. This combined
phone test is required before claiming the hybrid mode works on the device.

---

# P10 — Our own bounded streaming pose estimator

The user has chosen to retain our algorithm work and withdraw ARCore. The old
SDK experiment is historical evidence, not the selected backend or an outstanding
phone-test requirement. [Current scope](docs/architecture/live_pose.md).

## P10.0 Withdraw the ARCore experiment

[x] App 0.6.1 (9) removes the ARCore Gradle dependency, service/feature manifest
entries, camera handoff, live screen and SDK-only data/export code. Archive the
experiment and its inspector/tests outside active source. Preserve the existing
capture/preview path, signing identity, P9 format and raw recordings. Publish a
new main APK and verify the packaged manifest, dependencies and native libraries.
Evidence: `data/processed/p10_own_tracking/`.

## P10.1 Replay the existing algorithm as a bounded frame processor

[ ] First implement and measure in Python on saved P9 tracking frames. Reuse P2
feature tracking, P3 gyro utilities, P4 essential/cheirality bootstrap, P5 robust
PnP and triangulation, and P6 landmark confidence/local refinement. Give the
processor one ordered frame at a time; do not repeatedly reconstruct the full
prefix. Keep only a bounded active map, keyframes and working images, with
explicit initialization/tracking/loss/reset states. Report separate feature,
pose, map-growth and refinement costs plus map/queue sizes.

Run the October 28 recording as the first real reference: its offline result
registers 16/16 JPEGs with 2,604 landmarks and 1.1597 px RMSE. Reuse synthetic
translation, rotation-only, bad geometry, loss/recovery and clock tests. Preserve
existing acceptance gates; do not assign poses to frames lacking support.
The old ARCore log is not required, and another physical capture is not needed
to begin this stage. Current capture data already supplies continuous previews.

Use the 15:27 session (`2026-10-28_152754_143_84510fbf`) for that known geometric
reference and the newer 22:56 app 0.6.1 session (`2026-10-28_225648_021_a0d054e8`)
as an additional input: it supplies 227 continuous previews and image-supported
observations for all 15 JPEGs, including closer desk views and darker conditions.

**Acceptance**

One-pass replay exports honest valid/invalid pose states, retained-state limits
and per-frame timing. Memory/work do not grow with the entire recording history.
Compare pose support and reprojection with the offline reference, accounting for
visual gauge. Measure the 100–200 ms responsiveness target rather than assuming
that 2D LK runtime is full pose latency. No new SLAM framework or Python dependency
is part of this stage.

## P10.2 Integrate the supported pose loop into Android

[ ] After P10.1 has usable timing/geometry evidence, adapt the current analyzer
and OpenCV primitives for bootstrap, PnP and bounded map updates. Preserve the
single camera/capture lifecycle and independent JPEG/sensor recording. Put
nonessential refinement away from the pose-update path. Show tracked features,
keyframe decisions, valid camera motion and map/reprojection support as each is
implemented. Label 2D-only states explicitly; never display invented 6-DoF poses.

**Acceptance**

Compile and test math/state/queue boundaries, then measure on the phone during
hold/translation/rotation and JPEGs, End/restart and background/return. Report
actual pose cadence/age, rejected updates, map bounds and tracking loss. Existing
P7/P9.4 native and UI checks remain separate physical acceptance items.

## P10.3 Add a small measurement demonstration with explicit scale

**Priority:** build the offline reference-scaled measurement prototype before
P10.1/P10.2 optimization and integration. The visible ruler in
`2026-10-28_225648_021_a0d054e8` supplies an initial fixture. Its exploratory
two-view check is published in
`data/processed/2026-10-28_225648_021_a0d054e8/ruler_validation/REPORT.md`:
held-out 3/2/5 cm spans measure 3.36/1.94/5.28 cm, but pixel-localization
sensitivity is large and additional-view mark transfer failed. Keep metric
acceptance open; review marks and establish independent-view repeatability.

[ ] Keep the measurement demonstration narrow: supported 3D endpoints, distance
in reconstruction units, and an optional known-length reference for metric
scale. Check against an independently measured object and report repeatability.
Monocular vision plus gyro alone does not establish metric scale. Full
accelerometer/IMU fusion is separate work, not naive double integration or a
prerequisite for a reference-scaled demonstration. Keep high-quality JPEG
registration/refinement image-supported and outside the live critical path.

[x] Implement the offline endpoint/known-reference prototype and full-resolution
photo annotation page, with scene binding, frozen cameras, one fixed primary
scale, explicit failed/missing views, held-out lengths, repeatability and
pixel-localization sensitivity. Reuse existing geometry and dependencies.
See `docs/architecture/measurement_prototype.md`; latest recording evidence is
under `data/processed/2026-10-28_225648_021_a0d054e8/measurement_prototype/`.

[ ] Confirm physical mark correspondences and demonstrate stable independent-view
lengths, then check a separately measured object. The first single-pass manual
later-view candidates pass basic reprojection but do not repeat the earlier
lengths at a fixed scale. Do not mark metric accuracy accepted or optimize the
streaming loop on the assumption that this has passed.

[x] Investigate a texture-supported planar alternative and a ruler-excluded
background refinement before optimizing streaming. Reuse existing camera math,
OpenCV and SciPy. Publish the original/conditional/refined comparisons, maintain
independent unused-view checks and preserve the original scene/annotations.
Evidence: `docs/experiments/p10_measurement_stability.md` and the latest session's
`measurement_stability/` outputs. 69 focused tests pass. The plane makes ruler
numbers steadier but fails unused-view support; the background trial does not
resolve it. Keep both experimental.

[ ] Capture matching-settings checkerboard calibration and a sharp lateral-motion
measurement session with a broad flat target and separately measured object.
Printable target/protocol are in the latest session's
`measurement_stability/capture_kit/`. Resolve geometry/support and physical
accuracy before treating metric measurement or P10.1 optimization as ready.

[x] Prepare recording intake for that physical test: validate structure/settings,
detect/refine native JPEG board corners, reserve views before calibration, test
unused corners with frozen K/distortion and prepare a reconstruction command
only for matching inputs. Preserve raw hashes and never apply a synthetic or
failed calibration to the real recording. See `docs/architecture/calibration_intake.md`.
The existing desk recording correctly detects 0/15 boards; no camera profile or
reconstruction is produced. The physical recordings remain outstanding.

---

# Definition of the P0–P8 prototype milestone

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
