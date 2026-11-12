# P9.1 tracking-frame sidecar, version 1

Implemented: P9.1 Python parsing/validation/inspection and P9.2 Android frame
export with periodic still capture in app 0.5.0. **Hybrid reconstruction remains
P9.3; physical stream/capture acceptance remains P9.4.**
No new dependency is needed. Existing schema-1 still/sensor sessions retain their
format and behavior. This extends the [capture contract](capture_format.md) and
implements the first step of [P9](hybrid_capture.md).

## Files and identities

```text
session.json                  # existing session metadata, schema 1
events.jsonl                  # existing still requests/results and timeline
sensors.csv                   # existing sensor samples
images/image_000.jpg           # existing full-quality stills
tracking.json                 # optional tracking manifest, independent schema 1
tracking/events.jsonl          # tracking records, one JSON object per line
tracking/frames/frame_000000.png
```

Absence of both `tracking.json` and `tracking/` means a legacy still-only session.
A tracking directory without its manifest is an incomplete export, not a legacy
session. A present manifest requires its records file, even when empty. Paths
must be safe relative paths; paths and symlinks escaping the session are rejected.
Preview filenames must be under `tracking/frames/` and end in `.png`.

`load_session` exposes `session.tracking: TrackingRecording | None`. Its `.frames`
are `TrackingFrame` objects; `session.images` remains exclusively still images.
Frame ID 0 and still index 0 are different identities. Neither implies a shared
exposure. A preview timestamp or scheduled timer tick cannot assign a still pose.

## Manifest

Example for one saved frame and no diagnostic events:

```json
{
  "schema_version": 1,
  "session_id": "same_id_as_session_json",
  "state": "complete",
  "failure_reason": null,
  "records_file": "tracking/events.jsonl",
  "pixel_format": "gray8_png",
  "settings": {
    "requested_width": 640,
    "requested_height": 480,
    "max_analysis_fps": 10,
    "still_interval_s": 1,
    "writer_queue_capacity": 4
  },
  "counts": {"frames": 1, "dropped_frames": 0, "resets": 0, "capture_events": 0}
}
```

States are `recording`, `ending`, `complete`, `interrupted`, and `unavailable`.
The first two fail finalized-session validation. `interrupted`/`unavailable`
require a nonempty `failure_reason`; `complete` cannot have a failure reason.
`unavailable` cannot contain saved frames. Its empty records file and explicit
reason describe a capture fallback without pretending tracking ran.

Counts must match records. `dropped_frames` counts offered frames in explicit
drop ranges; `capture_events` includes requested and skipped ticks. Requested
dimensions and queue capacity are positive integers; the analysis-rate cap and
still interval are positive finite numbers. They describe configured budgets,
not guaranteed or measured camera throughput.
P9.2 optionally adds boolean `settings.automatic_stills` and positive integer
`settings.diagnostic_queue_capacity` (32 in the app). The pixel queue capacity
counts waiting frame copies; one further frame can be inside the encoder.

## Common record fields and clocks

Every row has `type`, `sequence` and `recorded_timestamp_ns`. Sequence numbers are
contiguous from zero and identify append order. Append timestamps are
nondecreasing Android elapsed-realtime nanoseconds, at/after session start.
Queued writes may finish after the timeline ends: their append time is not an
exposure or reception time. The writer must keep each session's queue isolated.

Actual camera timestamps stay separate. A frame declares exactly
`android_elapsed_realtime_ns` when the camera's REALTIME source is known, or
`camera_unknown` otherwise. Unknown timestamps are retained as recorded; there
is no guessed offset to sensor time. Saved frame IDs increase in append order;
camera timestamps increase within consecutive records sharing a clock domain.
Reception timestamps are nondecreasing and lie strictly inside the active
session timeline. A REALTIME exposure must also be inside that timeline and
must not follow reception. Each reception must precede/equal append time.

Unknown clocks and missing intrinsics are supported but explicitly warn that
the corresponding gyro/still association is unavailable. REALTIME preview frames
without strict before/after gyro samples within the configured validation gap
also warn; validation does not invent a rotation. The current nominal gyro model
requires an unmirrored back camera, native target rotation and known quarter-turn
sensor mounting. Missing/unsupported mounting metadata remains visible.

## Frame record and coordinates

```json
{
  "type": "frame", "sequence": 0, "recorded_timestamp_ns": 1220000000,
  "frame_id": 0, "filename": "tracking/frames/frame_000000.png",
  "timestamp_ns": 1200000000,
  "clock_domain": "android_elapsed_realtime_ns",
  "received_timestamp_ns": 1201000000,
  "buffer_size": [640, 480],
  "buffer_crop_region": [0, 0, 640, 480], "sample_step": 1,
  "metadata": {
    "width": 640, "height": 480, "camera_id": "0",
    "physical_camera_id": null,
    "rotation_degrees": 90, "target_rotation": 0,
    "is_flipped": false, "lens_facing": 1, "sensor_orientation_degrees": 90
  },
  "intrinsics": {
    "image_width": 640, "image_height": 480,
    "camera_matrix": [[500, 0, 319.5], [0, 500, 239.5], [0, 0, 1]],
    "distortion": [0, 0, 0, 0, 0], "source": "metadata-derived",
    "approximate": true, "pixel_convention": "stored_pixels_ignore_exif",
    "camera_id": "0",
    "assumptions": ["Example nominal K; lens distortion is uncalibrated"]
  },
  "intrinsics_unavailable_reason": null,
  "diagnostics": {"quality_status": "nominal_preview_geometry_unverified"}
}
```

Pixels are native, unrotated, cropped/subsampled luminance, saved as a genuine
single-channel **8-bit PNG**. Validation checks the file signature, decoded type
and dimensions without converting color, bit depth or display orientation.
`metadata.width/height`, `camera_id`, `rotation_degrees`, and `is_flipped` are
required; other metadata uses the existing `ImageMetadata` field validation.
`diagnostics` is an optional object retaining the producer's tracking metrics;
its presence alone is not a successful pose or gyro result.

`buffer_size` is the original YUV buffer `[width, height]`.
`buffer_crop_region` is `[left, top, right, bottom]` in that buffer, with exclusive
right/bottom bounds. This is distinct from optional `metadata.crop_region`, which
is a Camera2 sensor-active-array crop. The positive integer `sample_step` uses
the existing `PreviewLumaSampler` mapping:

```text
saved_u = (buffer_u - left) / sample_step
saved_v = (buffer_v - top) / sample_step
saved_width  = ceil((right - left) / sample_step)
saved_height = ceil((bottom - top) / sample_step)
```

`TrackingFrame.buffer_to_saved_affine` exposes that mapping. This is integer
subsampling, not the pixel-center affine for an interpolating image resize.
The declared crop must fit the buffer and imply exactly the saved dimensions.

Intrinsics reuse `CameraIntrinsics`, describing **saved PNG coordinates** with
matching dimensions/camera ID. The existing source values, distortion model,
pixel convention and `approximate` consistency checks apply. Explicit source,
approximation flag, distortion array and assumptions array are required;
approximate K needs nonempty assumptions. If K is unavailable, write
`intrinsics: null` with a nonempty `intrinsics_unavailable_reason`; do not substitute
JPEG intrinsics or label an estimate as calibrated. A supplied K needs a null/
absent unavailable reason. Optional sensor-timestamp metadata must agree with
the frame's actual timestamp/clock.

Camera identity, geometry, clock or intrinsics changes without a covering reset
produce an inspection warning. P9.3 must split/reset those segments before using
tracks. Crop metadata and K availability do not prove preview/JPEG alignment;
cross-stream image correspondence and pose verification remain necessary.

## Drop, reset and automatic-capture records

These rows include the common sequence and append-time fields:

| Type | Additional fields | Meaning |
|---|---|---|
| `drop` | `first_frame_id`, positive `count`, nonempty `reason` | Offered IDs `[first_frame_id, first_frame_id + count)` were not saved. |
| `reset` | `next_frame_id`, nonempty `reason` | Tracking continuity resets before this offered frame ID. May point to the end if no frame follows. |
| `capture` | `scheduled_timestamp_ns`, `status`, `request_timestamp_ns`, `image_index`, `reason` | An automatic still tick was requested or skipped. |

Offered IDs are assigned to selected/copied frames presented to the export
writer, starting at zero. Saved IDs and explicit drop ranges must cover those
IDs exactly once, without holes or overlaps. Ranges are checked without expanding
each ID. Examples of reasons include `writer_queue_full`, `storage_error`,
`session_start`, `camera_geometry_changed`, and `camera_busy`.

Capture status is `requested` or `skipped`. A requested tick names exactly one
existing core `image_request` index and its actual request time; reason is null/
absent. It does not assert the JPEG succeeded. Existing `image`/`image_error`
events remain authoritative. A skipped tick has no request time or image index
and requires a reason. Scheduled times strictly increase and lie inside the
session; accepted request time lies between its tick and append time, inside
the session. Manual photos need no automatic tick record.

P9.2 reports a delayed scheduler as one skipped range: positive integer
`missed_tick_count` plus `last_skipped_timestamp_ns` on a `skipped` capture row.
Its `scheduled_timestamp_ns` is the first missed tick; the last is inclusive,
inside the timeline and at/before append time. The next capture row must follow
the entire range. The app then considers only the latest due tick, without
firing a burst of old photo requests. `capture_events` and `capture_status_counts`
count rows; inspector `automatic_skipped_tick_count` expands the ranges and
`scheduler_missed_tick_count` reports the missed-interval portion separately.

Native PNG export adds nonnegative `diagnostics.writer_queue_wait_ms` and
`diagnostics.png_export_runtime_ms`, alongside the existing flow/selection/gyro
diagnostics. Common append time remains separate from exposure/reception time.

Camera keep-only-latest drops and intentional analysis throttling occur upstream
of offered IDs and are **not** counted as explicit writer drops. The inspector
reports observed saved-frame rate/gaps, explicit drops and unknown acquisition
loss count, rather than claiming fixed-rate video or zero upstream loss.

## Inspection and implementation boundary

```bash
.venv/bin/python python/scripts/inspect_session.py SESSION --json
```

JSON adds `tracking` only when a sidecar exists; legacy summaries retain their
keys. Text output includes frame count, drops, resets, observed rate/gaps and
unknown clocks. Structural/JSON failures return exit 2; readable inconsistent
recordings return exit 1 with issue codes; unavailable tracking/gyro metadata
is reported as warnings. Default `load_session` raises for validation errors;
`validate=False` permits inspection without disabling structural field checks.

P9.1 changes the capture device metadata to read `app_version` and
`app_version_code` from generated Android `BuildConfig` values. Old raw labels
are not rewritten. Generated examples/tests validate this contract, not physical
camera performance. P9.2 implements the writer/scheduler; P9.3 will consume
the stream in reconstruction. Current P8 reconstruction remains still-only.
