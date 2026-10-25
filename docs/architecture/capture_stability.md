# P1 capture settings and prototype stability mode

App 0.3 extends schema-1 image metadata with optional Camera2 settings. Older
recordings remain readable. No Android CV or Python dependency was added;
CameraX/Camera2, AndroidX, the standard library and the existing numerical stack
are sufficient.

## Inspect every image

From the repository root:

```bash
.venv/bin/python python/scripts/inspect_session.py data/raw/measurement01
.venv/bin/python python/scripts/inspect_session.py data/raw/measurement01 --json
```

Both forms include every successful image's camera and physical lens ID, focal
lengths, dimensions, orientation, exposure/ISO, focus distance, zoom/crop, 3A
modes/states/locks and original camera sensor timestamp/domain. JSON places
these under `capture_geometry.images`; existing top-level image summaries are
preserved. `capture_geometry.fields` records per-setting stability and the image
IDs with observations. `camera_settings_changed` warnings identify changing
settings without making an otherwise valid capture unreadable.

`capture_geometry.stability` reports `lens`, `focal_length`, `focus` and `crop`
as `stable`, `changed` or `unknown`. Stability needs at least two images and
complete observations. A change between known observations is still detected
when intermediate values are missing. Lens stability needs both logical and
physical identities. Crop stability checks the rectangle and, when reported,
zoom ratio; a rectangle alone is sufficient on older devices. Floating settings
use relative tolerance `1e-5` and absolute tolerance `1e-9`; rectangles, enums and
IDs compare exactly. Timestamps and transient 3A/lens states are printed but are
not treated as fixed settings. Sensor-frame times are retained as integers.

The existing `measurement01` capture passes inspection. It reports focal length
stable; lens, focus and crop unknown; and changing exposure and ISO. Its absent
new metadata is not evidence of a stable physical capture. Raw files are not
changed or backfilled.

## Per-frame metadata and association

`CaptureGeometry.kt` reads Camera2 results using the existing session callback.
`FrameMetadataStore.kt` joins these to the JPEG's `ImageInfo.timestamp` by exact
sensor timestamp. It retains 120 recent results plus eight still results, so
slow JPEG delivery does not immediately lose its settings to preview eviction.
If the result has not arrived when the JPEG callback runs, the sensor worker
yields for at most 500 ms before saving. It does not block sensor delivery during
that wait. Callback time still means entry to the original image callback.

Only exact matches supply dynamic geometry fields and set
`capture_result_source="exact_sensor_timestamp"`. Otherwise it is `unavailable`,
and missing fields stay unavailable; a nearby preview result is never substituted.
Available capture-result focal length, exposure and ISO reuse the existing field
names and take precedence over EXIF. Dimensions and orientation retain the
existing stored-JPEG convention. The primary image timestamp and clock fallback
rules are unchanged.

`physical_camera_id` is the selected physical result ID for a pinned output, the
reported active physical ID for a logical output, or the camera ID for a known
single-camera device. A pinned output uses its physical result metadata rather
than the logical camera's potentially different active sensor. Missing physical
results leave the association unavailable. See Android's
[total capture results](https://developer.android.com/reference/android/hardware/camera2/TotalCaptureResult)
and [capture-result fields](https://developer.android.com/reference/android/hardware/camera2/CaptureResult).

New optional fields:

| Field | Meaning |
| --- | --- |
| `physical_camera_id` | Observed physical camera ID, or null when unavailable. |
| `focus_distance_diopters` | Lens focus distance in diopters; zero means infinity/fixed infinity focus, not a missing value. |
| `crop_region` | Sensor crop `[left, top, right, bottom]`; not an EXIF-rotated JPEG rectangle. |
| `zoom_ratio` | Reported Camera2 zoom ratio where available (API 30+). |
| `ae_mode`, `ae_state`, `ae_lock` | Actual auto-exposure mode, transient state and lock. |
| `awb_mode`, `awb_state`, `awb_lock` | Actual auto-white-balance mode, transient state and lock. |
| `af_mode`, `af_state`, `lens_state` | Actual focus mode/state and lens-motion state. AF states 4/5 mean focused/not-focused locked; manual AF-off focus has no separate AF-lock request key. |
| `capture_result_source` | Exact association or unavailable. |
| `capture_stability` | Requested mode and phase at the button request, distinct from observed per-frame settings. |

## Default stability behavior

`CaptureStabilityPolicy.kt` and `CaptureStability.kt` implement one prototype
default, without another settings screen:

1. Keep the same selected logical camera. On API 28+ logical multi-cameras,
   request one physical camera for both preview and JPEG, chosen once near the
   logical camera's first advertised focal length. A synchronous bind rejection
   or asynchronous stream-configuration error retries without the physical pin.
   The fallback remains visible in `capture_stability.physical_camera_pin`.
2. Request 1× zoom and expose success/unavailability in `zoom_1x`.
3. At timeline start, wait for three consecutive frames with focused AF (or
   stationary manual/fixed focus), converged/locked AE (or manual AE), a
   no reported lens motion and consistent reported physical ID, focal length,
   controllable focus and crop.
   Focus-request comparisons allow `1e-4` diopter rounding noise. Missing
   convergence metadata leaves automatic capture available.
4. Hold the observed sensor crop. When manual focus is supported, request AF off
   with the observed focus distance. Fixed-focus cameras need no focus request.
   Request AWB lock only when advertised and already converged/locked. AE remains
   automatic; no AE lock is requested.
5. On unsupported/failed control application, clear the request overrides and
   continue through the automatic path. A two-second application timeout also
   requests fallback. The controller makes one attempt per timeline and ignores
   late completions from an earlier attempt. End-of-timeline restores automatic
   controls after a pending still is saved.

Control requests apply through CameraX's existing
[Camera2 control interface](https://developer.android.com/reference/androidx/camera/camera2/interop/Camera2CameraControl).
Physical stream selection uses the existing
[Camera2 interop extender](https://developer.android.com/reference/androidx/camera/camera2/interop/Camera2Interop.Extender).
The repository remains pinned to CameraX 1.4.2. The application neither submits
independent Camera2 requests nor closes CameraX's underlying capture session.

`capture_stability.phase_at_request` is `waiting_for_convergence`, `applying`,
`applied`, `automatic_fallback` or `disabled`; failures carry a reason. Requested
focus/crop/AWB fields describe the attempted controls. `applied` means the
CameraX request future completed; it does not certify the still's settings or
physical stability. Inspect the actual per-image fields and sequence stability
report for that. Automatic capture remains possible before convergence.

## Verification and remaining device check

Automated checks cover exact timestamp association, bounded eviction, slow still
delivery, metadata snapshots, recording-field preservation, convergence/reset,
fixed-focus and unsupported controls, one attempt per timeline, fallback state,
old/new schema loading, missing/partial data and per-image change diagnostics.
The debug APK builds; **30 Android JVM tests** and `lintDebug` pass with the
existing dependency versions (lint reports zero errors and nine existing
dependency/manifest/UI warnings). **170 Python tests** pass across the new
geometry tests, dataset schema/validation, intrinsics, session inspection and
calibration commands. Repository Ruff lint and formatting checks pass.

The unchanged P0 command was rerun to
`data/processed/measurement01/prototype_baseline_after_p1`: **33.402 seconds**,
318 F-verified pair matches, 283 pose inliers, three cameras, 206 sparse points
and refined RMSE 0.269012 working pixels. Raw input hashes, pair counts and all
saved refined cameras, points and observations match P0 exactly. This is a
compatibility check on the old capture, not a test of new phone controls or a
speed improvement claim.

No Android device is attached in this environment. Physical behavior therefore
remains unverified: install `android/app/build/outputs/apk/debug/app-debug.apk`,
start a timeline after aiming at a textured stationary scene, allow convergence,
capture at least three overlapping images, end and copy the recording, then run
the inspection command above. Check actual lens/focal/focus/crop stability and
the exact-result association status. Repeat after ending/resuming and on a
device lacking optional controls; its recording must still complete. Builds and
synthetic metadata tests cannot establish vendor-specific lock behavior or
camera/IMU exposure-time accuracy.
