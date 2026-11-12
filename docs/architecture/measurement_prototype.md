# Offline endpoint measurement prototype

P10.3 starts with frozen offline visual cameras, before streaming optimization.
`python/scripts/measure_scene.py` triangulates named corresponding endpoints,
uses one known interval for scale and reports separate held-out lengths. It
reuses the existing triangulator and adds no application or Python dependency.

The optional research helper `vision.planar_measurement` tests measurements on
an explicitly fitted flat surface. It is separate from this default command:
conditional repeatability improved, but unused-view surface checks failed.
See the [stability experiment](../experiments/p10_measurement_stability.md) for
its independent gates, background-only refinement trial and next capture kit.

The [calibration intake command](calibration_intake.md) is ready for those new
recordings. It checks native board corners, reserved views/corners and matching
capture settings before preparing a calibrated reconstruction command. The
existing desk recording has no calibration boards and produces no profile.

The optional standalone HTML page embeds full-resolution source photos for
review. Open it directly in a browser without a server or network. Select a
photo and point, use **Zoom to mark**, then click or nudge the same physical mark.
Click **Confirm this mark** only after inspection; any edit clears confirmation.
Drag to pan, scroll to zoom, and use arrows for 0.25 working-pixel nudges (Shift:
1 pixel). Download annotations before closing the tab. Load JSON to resume.
Edited results are hidden until Python evaluates the downloaded annotations.

## Run

From the repository root, using the existing virtual environment:

```sh
.venv/bin/python python/scripts/measure_scene.py \
  data/processed/<session>/experimental_reconstruction/scene.json \
  data/processed/<session>/measurement_prototype/annotations.json \
  --output data/processed/<session>/measurement_prototype/measurements.json \
  --review-html data/processed/<session>/measurement_prototype/index.html \
  --session data/raw/<session>
```

`--review-html` is optional; photo export needs `--session`. Calibrated cameras
also need the original `--calibration` profile. Outputs cannot overwrite the
input annotations or reside in raw, the declared session or source scene
directories. Exit 0 means a diagnostic report was written, **not accuracy
acceptance**; invalid input or export failure exits 2. `--sensitivity-trials`
defaults to 200 (minimum 20).

The October 28 22:56 recording has a ready-to-open example under
`data/processed/2026-10-28_225648_021_a0d054e8/measurement_prototype/`.
Its report explicitly fails the prototype checks. Its approximate seed and
later-view manual candidates are retained separately, with no confirmed marks.

## Start annotations for a new scene

Use the variables from the [dataset walkthrough](run_dataset.md). The following
creates a scene-bound template with four named endpoints, an initial report and
the editable photo page. It requires a fresh output directory:

```bash
MEASURE_DIR="data/processed/$SESSION_ID/measurement_$RUN_NAME"
.venv/bin/python python/scripts/init_measurements.py "$RECON_DIR/scene.json" \
  --session "$SESSION_DIR" --reference-length-m 0.1 --output "$MEASURE_DIR"
```

Supply the physically known reference length in meters; **0.1 is a 10 cm example**.
Supply the original `--calibration FILE` for calibrated scenes. Open the generated
`index.html` directly, or serve that folder. `--review-html PATH` chooses a
separate, unused HTML destination. Select view groups where the same endpoints
are readable, before comparing length results. The
default groups use the first two sorted camera IDs, then the first and third
when available. Override them with repeated `--view-group 0 1 --view-group 0 14`
using actual retained camera IDs. The first group fixes scale. The generated
second group shares a camera, so it is not an independent recording.
Add `expected_length_m` to the object measurement
only when its length was measured separately; an unknown length stays absent.
`localization_radius_px` is an assumed working-pixel perturbation radius, not
measured accuracy. Adjust it to the annotation uncertainty being investigated.

The empty points intentionally report missing observations while the page lets
you select each point, click the same physical
endpoint in each chosen photo and confirm reviewed marks. Download the edited
JSON, then evaluate that downloaded file (replace the annotation path below):

```bash
.venv/bin/python python/scripts/measure_scene.py \
  "$RECON_DIR/scene.json" /path/to/measurement_annotations.json \
  --session "$SESSION_DIR" \
  --output "$MEASURE_DIR/measurements.json" \
  --review-html "$MEASURE_DIR/index.html"
```

Use the original `--calibration FILE` when applicable. The browser does not write
to the original JSON or recompute the geometry. With only two cameras, repeatability remains
unavailable. Calibration and physical accuracy checks remain separate.

## Annotation contract

JSON schema version 1 contains:

| Field | Meaning |
|---|---|
| `session_id`, `scene_sha256` | Exact recording and source scene file binding |
| `coordinate_frame` | `undistorted_working_pixels_ignore_exif` |
| `image_sizes` | All scene camera IDs as strings mapped to `[width, height]` |
| `points` | Named objects with `observations`: image-ID objects containing `pixel: [u, v]`, `reviewed: boolean` and optional method/provenance |
| `reference` | Two named `endpoints` and positive `length_m` |
| `measurements` | Unique `id`, two named `endpoints` and optional positive `expected_length_m` |
| `view_groups` | Predeclared lists of at least two distinct camera IDs; first group sets scale |
| `localization_radius_px` | Nonnegative assumed perturbation radius in working pixels |

Missing observations are absent keys, never `(0, 0)` placeholders. All supplied
pixels must be finite and inside their working images. Known held-out intervals
cannot share scale-setting endpoints. To measure another object, add named
endpoint objects and a measurement interval to the JSON, then load/re-export it.
An unknown object length omits `expected_length_m`; a known validation interval
is still required to pass the prototype checks.

## Geometry and scale

Every group uses all its declared views. Missing or inconsistent observations
are reported rather than dropped. The triangulator keeps its existing positive
depth, 3-pixel reprojection and 1-degree minimum angle gates. There is no pose,
intrinsic or endpoint fit against known lengths and no search for a group with
the best metric agreement.

The **first predeclared group** supplies the only nominal meters-per-unit scale.
Every other group uses that same scale, including remeasurement of the reference
interval itself. Local per-group scale estimates are diagnostic only. If the
primary group fails, other groups cannot silently replace it. The source scene
and any existing viewer remain in their original reconstruction units.

Repeatability reports the range divided by the median length across distinct
supported camera groups. Shared camera IDs are explicit: overlapping groups
are conditional repeat checks, not independent recordings. All declared groups
must have usable marks for the prototype checks to pass.

## Sensitivity and acceptance

Seeded trials perturb each point/view annotation once by independent uniform
noise within the declared working-pixel radius. Overlapping groups share each
trial's perturbation, and all groups share the trial's scale from the primary
reference. The report exposes geometry survival and 5th/50th/95th percentiles.
These are **illustrative localization sensitivity, not confidence intervals**.
They exclude camera/calibration uncertainty, systematic annotation error and
incorrect physical mark identification.

`prototype_checks_passed` requires reviewed observations, visually validated
cameras, all declared geometry, a held-out known length, repeatability, reference
remeasurement and held-out errors within the explicit 10% diagnostic thresholds.
At least 95% of sensitivity trials must retain geometry; the 5th/95th lengths
must remain within 10% of the nominal length. These initial thresholds are
prototype diagnostics, not a validated product accuracy specification.

`metric_measurement_accepted` remains false throughout this prototype. Formal
physical validation and the guarded `tools/validation/validate_ruler_reference.py`
workflow remain separate. Its validated-camera requirement is unchanged.

The report records input SHA-256 hashes and verifies them again before export.
Photo export checks session identity, original image dimensions and K agreement.
It undistorts using the original calibration, keeps native resolution and
applies EXIF 1/3/6/8 display rotation. Explicit affines include both OpenCV's
resize pixel-center convention and the canvas half-pixel offset, so full-detail
review coordinates map back to the stored working camera frame.

## Verification

Synthetic/integration tests cover known metric lengths, fixed-scale drift,
failed primary reference, inconsistent/missing views, circular validation,
localization sensitivity, scene binding, raw/input protection and pixel mapping.
The actual exported recording also has an offline Chromium interaction check:

```sh
node tools/scene_viewer/measurement_smoke.mjs /absolute/path/to/index.html
```

This uses the existing development-only Playwright installation. It tests
canvas clicks, rotation-aware nudges, review invalidation, stale-result hiding,
JSON round trips, invalid imports, navigation and mobile layout. Its temporary
UI edits are never promoted into physical ruler annotations.
