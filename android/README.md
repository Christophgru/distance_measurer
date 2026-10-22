# Android capture app

One Kotlin Activity uses CameraX to save high-quality JPEGs while recording an
accelerometer, gyroscope, and optional compass, rotation vector and game rotation
vector. App 0.2 retains all available streams without fusing them during capture.
Requires Android 8.0+
(API 26). Capture runs while the Activity is in the foreground.

App 0.2 builds with `assembleDebug`; its **17 JVM tests** and `lintDebug` pass.
Compass availability, JPEG metadata/orientation and REALTIME frame timestamps
still require the physical-device checks below.

## Build and install

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

On Windows use `gradlew.bat`. The checked-in wrapper pins Gradle 8.11.1 and
verifies its distribution checksum. First build needs network access to download
Gradle and Maven dependencies. No Android Studio project generation is required.

## Capture

1. Enable USB debugging on a physical phone and authorize its connection. Launch
   **Distance capture** and grant camera access. An accelerometer and gyroscope
   must be present; missing rotation-vector support is allowed.
2. Tap **Start timeline** and hold the phone completely still for at least two
   seconds. Then take overlapping pictures while slowly translating the phone
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
