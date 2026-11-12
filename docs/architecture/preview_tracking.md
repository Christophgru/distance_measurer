# P7: keyframes and Android preview tracking

[P9.2 hybrid capture](hybrid_capture.md#p92-implementation-and-use) now persists
the tracking stream and adds periodic high-quality stills in app 0.5.0. The P7
algorithm and keyframe-policy description below remains applicable; original
log-only/manual capture limitations are superseded by P9.2's sidecar and timer.
P7 device acceptance can be checked alongside the combined P9 phone test.

## P7.1 keyframe policy

```bash
python python/scripts/score_keyframes.py \
  data/processed/measurement01/prototype_parallax_p3_full \
  --output data/processed/measurement01/prototype_keyframes_p7
```

Choose a fresh output directory outside raw/P3 inputs. The command reads P3's
saved correspondences, reuses P2's grid mapping and labels each selected image
`skip` or `candidate keyframe` with explicit reasons. Consumed inputs are hashed
before/after processing. `keyframes.json` retains input quality warnings and the
gyro-compensation-increases-motion diagnostic. A candidate is a pre-geometry
proposal, not a verified pose or evidence of metric translation.

Thresholds reuse P4's measured support gates: at least 24 surviving tracks with
finite residual parallax, at least 6 occupied cells in each view on a 6×8 grid,
and median gyro-compensated residual of at least 3 px at a 1600 px long edge.
The last gate scales by the actual long edge (`3/1600`), giving 1.2 px at 640 px.
The bounded score is the minimum of normalized track, coverage and parallax
support, capped at one. Rotation/parallax must be available. The first image is
a reference and skipped. No blur heuristic or automatic JPEG trigger is added.

`vision/keyframes.py` and Android `KeyframePolicy.kt` share defaults and the
original fixtures in `tools/validation/keyframe_cases.json`; Python and JVM
tests check parity. Criteria compare consecutive processed frames, not motion
accumulated since the last candidate. Android preview candidates remain
unverified when K/mounting are approximate, just as offline P3 residuals do.

The five-image baseline skips 0 (reference), 1 (coverage), 3 (only three surviving
tracks), and 4 (no surviving tracks/residual). Image 2 is a candidate. Its P3
gyro compensation increases apparent motion; that warning is retained, not
interpreted as calibrated translation or silently removed from the report.

P7.1 validation: 8 Python tests and 32 Android JVM tests pass, including shared
cross-language defaults/cases. The five-image command labels all frames without
changing raw or P3 inputs.

## Android dependency decision, before implementation

Inspection of `android/app/build.gradle.kts`, `MainActivity`, `CaptureRecorder`
and CameraX usage finds no existing Android optical-flow implementation. The
current dependencies provide preview/JPEG/sensor acquisition and metadata, but
no Shi–Tomasi or pyramidal LK primitive. Reimplementing these solvers in Kotlin
would duplicate the validated P2 algorithms. The P7.3 prototype therefore uses
the official **`org.opencv:opencv:4.12.0` Android AAR**, pinned to an available
release, to call the same detector and LK primitives. Python dependencies are
unchanged. OpenCV is required only for optional live analysis; a loading failure
must leave normal capture usable.

This is an explicit dependency-gate exception based on repository inspection,
not a new optimizer/ML stack. Official references:
[OpenCV Android Maven setup](https://docs.opencv.org/4.12.0/d5/df8/tutorial_dev_with_OCV_on_Android.html),
[OpenCV Android distribution](https://opencv.org/opencv4android-usage-models/),
and [CameraX non-blocking image analysis](https://developer.android.com/media/camera/camerax/analyze).

## P7.2 luminance stream

CameraX `ImageAnalysis` requests 640×480 YUV with keep-only-latest backpressure.
`PreviewAnalyzer` samples at most ten frames per second only during an active,
non-ending timeline, copies a bounded unrotated Y image, and closes every
`ImageProxy` in `finally` before processing the owned copy. Y-plane position,
row/pixel stride, crop, native dimensions, sample step, rotation metadata and
camera-frame timestamp are retained. Analysis has its own executor, separate
from sensor/file/JPEG work; session tokens suppress late UI results.

Binding first measures the existing two-use-case JPEG resolution, then attempts
the optional analysis use case. If JPEG resolution changes, the combination is
unsupported, or resolution is unknown, normal JPEG capture is retained without
analysis. Asynchronous configuration errors first drop analysis, then apply the
existing physical-camera fallback. Device-specific stream behavior still needs
the phone checks below.

The debug APK, `lintDebug` and 35 JVM tests pass, including original capture
tests and Y-plane crop/stride/sampling tests. UI timestamps demonstrate the
stream interface once a timeline is active. P7 originally wrote no preview
images/events; P9.2 now saves a separate tracking sidecar.

## P7.3 live tracking prototype

App 0.4 adds `PreviewFlowTracker`, using the same OpenCV Shi–Tomasi and forward/
reverse pyramidal LK primitives as P2. The preview budget is 240 points, on the
same 6×8 integer-boundary grid, with 8 px exclusion spacing, quality 0.01, a 31 px
LK window and four pyramid levels. Failed/out-of-bounds/nonfinite flow and
round-trip error above 1.5 px are rejected. Persistent IDs/ages survive accepted
flow. When fewer than 75% of the budget remain, only under-populated cells are
replenished. The active count includes newly seeded points; surviving/valid
parallax counts are reported separately.

`CaptureRecorder.previewGyro` hands off at most 2,048 already-recorded gyro
samples. Sensors and CSV/JPEG writes retain their original worker. Analysis
copies a snapshot and ports P3's clipped-endpoint, midpoint right-composed
rotation integration and nominal back-camera mounting. Shared original cases
in `tools/validation/preview_gyro_cases.json` verify Kotlin/Python agreement on
stationary, yaw, pitch, quarter-turn mounting and noncommuting rotations, plus
pixel prediction with unequal/skewed intrinsics. There is no IMU translation,
gravity/scale estimate or new optical-flow solver.

`LivePreviewProcessor` delays analysis by one copied frame to give sensor
callbacks time to bracket both frame timestamps. Extrapolation is disabled;
gaps above 250 ms, missing samples, unknown camera clocks, front-camera mounting,
or unavailable intrinsics produce explicit unavailable-parallax/skip results.
The stream uses native, unrotated buffer pixels. `rotationDegrees` remains
metadata rather than rotating tracked coordinates. Layout, known lens and
nominal intrinsic changes reset the flow reference. A new session clears the
reference and starts a fresh gyro buffer.

Focal length, physical sensor size, active/crop region and the exact preview
crop/sample transform provide **approximate** K. There is no calibrated
distortion correction or calibrated camera/IMU extrinsic. Exact timestamp
metadata is read with a defensive non-consuming `FrameMetadataStore.peek`;
analysis cannot steal metadata needed by an overlapping JPEG. Camera clock
comparability is enabled only for a declared REALTIME timestamp source.
Keyframe labels remain proposals requiring later geometry verification.

During an active timeline, the existing screen displays track/survival count,
median FB error, rotation-compensated residual parallax, keyframe label and
unavailable/reset reason. This keyframe policy never triggers a JPEG itself;
P9.2 adds an independent optional periodic timer. Compact JSON
is also logged at most twice per second:

```bash
adb logcat -s PreviewTracking:I '*:S'
```

Logs include timestamp/clock, dimensions, sample step, one-frame delay, active/
surviving/valid tracks, source/target coverage, FB/parallax, compensation warnings,
gyro sample count/gaps/reasons, candidate thresholds, and flow/selection runtimes.
P7 originally kept these diagnostics only in logs. P9.2 persists them with native
grayscale preview frames in the versioned tracking sidecar. The sensor/JPEG
schemas and manual photo control remain compatible; new device metadata uses
the actual build version name/code.

OpenCV loads lazily on the analysis worker with one native thread. A load/flow
failure reports unavailable/reset diagnostics and leaves JPEG controls enabled.
Camera buffers close before processing; native Mats are explicitly released
on the owner worker and the processor is closed at Activity destruction.
The official AAR includes several native ABIs; the universal debug APK is
approximately 140 MB.

The AndroidX JUnit extension/runner are added **only to `androidTest`**, because
OpenCV's Android JNI cannot execute as an ordinary host JVM unit test. Three
instrumentation tests cover translation/ID persistence, pure-rotation residuals,
and the processor's bracketing/unknown-clock path using generated textures.
They compile into a separate test APK and remain unexecuted here. They do not
replace a physical camera/sensor/JPEG coexistence check.

## Validation and build artifacts

```bash
cd android
JAVA_HOME=/path/to/jdk17 ./gradlew :app:assembleDebug :app:assembleDebugAndroidTest \
  :app:testDebugUnitTest :app:lintDebug
```

Both APKs build, lint passes, **40 JVM tests pass**, and **95 Python focused/
regression tests pass** across policy, shared gyro contracts and P2/P3 tracking/
rotation stage paths. Python Ruff lint/format and whitespace checks pass. Existing
capture/JPEG/session JVM tests remain covered. These checks do not establish
native tracking runtime or camera stream quality on a device.

APK paths from the repository root:

- `android/app/build/outputs/apk/debug/app-debug.apk`
- `android/app/build/outputs/apk/androidTest/debug/app-debug-androidTest.apk`

## Physical-device status

No physical Android device is connected to this workspace (`adb devices -l`
returns an empty list). P7.3's requirement to observe diagnostics while moving
a real phone and capture a normal still photo therefore remains pending.
Build/JVM/synthetic results must not be presented as physical-device acceptance.

On a connected physical phone:

1. Install the debug APK, launch, grant camera permission, and start a timeline.
   Confirm diagnostics update on a textured scene. Hold still, rotate, and
   translate; inspect the FB error, gyro window, residual and candidate reasons.
   Unsupported clocks/mounting must display unavailable residual and `skip`.
2. Capture three ordinary JPEGs while diagnostics update. Check photo count,
   full dimensions, readability, matching events and continued sensor coverage.
   Confirm the analyzer is disabled rather than reducing the original JPEG
   resolution on an unsupported stream combination.
3. End during a photo, background/return, rotate and start a second session.
   Check the original capture lifecycle behavior and that old diagnostics/gyro
   intervals do not leak into the next session.
4. Run `./gradlew :app:connectedDebugAndroidTest` for the native synthetic tests.
   Record phone model/API, test results and camera/sensor/JPEG observations
   before marking P7.2/P7.3 acceptance complete.
