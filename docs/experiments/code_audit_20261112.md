# Dataset workflow and code audit, 2026-11-12

The current entry point is [Run a recording and open the web UI](../architecture/run_dataset.md).
It replaces the need to assemble commands from older milestone examples. It
covers installation, complete-session import, inspection, hybrid/still selection,
fresh reconstruction output, HTML export, local serving, calibration and the
separate endpoint annotation page. The top-level, Python, Android and renderer
guides link to it. Older dense and three-image examples are labeled historical.

## Scope

Reviewed the active Python command path: session inspection, P9 hybrid replay
and P2–P6 orchestration, scene loading, browser export, measurement evaluation,
annotation-page generation and browser checks. Reviewed the Android preview
processor and bounded frame writer for context; no Android code was changed.
The numerical acceptance gates, source scenes and raw recordings are retained.
Existing still-only, dense-reference and desktop workflows remain supported.

This is a maintenance audit of the current workflow, not a new assessment of
physical camera or measurement accuracy. Full numerical-method review and
on-device lifecycle/native execution are separate work.

## Issues fixed

1. **Output paths could replace inputs.** Browser export and measurement output
   did not consistently exclude calibration files or hard-linked input aliases.
   Measurement JSON and review HTML could also alias each other. Both exporters
   now check file identity as well as resolved paths, including the photo and
   metadata inputs discovered during export. Rejections occur before publication.
2. **Malformed annotations could escape as tracebacks.** Non-object JSON,
   reference objects and measurement entries were dereferenced before validating
   their shape. Validation now raises explicit input errors, checks image-size
   mappings and rejects boolean schema versions. The CLI retains exit 2 for
   malformed input. OpenCV failures also follow the normal CLI error path.
3. **Some provenance snapshots happened too late.** Session metadata and
   calibration hashes were recorded after photo preparation, allowing an input
   change during that work to become the recorded baseline. They are now captured
   before preparation and rechecked before publishing. Browser photos are decoded
   from the same bytes that were hashed; measurement scenes are likewise parsed
   from their hashed bytes. Exported HTML and reports use explicit UTF-8.
4. **CLI help initialized plotting unnecessarily.** Camera and desktop-preview
   modules imported Matplotlib at module import time. Plotting imports now occur
   only when a plot is requested. Reconstruction help and input-only commands
   avoid plotting configuration/cache setup. Reconstruction help/log wording also
   covers the current preview/JPEG path rather than calling every run P8.

Regression tests cover output/calibration/source collisions, symbolic and hard
links, aliases between outputs, raw-photo aliases, inputs changing during photo
preparation, malformed annotation objects and existing measurement geometry.
No reconstruction threshold, scale policy or metric acceptance rule was relaxed.

## Verification

- Export/measurement/annotation checks: **58 tests pass**.
- Full Python suite: **977 tests pass** (425.41 s). Plotting/orchestration
  follow-up: **42 tests pass**.
- Ruff lint/format and `git diff --check` pass.
- All **95 local documentation file/heading links** resolve. The four main
  workflow commands provide clean `--help` output with the documented options.
- The documented empty-annotation generator was executed on the existing October
  28 22:56 scene, then both browser pages were generated with the current commands.
- All **247 raw hashes** still match the published reconstruction. Evaluating
  the existing manual ruler annotations reproduces the published measurement
  report exactly, excluding input-path provenance.
- Offline Chromium checks pass for the 3D viewer: GPU picking, stable IDs,
  orbit/pan/zoom, filters, camera views, mobile layout and one cloud draw call.
  The separate synthetic 100k cloud check also passes.
- The full-resolution annotation page passes click/nudge coordinate round trips,
  confirmation invalidation, JSON download/import, wrong-scene rejection,
  missing-view editing, pan/zoom and mobile layout checks.
- A server started with `python -m http.server` successfully serves both new
  exports. Browser checks verify the 1,872-point/15-camera scene and placing and
  confirming a first mark in the empty annotation template, without page errors.
  The test server is stopped afterward.

Chromium and its missing host libraries were downloaded/extracted into temporary
directories for these browser checks. No browser binary, new project dependency
or system package change is part of this cleanup.

Python/static checks, run from the repository root:

```bash
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 .venv/bin/python -m pytest -q -p no:cacheprovider python/tests
.venv/bin/python -m ruff check --no-cache .
.venv/bin/python -m ruff format --check --no-cache .
git diff --check
```

Browser regression commands and setup are in the
[renderer guide](../../tools/scene_viewer/README.md). Their temporary edits do not
replace the saved physical annotations.

The local export/check artifacts are under
`data/processed/2026-10-28_225648_021_a0d054e8/docs_cleanup_z2nhsn9b/` (Git-ignored).
The 3D export retains **1,872 points and 15 cameras**. Blank annotations report
missing marks and unavailable scale; they do not create artificial measurements.

No new phone recording is needed for this maintenance work. Measured calibration,
independent physical lengths and outstanding native/UI checks still require the
phone; the roughly 4% planar ruler example remains experimental.

## Follow-up: one-command workflow

`python/scripts/process_session.py SESSION --serve` now composes the existing
inspection, reconstruction and export commands, creates a fresh timestamped run
folder and optionally serves the result on localhost. `workflow.json` and
`REPORT.md` retain stage results; per-stage logs retain console output. Partial
geometry remains exit 1 and can still be viewed; invalid input/stage errors
remain exit 2. Ctrl+C stops serving without changing the completed processing
status. See the [current command walkthrough](../architecture/run_dataset.md#process-a-copied-recording-in-one-command).

`--measurement-reference-m LENGTH` also prepares blank endpoint annotations and
the review page. The separate `init_measurements.py` command supports existing
scenes, explicit view groups and calibration. Both reuse the existing annotation
validator, evaluator and photo exporter. No physical endpoints or metric scale
are inferred during setup. The previous Python template snippet is replaced by
this command in the measurement guide.

Verification of this follow-up:

- **996 Python tests pass**, including **13 focused runner checks** for complete,
  partial, unsupported and invalid captures, file aliases, paths containing spaces,
  calibration forwarding, logs, HTTP serving and stopping with Ctrl+C.
- Ruff lint/format and diff checks pass. All **97** local documentation file and
  heading links in the changed guides resolve.
- The existing October 28 15:27 recording completes all four workflow stages
  with exit 0 in **678.52 s**, recovering **16 cameras, 2,604 landmarks and 7,703
  observations**, with **1.15967688 px RMSE**. Camera K/R/C and all point positions
  match the published reference within absolute tolerance 1e-10. All **263 raw
  input hashes** are unchanged.
- The generated 3D page passes the offline Chromium regression checks, including
  GPU picking, navigation, filtering, camera views and mobile layout. The blank
  measurement page loads over HTTP and supports placing/confirming a first mark
  without page errors. Its report retains missing observations and unavailable
  scale until real endpoints are supplied.

Real-recording artifacts, the raw/geometry comparison and the HTTP browser check
are retained under the Git-ignored local directory
`data/processed/2026-10-28_152754_143_84510fbf/process_20261112_140130_761343/`.
