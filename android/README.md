# Android capture app

App **0.6.1 (code 9)** removes the optional ARCore experiment and its Google Play
Services for AR dependency. **Start timeline** is the single capture flow, with
our existing OpenCV-based feature tracking, gyro/parallax/keyframe diagnostics,
recorded previews, high-quality JPEGs and sensors. The capture format and app
signing identity are preserved. Full live 6-DoF pose and metric measurement are
still development work; the current phone diagnostics are 2D tracking estimates.
See [our streaming-pose direction](../docs/architecture/live_pose.md).

The app 0.6.0 ARCore experiment is withdrawn. Its source snapshot and prior
evaluation evidence are retained under `data/processed/p10_own_tracking/withdrawn_arcore/`
and `data/processed/p10_live_pose/` respectively, outside the active build.

One Kotlin Activity uses CameraX to save high-quality JPEGs while recording an
accelerometer, gyroscope, and optional compass, rotation vector and game rotation
vector. App 0.2 retains all available streams without fusing them during capture.
Requires Android 8.0+
(API 26). Capture runs while the Activity is in the foreground.

App 0.3 adds observed per-image Camera2 geometry metadata and a default capture
stability mode. It requests a fixed lens/zoom and holds focus/crop after
convergence where supported, with automatic fallback. See
[behavior and inspection](../docs/architecture/capture_stability.md). Hardware
capture and vendor lock behavior still require the physical-device checks below.
The debug APK builds; **30 JVM tests** and `lintDebug` pass.

App **0.4** adds optional low-resolution preview analysis with live track/FB/
gyro-parallax/keyframe diagnostics. It retains normal JPEG resolution or falls
back to capture without analysis. The pinned OpenCV Android AAR supplies the
same Shi–Tomasi/LK primitives used offline; the dependency rationale, coordinates,
clock limitations and phone checklist are in
[P7 preview tracking](../docs/architecture/preview_tracking.md).
The debug and instrumentation APKs build, **40 JVM tests** and `lintDebug` pass.
Native instrumentation tests and physical camera coexistence remain pending;
no phone is connected to the build workspace.

App **0.4.1** keeps the debug signing key in the persistent checkout and offers
standalone APKs for individual CPU architectures. Capture/tracking algorithms
are unchanged by this packaging patch. The main app has an explicit launcher
icon; the separate instrumentation package is labeled **Distance capture tests
(no launcher)** and is not the app to open on the phone.

## Build and install

App **0.5.0 (code 6)** implements [P9.2 hybrid capture](../docs/architecture/hybrid_capture.md#p92-implementation-and-use):
selected low-resolution luminance frames/diagnostics are saved alongside JPEGs
and sensors. Automatic photos default to every 1 s; choose 0.25–60 s before Start
or uncheck automatic photos for manual stills. Pixel/control queues are bounded,
losses are recorded, and End drains export asynchronously. Stream support,
actual cadence, JPEG interruptions and native execution still need the combined
phone check. Python hybrid reconstruction is provided by the
[P9.3 command](../docs/architecture/hybrid_replay.md).

App **0.5.1 (code 7)** fixes foreground End during an in-flight still. Automatic
requests stop immediately, while analysis continues through JPEG completion and
until a subsequent preview has entered the bounded writer queue. The tail wait
after the JPEG settles is limited to 2 s; unavailable streams and background/
destruction end preview promptly. The original 30 s JPEG watchdog remains.
Session metadata records the preview-end decision, comparison timestamp and
whether the photo boundary came from actual exposure or callback. Writer drain
still determines whether queued frames were successfully saved. End/restart
behavior on the phone and native instrumentation execution remain P9.4 checks.
Install artifacts and refinement evidence are in
`data/processed/2026-10-27_153337_135_48fb0956/refinement_p9/`.
The clean build passes **64 JVM tests** and Android lint; both main and native-test
APKs compile. The associated offline refinement passes **908 Python tests**.

P9.1 fixes recorded build provenance: new sessions take `device.app_version` and
`device.app_version_code` from generated `BuildConfig`, rather than the previous
hardcoded `0.4.0`. No existing raw session is rewritten. The
[tracking-frame contract](../docs/architecture/tracking_capture_format.md) is now
used by the P9.2 writer and periodic-still diagnostics.

Install JDK 17 and the Android SDK (Android Studio's SDK Manager or command-line
tools), then install these SDK packages:

```bash
sdkmanager 'platforms;android-35' 'build-tools;35.0.0' 'platform-tools'
```

Set `ANDROID_HOME` to your SDK directory, or put `sdk.dir=/absolute/sdk/path` in
`android/local.properties` (ignored). From `android/`:

```bash
./gradlew :app:assembleDebug :app:testDebugUnitTest :app:lintDebug
adb devices
adb install -r app/build/outputs/apk/debug/app-debug.apk
adb shell am start -n org.distancemeasuring.capture/.MainActivity
```

While a timeline is active, inspect preview diagnostics using
`adb logcat -s PreviewTracking:I '*:S'`. Build the native test APK with
`./gradlew :app:assembleDebugAndroidTest`; run it on a connected device with
`./gradlew :app:connectedDebugAndroidTest`. Compiling those tests is separate
from running them and from the physical camera/JPEG checklist.

On Windows use `gradlew.bat`. The checked-in wrapper pins Gradle 8.11.1 and
verifies its distribution checksum. First build needs network access to download
Gradle and Maven dependencies. No Android Studio project generation is required.

### Signing identity and smaller APKs

Debug builds now use `android/.signing/debug.keystore` (ignored by Git). On its
first use, the build adopts an existing `~/.android/debug.keystore` when present;
otherwise it generates an initial debug key using JDK `keytool`. The local key
is never replaced automatically. Keep a backup and restore it when moving to
another checkout; deleting it or losing the checkout can change signing identity.
An explicit existing location can be selected with
`-PcaptureDebugKeystore=/absolute/path/debug.keystore`. This is a development key,
not a production release-signing setup.

The default build still produces the universal `app-debug.apk`. To also build
smaller standalone APKs, from `android/` run:

```bash
./gradlew :app:assembleDebug -PsplitApks=true
```

Outputs include `app-arm64-v8a-debug.apk`, `app-armeabi-v7a-debug.apk`,
`app-x86-debug.apk`, `app-x86_64-debug.apk`, and `app-universal-debug.apk` under
`app/build/outputs/apk/debug/`. Install just the APK matching the phone's ABI;
these are complete standalone APKs, not a set of configuration splits. On the
computer connected to the phone, `adb shell getprop ro.product.cpu.abilist`
reports its supported ABIs. ARM64 phones can use:

```bash
adb install --no-streaming -r app/build/outputs/apk/debug/app-arm64-v8a-debug.apk
```

ADB reports an installation failure code when available; an install progress bar
alone does not identify a Play Protect, transfer, storage, or package failure.
Keep the phone online if it requests an online security scan. Stable signing and
smaller APKs do not guarantee Play Protect approval. Distribution beyond local
development should use a maintained release key and an appropriate testing or
store distribution channel.

An update requires the same signing identity as the installed app. If ADB reports
`INSTALL_FAILED_UPDATE_INCOMPATIBLE`, recover the original key where possible.
Do not uninstall an existing app until its sessions have been copied: uninstalling
deletes its app-specific recordings. The build cannot recover a lost old key.

The phone app is **Distance capture** (`org.distancemeasuring.capture`). Do not
install `app-debug-androidTest.apk` as the phone app: that package contains the
instrumentation runner and deliberately has no launcher activity. If the main
package is installed but its launcher entry is unavailable, diagnose from the
connected computer with:

```bash
adb shell am start -W -n org.distancemeasuring.capture/.MainActivity
```

## Capture

1. Enable USB debugging on a physical phone and authorize its connection. Launch
   **Distance capture** and grant camera access. An accelerometer and gyroscope
   must be present; missing rotation-vector support is allowed.
2. Tap **Start timeline** and hold the phone completely still for at least two
   seconds to allow camera convergence and stability controls. Then take
   overlapping pictures while slowly translating the phone
   around a textured, stationary scene. Every selected photo must connect to the
   others through overlaps; disconnected reconstruction aborts.
3. Wait for each image counter increment before taking the next picture. Capture
   is serialized, and the picture button is disabled while a JPEG is being saved.
4. Hold still for at least two seconds after the last image, tap **End timeline**,
   and wait for **saved**.
   The session path remains visible for copying.

Starting is disabled during recording and finalization. Ending during a photo
waits for that capture to finish or fail before closing the timeline. Sensors
continue during that wait. A 30-second watchdog records a failed capture if the
camera does not respond; late output is discarded. A failed photo gets an
`image_error` event and does not increment the image counter. File indices may
therefore have gaps after failures.

Leaving the app or turning the screen off requests a safe end with an
`activity_paused` reason and an `interrupted` session state. Returning allows a
new session. Rotation is handled without recreating the Activity; camera target
rotation is sampled for each capture. Force-stop, power loss, or a storage failure
can leave a recording unfinished. A session without `timeline_end`, or with
`state` equal to `recording`/`ending`, must be treated as incomplete. No background
service or automatic recovery is included.

## Copy a session manually

Sessions are stored in app-specific external storage, normally:

```text
/sdcard/Android/data/org.distancemeasuring.capture/files/sessions/<session_id>/
```

Session IDs contain UTC date/time, milliseconds, and a random suffix. Each folder
contains `session.json`, `sensors.csv`, `events.jsonl`, and `images/`. App-specific
storage is deleted when the app is uninstalled; copy recordings first.

From the repository root, list and copy an ended session (replace the example
ID with the directory shown on the phone):

```bash
adb shell ls /sdcard/Android/data/org.distancemeasuring.capture/files/sessions
SESSION_ID=2026-10-21_134500_123_a1b2c3d4
adb pull "/sdcard/Android/data/org.distancemeasuring.capture/files/sessions/$SESSION_ID" data/raw/
```

If a device restricts shell access to `Android/data`, the debug build supports
copying as the app's UID:

```bash
adb exec-out run-as org.distancemeasuring.capture tar \
  -C /sdcard/Android/data/org.distancemeasuring.capture/files/sessions \
  -cf - "$SESSION_ID" | tar -xf - -C data/raw
```

Read image metadata using ordinary Python, without Android tooling:

```bash
python - "$SESSION_ID" <<'PY'
import json
import pathlib
import sys

path = pathlib.Path('data/raw') / sys.argv[1]
session = json.loads((path / 'session.json').read_text())
print(session['session_id'], session['state'], session['sensors'])
for line in (path / 'events.jsonl').read_text().splitlines():
    event = json.loads(line)
    if event['type'] == 'image':
        print(event['filename'], event['timestamp_ns'], event['metadata'])
PY
```

The detailed data contract and clock definitions are in
[capture_format.md](../docs/architecture/capture_format.md).

Print per-image settings and observed lens/focal/focus/crop stability with
`python python/scripts/inspect_session.py data/raw/<session_id>` (add `--json`
for machine-readable output). Unknown/missing values do not establish stability;
check actual values as well as `capture_stability.phase_at_request`.

## Physical-device acceptance checklist

JVM tests validate file/state behavior, including error paths. These checks need
a physical phone; a build alone does not establish hardware capture quality:

- Launch, grant permission, and check the single screen and live preview. Deny
  permission once and confirm a useful explanation appears.
- Start/end two timelines. Confirm separate directories and disabled buttons
  prevent overlapping sessions.
- Capture three pictures. Confirm exactly three decodable JPEGs and three
  `image` events; inspect full image dimensions, orientation, and EXIF metadata.
- Check both required sensor streams span the image request timestamps, maintain
  increasing per-stream timestamps, and contain no samples at/after timeline end.
  Inspect sample rates, gaps, and `dropped_sensor_samples` on the actual phone.
- Test a device without a rotation-vector sensor when available: it should store
  `false` in the availability map while the required streams keep recording.
- Check availability/counts for `magnetometer` (microtesla) and
  `game_rotation_vector` (xyzw). Their absence must not stop capture.
- Inspect each JPEG's camera frame timestamp and clock. On REALTIME-clock
  devices, primary source should be `camera_frame` with plausible request/frame/
  callback order. Unknown clocks must retain the button-request fallback.
  Confirm JPEG dimensions, rotation, EXIF exposure/focal length and readability
  after the captured-buffer save path on the actual phone.
- End during capture, rotate, background, and close/reopen. Confirm no orphan
  JPEGs, missing end events in normally finalized sessions, or overlapping files.
- Copy an ended session with the commands above and read it using Python.
