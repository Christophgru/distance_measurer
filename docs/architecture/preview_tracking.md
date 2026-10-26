# P7: keyframes and Android preview tracking

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

## Physical-device status

No physical Android device is connected to this workspace (`adb devices -l`
returns an empty list). P7.3's requirement to observe diagnostics while moving
a real phone and capture a normal still photo therefore remains pending.
Build/JVM/synthetic results must not be presented as physical-device acceptance.
