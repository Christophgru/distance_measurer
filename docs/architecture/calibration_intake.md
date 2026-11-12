# Calibration recording intake

`tools/calibration/intake_capture.py` prepares matching-settings calibration for
the offline measurement prototype. It uses the existing OpenCV corner detector
and our planar camera-calibration implementation, with no new dependency or APK.
It does not start reconstruction or establish physical measurement accuracy.

```sh
.venv/bin/python tools/calibration/intake_capture.py \
  data/raw/CALIBRATION_SESSION \
  --measurement-session data/raw/MEASUREMENT_SESSION \
  --output data/processed/CALIBRATION_SESSION/intake \
  --columns 9 --rows 6 --square-size-m 0.025
```

Replace the two session names and use the actual printed square size. The output
must be a fresh directory outside all raw input directories. Calibration alone
can be checked by omitting `--measurement-session`.

## Independent checks

The tool first validates capture structure and recorded camera settings. It
rejects changes of lens, dimensions, focal length, focus, zoom or crop within
the calibration recording. Missing settings remain unknown.

Board detection runs at up to 1600 pixels on the longest axis, without EXIF
rotation. Pixel-center-aware mapping converts detections back to native pixels,
where subpixel corner refinement runs. The saved profile therefore describes
the original stored JPEG dimensions and pixel frame.

At least six complete detected boards are needed for this diagnostic. The
recommended physical capture is still 15–25 sharp, differently tilted views.
Every third detected view, in image-ID order, is reserved before fitting K or
distortion. No search chooses views with favorable residuals. Degenerate,
repeated views must pass the existing calibration rank/conic checks.

For each reserved view, K and distortion remain frozen. A six-parameter board
pose fits alternating checkerboard corners; the remaining corners test that
pose and camera model. Reserved corners cannot improve the fitted profile.
Initial diagnostic thresholds are at most 2 native pixels RMSE and 3 native
pixels p95 on the tested corners, plus a converged training calibration with
training RMSE at most 2 native pixels. These are prototype pixel checks, not a
metric accuracy specification or a guarantee outside the observed image region.

## Results

- `intake.json`: detections/native coordinates, train/reserved IDs, tested-corner
  errors, capture-setting comparisons and raw-file SHA-256 hashes.
- `REPORT.md`: concise results and unresolved blockers.
- `candidate_camera.yaml`: saved only if the calibration audit passes; fitted
  exclusively on the training views, with the split recorded in its provenance.

With a measurement recording, the tool compares device metadata, image size,
logical/physical camera, focal length, focus, zoom and crop. Unknown values do
not count as matches. A passing calibration, valid measurement recording and
matching recorded settings produce `ready_for_calibrated_reconstruction=true`
and a structured `next_commands` entry for the existing reconstruction command.
This means eligible input for a reconstruction attempt; supported 3D geometry
and physical accuracy still need their own checks. Nothing executes that entry
automatically, and recorded settings cannot prove identical physical optics.

The current app holds observed focus/crop during a recording; see
[capture stability](capture_stability.md). For the two recordings, begin from a
similar camera-to-target distance and let the preview settle before recording.
Focus differences remain visible in intake rather than being silently ignored.

The tool checks the full input inventory again before publishing. Raw files are
never rewritten. Exit 0 means the calibration audit passes and, when requested,
measurement settings match. Exit 1 preserves diagnostic failure/unknown results.
Exit 2 indicates malformed input, unsafe output placement or another input error.
`metric_measurement_accepted` remains false in every intake result.

The old 22:56 desk recording correctly produces 0/15 boards and no profile under
`data/processed/2026-10-28_225648_021_a0d054e8/calibration_intake/`. It has not been
treated as a physical calibration capture. Tests use generated board observations
and JPEGs, including EXIF-6 native-coordinate mapping, deliberately corrupted
reserved corners, unknown/mismatched settings, input protection and a matching
two-session path that prepares a command without executing reconstruction.
