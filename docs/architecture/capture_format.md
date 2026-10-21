# Android capture contract (schema version 1)

The capture app writes self-contained folders. Raw recordings remain immutable
after export; reconstruction outputs belong under `data/processed/`.

## Clock domains

Timeline start/end, button requests, and file-save callbacks use Android
`SystemClock.elapsedRealtimeNanos()`: nanoseconds since boot, including sleep.
Android `SensorEvent.timestamp` uses the same clock. UTC is used only for readable
session IDs and the informational `created_at_utc` field; it cannot be subtracted
from monotonic timestamps. Never compare monotonic times across phone reboots.

The primary `timestamp_ns` on an image is its **button request time**, also saved
as `request_timestamp_ns`. `callback_timestamp_ns` is sampled when CameraX's
file-save callback begins, before EXIF parsing. These bound capture latency but
are not exposure timestamps. The exposure occurred sometime during asynchronous
capture; neither endpoint should be used as an exact visual/IMU alignment time.
This first version deliberately leaves `sensor_timestamp_ns` and its clock domain
null. Reliable frame timestamp association is future work.

References: [Android SensorEvent timestamp](https://developer.android.com/reference/android/hardware/SensorEvent#timestamp)
and [CameraX file capture](https://developer.android.com/media/camera/camerax/take-photo).

## Session metadata

`session.json` contains:

- `schema_version`, unique `session_id`, `created_at_utc`, and `device` (manufacturer,
  model, Android API, app version).
- `state`: `recording`, `ending`, `complete`, or `interrupted`. Manual end produces
  `complete`; lifecycle end produces `interrupted` with an explicit `end_reason`.
- `sensors`: booleans for `accelerometer`, `gyroscope`, and `rotation_vector`,
  reflecting successful listener registration rather than advertised support alone.
- `timeline_start_ns`, and `timeline_end_ns` after successful finalization.
- `clock_domain`, `image_timestamp_source`, `image_count`, per-type `sensor_counts`,
  and `dropped_sensor_samples`.
- `sensor_order` and `sensor_reorder_window_ns` describe CSV ordering.

Metadata is replaced using a synced temporary file and atomic rename where
supported. Events are flushed individually; sensor output is flushed at least
every half second while samples arrive and at finalization. This limits normal
buffering, but does not guarantee persistence after power loss. A failed final
metadata update leaves an explicit unfinished state; never infer completeness
from filenames alone.

## Sensor CSV

```text
timestamp_ns,sensor_type,x,y,z,w,accuracy
```

Accelerometer components include gravity and use m/s². Gyroscope components use
rad/s. Rotation-vector components are a quaternion in **x, y, z, w** order, with
Android's device-to-reference orientation convention. `w` is empty for the other
sensor types. Accuracy is Android's raw sensor accuracy integer.

The axes are Android device axes, not optical camera axes. No gravity removal,
bias correction, camera/IMU transform, or integration is performed during capture.
See [Android motion sensor conventions](https://developer.android.com/develop/sensors-and-location/sensors/sensors_motion).

The recorder requests 100 Hz without batching; actual rates depend on hardware.
Independent sensors can arrive out of timestamp order. A 100 ms priority queue
merges them into globally nondecreasing CSV rows, while each stream remains
strictly increasing. Distinct sensors may share a timestamp. Duplicate,
non-finite, pre-start, future, unavailable-stream, and excessively late samples
are dropped and counted. Samples still queued at normal end are drained before
the end timestamp. Callbacks arriving after listener shutdown cannot write rows.

## Image events and metadata

Only successful, readable images produce `image` events:

```json
{
  "type": "image",
  "index": 0,
  "filename": "images/image_000.jpg",
  "timestamp_ns": 124100000000,
  "request_timestamp_ns": 124100000000,
  "callback_timestamp_ns": 124500000000,
  "timestamp_source": "button_request",
  "clock_domain": "android_elapsed_realtime_ns",
  "metadata": {
    "width": 4000,
    "height": 3000,
    "exif_orientation": 6,
    "rotation_degrees": 90,
    "is_flipped": false,
    "target_rotation": 0,
    "camera_id": "0",
    "lens_facing": 1,
    "sensor_orientation_degrees": 90,
    "sensor_width_mm": 5.6,
    "sensor_height_mm": 4.2,
    "focal_length_mm": 4.2,
    "focal_length_35mm": null,
    "exposure_time_s": 0.01,
    "iso": 100,
    "sensor_timestamp_ns": null,
    "sensor_timestamp_clock_domain": null,
    "metadata_source": "jpeg_exif_and_camera_characteristics"
  }
}
```

Dimensions describe the stored JPEG before EXIF orientation. Consumers must
handle EXIF rotation/flipping consistently when using pixel coordinates.
`target_rotation` is the Android Surface rotation enum, not degrees. The camera
ID is CameraX's selected logical camera ID; a logical camera may select different
physical lenses internally. Lens facing and native sensor orientation come from
Camera2 characteristics. Physical lens identity is not claimed.

EXIF fields are null when missing, rather than invented defaults. Physical sensor
size is a characteristic of the logical camera, not a calibrated active crop;
these values alone cannot guarantee accurate intrinsics. CameraX requests JPEG
quality 100 and its highest supported capture resolution for the chosen stream
configuration. Actual dimensions depend on the device and are recorded per image.

`image_request` records accepted button requests; `image_error` records failures.
`timeline_start` and `timeline_end` delimit the timeline. During end, new requests
are rejected and sensors keep running until the outstanding capture settles.
Then listeners are unregistered, sensor rows are flushed, `timeline_end` is
written, and final metadata is persisted. Temporary JPEGs are renamed only on
success. The scene loader should use `image` events as the authoritative list.
