# Our streaming camera-pose direction (P10)

The selected direction is a slim native app demonstrating our own tracking and
reconstruction algorithms. App **0.6.1 (9)** withdraws the ARCore experiment:
its SDK, manifest entries, screen, camera handoff and associated export code are
removed. Google Play Services for AR is not required. The existing AndroidX
camera APIs and OpenCV remain low-level building blocks, not a complete external
pose-estimation backend.

## What works today

The Android capture screen runs our existing preview tracking pipeline: feature
selection/tracks, forward/backward checks, gyro-assisted residual parallax and
keyframe decisions. It records bounded grayscale PNG previews alongside sensors
and full-quality JPEGs. This is live **2D tracking**, not a completed live 6-DoF
estimator. Use **Start timeline → Take picture / automatic photos → End timeline**;
new recordings remain under `files/sessions/` in the P9 format.

Our offline P2–P6 pipeline tracks the preview stream, checks actual JPEG pixels
against both exposure-bracketing previews, bootstraps geometry, registers later
cameras with PnP, grows landmarks and refines with local bundle adjustment. The
October 28 capture registers all 16 JPEG cameras with 2,604 landmarks, 7,703
observations and 1.1597 px RMSE at 1600-pixel working resolution. The run took
11.34 minutes; it is a reference for geometry, not live performance. Results:
[October 28 report](../../data/processed/2026-10-28_152754_143_84510fbf/REPORT.md).

## Next implementation

The current priority is the implemented
[offline measurement prototype](measurement_prototype.md) and its unresolved
[physical validation](../experiments/p10_measurement_stability.md), as tracked in
[P10.3](../../TODO.md#p103-add-a-small-measurement-demonstration-with-explicit-scale).
Calibration intake is ready, but matching physical recordings remain outstanding.
While capture is unavailable, review saved observations and geometry diagnostics
and maintain the existing code/tests. This work does not establish physical
accuracy or close the outstanding phone acceptance checks.

The subsequent streaming stage is to extract a one-frame-at-a-time Python
processor from the existing building
blocks. Retain only a bounded active map, keyframes, images and queues. Distinguish
waiting for useful motion, bootstrap, tracked pose, lost tracking and reset.
Reject unsupported poses. Keep mapping/refinement budgets explicit and report
per-stage costs. Test recorded previews and controlled synthetic geometry before
porting the supported loop into the current Android analyzer. Avoid a second
camera stack or another complete SLAM SDK.

P2 feature tracking, P3 gyro handling, P4 bootstrap, P5 PnP/triangulation and P6
confidence/refinement are reused. Gyro mounting, clocks and nominal intrinsics
must remain explicit: the current recorded-data diagnostics already flag cases
where nominal gyro compensation increases image motion. Do not call all gyro
residuals translation or replace missing sensor intervals with identity.

Show our work as it becomes available: tracked features, keyframe decisions,
pose status, camera trajectory in reconstruction units and sparse-map/reprojection
support. Keep live pose and background image refinement distinguishable. Measure
actual frame-to-pose latency and bounded-state behavior before making a real-time
claim. Existing October 28 P9 data is sufficient to start this Python stage; an
ARCore recording is no longer needed.

## Scope and measurement

Keep one native Kotlin app, existing OpenCV primitives and our own geometry/state
logic. The Python pipeline stays an offline development/reference tool. New
frameworks, cloud services and complete external SLAM engines are outside the
selected scope. OpenCV is a large native dependency but supplies the tested
feature/LK and numerical primitives needed by this approach. Shrink packaging
only after measuring the current build and preserving required functionality.

Monocular visual geometry and gyro rotation provide no absolute metric scale.
A small measurement demonstration can use supported 3D endpoints and an explicit
known-length reference. Proper visual-inertial metric scale requires additional
calibration/estimation work; accelerometer double integration is not a substitute.
Neither low pixel residuals nor successful camera registration establish physical
accuracy. Compare a scaled measurement against an independent measured length.

## Historical experiment

App 0.6.0's ARCore screen, format, inspector and tests are withdrawn from active
source. Their before-removal snapshots are retained under
`data/processed/p10_own_tracking/withdrawn_arcore/source/` with SHA-256 hashes;
original build/size/fixture evidence remains under `data/processed/p10_live_pose/`.
No captured session or prior APK is deleted. The original SDK software checks
were not physical-device validation, and no ARCore phone test remains on the
current roadmap. Use the 0.6.1 main APK for the current capture flow.
