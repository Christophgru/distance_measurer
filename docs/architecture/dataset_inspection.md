# Dataset loading and inspection (M2)

The Python dataset package implements the Android
[capture contract](capture_format.md). Input files are read without rewriting or
sorting them. Regression fixtures are generated from original small images and
synthetic sensor rows; personal recordings are never required by the test suite.

## API

```python
from distance_measuring.dataset import (
    DatasetFormatError,
    SessionValidationError,
    load_session,
    validate_session,
)

try:
    session = load_session("data/raw/measurement01")
except DatasetFormatError as error:
    print(error)  # source file/line and malformed field
except SessionValidationError as error:
    for issue in error.report.errors:
        print(issue.code, issue.message)
```

`Session` holds a resolved directory, `SessionMetadata`, typed `SensorSample`
records, and the ordered event log. `session.images` provides successful
`ImageEvent` records with `ImageMetadata`; `session.sensor_streams` exposes
accelerometer, gyroscope, and rotation-vector tuples. Timestamps remain integer
nanoseconds. `session.image_path(image)` resolves a safe file path. Image arrays
are decoded during validation and discarded, keeping the loaded session compact.

Schema `from_dict`/`from_csv` constructors reject missing required fields, wrong
types, booleans masquerading as numbers, non-finite numbers, unsupported schema
versions/sensor types, and unsafe filenames. JSON duplicate keys and NaN/Infinity
constants are rejected. CSV headers must match the Android contract exactly;
rotation vectors require `w`, and the other streams leave it empty. Original
metadata, including extra camera fields, is retained in each model's `raw` map.

Session ID, schema version, device object, and the three sensor availability
booleans are required in `session.json`. Image index, relative filename, and
timestamp are required in image events. Camera metadata may be absent or null,
which produces warnings; present fields must have valid values. Timeline
boundaries are required in the event log and checked against session metadata
when duplicated there. Finalization state and clock domain may be omitted by
older captures, but this is reported explicitly. Unknown event types retain their
timestamp and original payload for inspection.

`load_session(path)` validates by default and raises on errors. For diagnostics
on an inconsistent recording, use `load_session(path, validate=False)` followed by
`validate_session(session)`. Disabling timeline validation never disables schema
checks. Reports contain stable issue codes and separate errors/warnings; errors
can be raised together with `report.raise_for_errors()`.

## Validation rules

- Exactly one start and end event delimit a positive-duration timeline. The log
  begins/ends with those markers; all other event timestamps lie strictly inside.
  Image timestamps and indices increase strictly. Failed requests may leave gaps
  in image indices.
- Sensor rows are globally nondecreasing and each sensor stream is strictly
  increasing. Equal timestamps from different sensors are allowed. Every sample
  lies strictly inside the timeline; advertised availability and saved counts
  agree with actual records.
- Every enabled stream must bracket each image timestamp with a sample strictly
  before and after it. The bracketing interval must be at most 0.25 seconds by
  default. This catches both missing coverage and internal outages. The API's
  `max_sensor_gap_s` or CLI's `--max-sensor-gap-s` explicitly changes that limit.
  Long gaps elsewhere are warnings, and maximum gaps are always printed.
- Image counts/filenames are consistent, files stay within the session directory
  (including symlink resolution), images decode, and decoded dimensions match
  metadata. OpenCV reads stored pixels with
  [`IMREAD_IGNORE_ORIENTATION`](https://docs.opencv.org/4.x/d8/d6a/group__imgcodecs__flags.html)
  so EXIF auto-rotation does not silently swap width and height.
- Image callbacks occur after their primary timestamp and before the timeline
  ends. A button-request primary timestamp agrees with its explicitly stored
  request timestamp. Image and session clock domains must agree when specified.

An interrupted but fully finalized session is loadable with a warning. A state
of `recording` or `ending` is an error. Missing camera metadata, unavailable
optional sensors, dropped samples, non-unit orientation quaternions, failed
capture attempts, and approximate image times are visible warnings.

Any number of images is accepted here, including five-image `measurement01`.
This milestone checks dataset consistency; it does not establish sufficient
parallax, visual match quality, calibrated intrinsics, or metric accuracy.
Reconstruction image selection belongs to a later milestone. Renaming a copied
folder does not change or invalidate its original recorded `session_id`.

## Command and outputs

From the repository root, with the editable Python package installed:

```bash
python python/scripts/inspect_session.py data/raw/measurement01
mkdir -p data/processed/measurement01
python python/scripts/inspect_session.py data/raw/measurement01 --json \
    --plot data/processed/measurement01/acceleration.png \
    > data/processed/measurement01/inspection.json
```

Text and JSON reports include duration, finalization state, counts, average
per-stream rates over the observed sample span, maximum gaps, exact image times,
offsets from start, differences between images, save-callback latency, and missing
metadata warnings. Status 0 means no errors; status 1 means consistency errors;
status 2 means malformed/unreadable inputs or invalid options. Plot output must
be outside the raw session directory. The optional PNG is generated headlessly
and shows acceleration magnitude (including gravity) with image request markers.

Image request and callback times are not sensor exposure times. Their difference
measures request-to-save-callback latency, not an estimate of shutter delay.
Successful validation cannot establish exact visual/IMU synchronization.
