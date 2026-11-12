# Run a recording and open the web UI

Run all commands from the repository root in Bash, using Python 3.12 or newer.
The normal workflow needs Python and a WebGL 2 browser. The renderer is already
bundled in the repository; Node/npm and an Android rebuild are not needed.

## 1. Install the Python environment once

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -e './python[dev]'
```

For an existing environment, skip creation and run the install command if the
checkout or dependencies changed. On Windows, use `.venv/Scripts/python.exe`
and adapt the shell variable syntax below.

## Process a copied recording in one command

Once the complete recording is under `data/raw/`, run:

```bash
.venv/bin/python python/scripts/process_session.py data/raw/SESSION_ID --serve
```

Replace `SESSION_ID` with its folder name. This inspects the recording, runs the
existing reconstruction pipeline, exports the 3D viewer and starts a local HTTP
server. Open **<http://localhost:8765/>**. Leave the terminal running; **Ctrl+C**
stops serving. Omit `--serve` to finish after export and print a server command
for later use. `--port 8766` chooses a different port.

Each invocation creates a fresh timestamped directory under
`data/processed/SESSION_ID/process_<UTC timestamp>/`. `--output PATH` chooses a
specific fresh run directory. A run contains:

```text
REPORT.md                  overall result and links to generated pages
workflow.json              stage commands, exit codes and timing
*.log                      captured stage output, also printed during processing
inspection.json            recording validation and warnings
reconstruction/            existing pipeline reports, diagnostics and scene
web/index.html             3D viewer, with embedded photo previews
```

The command retains the current geometry gates. A partial scene is exported
with its missing-camera diagnostics and exit code **1**. If no scene is
supported, reports are kept and no viewer is created. Inspection consistency
errors also stop the workflow with exit 1. Malformed input, stage/export or
server errors return **2**, preserving artifacts already written. Exit **0**
means all selected JPEG cameras registered and requested exports succeeded;
it does not establish metric accuracy. Stopping a successful server with Ctrl+C
retains the processing exit code; interrupting processing returns 130.

Useful options are forwarded to the existing reconstruction command:
`--calibration FILE`, `--image-indices 0 1 3`, `--tracking-source auto|stills|hybrid`,
`--initialize-motion`, `--max-image-size` and `--max-points`. A supplied calibration
is passed to all photo exports as well. The runner uses one BLAS/OpenMP thread
unless those environment variables are explicitly set. No algorithm is replaced.

To also create blank endpoint annotations and the photo review page, supply the
physically known reference length in meters:

```bash
.venv/bin/python python/scripts/process_session.py data/raw/SESSION_ID \
  --measurement-reference-m 0.1 --serve
```

Here **0.1 means a known 10 cm reference**; use the length you actually measured.
The run adds `measurement/annotations.json`, `measurement/measurements.json` and
`web/measurement/index.html`, available at **<http://localhost:8765/measurement/>**.
No endpoint positions are invented: the page starts with missing marks. Review
the selected photos and reference, save edited annotations using **Download**,
then evaluate them with `measure_scene.py` as described in the
[measurement workflow](measurement_prototype.md#start-annotations-for-a-new-scene).

The manual commands below remain useful for running or repeating individual
stages. Copy a new recording with step 2 first, then use either this runner or
the remaining individual steps.

## 2. Copy and inspect the complete recording

Choose the directory name shown by the phone and a new name for this processing
attempt. Keep these variables in the same terminal for the remaining commands:

```bash
SESSION_ID=2026-10-28_152754_143_84510fbf
RUN_NAME=run01
SESSION_DIR="data/raw/$SESSION_ID"
RECON_DIR="data/processed/$SESSION_ID/$RUN_NAME"
WEB_DIR="data/processed/$SESSION_ID/web_$RUN_NAME"
```

The ID above is an existing reference recording. Replace it to process a new
dataset. Copy the entire ended session, preserving its files and subdirectories:

```bash
mkdir -p data/raw
adb shell ls /sdcard/Android/data/org.distancemeasuring.capture/files/sessions
adb pull "/sdcard/Android/data/org.distancemeasuring.capture/files/sessions/$SESSION_ID" data/raw/
```

Skip ADB if the session is already copied. A recording contains `session.json`,
`events.jsonl`, `sensors.csv` and `images/`. Hybrid recordings also contain
`tracking.json`, `tracking/events.jsonl` and the referenced preview PNGs. Keep
these together; do not rename or edit the individual raw files. Restricted
ADB access and capture instructions are covered in the
[Android guide](../../android/README.md#copy-a-session-manually).

```bash
.venv/bin/python python/scripts/inspect_session.py "$SESSION_DIR"
```

Exit 0 means the recording passes structural/timing inspection; read its warnings
as well. Exit 1 means consistency errors; exit 2 means malformed input or options.
Resolve inspection errors before reconstruction. This check does not establish
camera calibration or physical measurement accuracy.

## 3. Reconstruct cameras and sparse points

```bash
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 .venv/bin/python \
  python/scripts/reconstruct_incremental.py "$SESSION_DIR" \
  --output "$RECON_DIR"
```

The command selects hybrid preview replay when `tracking.json` exists, otherwise
still-only tracking. Do not force `--tracking-source stills` to hide a broken
hybrid recording. For widely spaced, still-only photos, the optional
`--initialize-motion` enables the existing SIFT-assisted initializer.
`--image-indices 0 1 3` selects recorded image IDs, not list positions; omit it
to assess all photos. Inputs need overlapping, textured views with useful
translation; unsupported cameras remain unregistered.

If a measured calibration matches this recording's camera settings, add
`--calibration /path/to/candidate_camera.yaml`. Use the same profile for the
viewer export and measurement review. The
[calibration intake](calibration_intake.md) checks a new calibration recording
and its compatibility with the measurement session. Without a measured profile,
the reconstruction retains the recorded approximate-camera assumptions.

`RECON_DIR` must be a fresh directory outside raw data. For another attempt,
change `RUN_NAME` and update `RECON_DIR` and `WEB_DIR`; do not delete a previous
run just to reuse its path. Typical real reference runs have taken 9–12 minutes;
runtime depends on image support and the machine.

| Exit | Meaning | Next action |
|---|---|---|
| 0 | All selected JPEG cameras registered | Read the report, then export the scene |
| 1 | Partial reconstruction or insufficient geometry | Read failures; export only if `scene.json` exists, keeping its limitations |
| 2 | Invalid input or a processing error | Fix the reported error before continuing |

Read `$RECON_DIR/REPORT.md` and `$RECON_DIR/reconstruction.json`. A completed
or partial scene is normally `$RECON_DIR/scene.json`. Scene storage switches
large point lists to `scene.bson`; use that actual filename if present.
Reprojection error and registered camera counts are geometry diagnostics, not
metric accuracy guarantees. This command does not generate the web UI.

## 4. Create the 3D web UI

```bash
.venv/bin/python python/scripts/export_scene_web.py \
  "$RECON_DIR/scene.json" \
  --session "$SESSION_DIR" \
  --output "$WEB_DIR/index.html"
```

Add the same `--calibration FILE` if reconstruction used a measured profile.
`--session` embeds photo previews and samples point colors; omit it for a
geometry-only export. Export exit 0 means HTML was written; exit 2 reports an
input/export error. It does not rerun reconstruction or change its scale.

The viewer output must be outside raw data and outside the reconstruction's
source directory. The separate `web_$RUN_NAME` folder above satisfies this.
Exporting again to the same HTML path replaces that generated file.

## 5. Start the web UI

```bash
.venv/bin/python -m http.server 8765 --bind 127.0.0.1 --directory "$WEB_DIR"
```

Leave that terminal running and open **<http://localhost:8765/>** in a browser
on the same computer. Stop the server with **Ctrl+C**. If port 8765 is occupied,
choose another port and use it in the URL. If using a remote development
workspace, forward that port and open its forwarded URL.

For this standalone sparse export, opening `index.html` directly also works.
The separate older dense-reference exports require HTTP because they fetch BSON
sidecars; keep their entire export folder together. Neither viewer has an API
backend or requires MongoDB.

Orbit with left-drag, pan with right-drag/Shift-drag and zoom with the wheel.
Select a camera or point to inspect it. Coordinates are reconstruction units;
the viewer does not apply the ruler's metric reference. See
[viewer controls](scene_viewer.md#interactive-browser-viewer).

## Optional: create the endpoint measurement page

The 3D viewer and the full-resolution photo annotation page are separate exports.
The annotation page requires an annotation JSON bound to this exact scene hash;
an old recording's annotations cannot be reused on a new scene. Create the
template, initial diagnostic report and review HTML together:

```bash
MEASURE_DIR="data/processed/$SESSION_ID/measurement_$RUN_NAME"
.venv/bin/python python/scripts/init_measurements.py "$RECON_DIR/scene.json" \
  --session "$SESSION_DIR" \
  --reference-length-m 0.1 \
  --output "$MEASURE_DIR"
```

Use the actual known reference length; 0.1 is a 10 cm example. `MEASURE_DIR`
must be fresh. Supply the original `--calibration FILE` when applicable. Open
`$MEASURE_DIR/index.html` directly, or serve `MEASURE_DIR` on another port, such
as 8766. Empty annotations produce
explicit missing-observation results while allowing marks to be placed in the UI.
Save the edited annotation JSON using **Download**, then evaluate the downloaded
file with `measure_scene.py`; see the
[measurement setup](measurement_prototype.md#start-annotations-for-a-new-scene).
Browser edits alone do not recompute 3D measurements. Export success does not mean the metric
checks passed; inspect `blocking_reasons` in `measurements.json`.

## Troubleshooting

| Symptom | Check |
|---|---|
| `No module named distance_measuring` | Install the editable Python package using step 1 and invoke the same environment |
| `Output already exists` | Choose a fresh reconstruction run directory |
| No `scene.json` after reconstruction | Read `REPORT.md`/`reconstruction.json`; unsupported geometry may produce no scene |
| Photo calibration differs from scene K | Pass the original calibration, exact raw session and correct scene |
| HTTP 404 or directory listing | Export HTML first and serve the folder containing `index.html` |
| Blank/error 3D view | Use a WebGL 2 browser and inspect its displayed error; a successful HTTP response alone does not validate rendering |
| Changed `app.js` but UI is unchanged | Rebuild the renderer and re-export the HTML; see the [renderer development guide](../../tools/scene_viewer/README.md) |

## Verified older recordings

The [saved-session compatibility rerun](../experiments/session_regression_20261112.md)
covers all seven local recordings, historical settings and current defaults.
For older still-only recordings, use `--initialize-motion` and the documented
photo selection to reproduce the corresponding reference. Default LK tracking
can have much lower coverage on widely spaced photos. Known partial runs remain
partial; the report lists their exact camera support and usable subsets.
