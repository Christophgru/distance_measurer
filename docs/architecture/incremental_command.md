# One-command incremental reconstruction (P8)

Run from the repository root with the Python environment installed:

```bash
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 .venv/bin/python \
  python/scripts/reconstruct_incremental.py \
  data/raw/2026-10-27_115100_304_3ca6165e \
  --image-indices 0 1 3 4 5 --initialize-motion
```

The default destination is
`data/processed/<raw-folder-name>/prototype_incremental_p8/`. Use `--output`
to choose a fresh directory. Existing results, raw directories and symlinks into
raw sessions are rejected. The explicitly selected image IDs remain unchanged;
omitting `--image-indices` processes every recorded image in order. Selecting
the five-image subset above is a declared dataset choice, not automatic recovery
of the full sequence.

`--initialize-motion` enables the existing bounded SIFT warp retry for widely
spaced stills. Without it tracking uses pure LK. Defaults retain the tested P2–P6
thresholds: 800 spatial points, 1600-pixel long edge, P3 best 200 candidates,
P4 maximum 100 initial landmarks, PnP at least eight inliers within three pixels,
confidence threshold 0.2, and at most two refinement rounds. P3's candidate pool
of 200 is distinct from P4's initial landmark cap. `--calibration` supplies the
existing calibration format. `--use-gyro-prior` optionally adds the existing
later-camera rotation gate; gyro analysis always runs for bootstrap selection.
The Python API also accepts the existing stage configuration dataclasses.

P9.3 adds [hybrid replay](hybrid_replay.md) to this same command. Its default
`--tracking-source auto` uses a tracking sidecar when present and retains the
still-only path otherwise. Hybrid output defaults to `prototype_hybrid_p9/`;
`--tracking-source stills` explicitly selects direct JPEG tracking. Hybrid
replay measures JPEG correspondences against both bracketing previews before
using the existing P3–P6 geometry stages.

## Composition and outputs

`vision/prototype_pipeline.py:reconstruct_incremental` calls existing functions:

1. `load_session`, `validate_session`, `summarize_session`;
2. `track_session`, including existing camera resolution/undistortion;
3. `analyze_track_parallax`;
4. `bootstrap_session`;
5. `extend_tracked_scene` with P6 refinement enabled;
6. existing scene loading, residual computation and export.

There is no new matching, pose estimation, triangulation or optimization solver.
No dependency was added. Stage folders `tracking/`, `parallax/`, `bootstrap/`
and `incremental/` retain their manifests, arrays, overlays, scenes and previews.
Root `scene.json` is the final complete or partial reconstruction;
`session_inspection.json` records capture diagnostics. `REPORT.md` and
`reconstruction.json` consolidate quality, costs, timings and failure reasons.
SHA-256 fingerprints cover all recorded JPEGs and session/events/sensors,
including images outside an explicit subset. Calibration is also fingerprinted.
Inputs are checked again before root scene export.

Exit 0 means **every selected camera registered**. Exit 1 means geometric
failure or partial registration; diagnostics and any partial scene are retained.
Exit 2 means invalid inputs/options or a stage/I/O error. Once processing starts,
an error report identifies its stage. Missing gyro never becomes identity
rotation. Successful BA on a seed alone cannot make a partial session complete.
The implementation remains forward-only: rejected cameras and cameras before
a later bootstrap pair are not revisited. It cannot relocalize across a complete
loss of tracks.

## Timing and quality interpretation

Stage wall times include existing validation, hashing, decoding, overlays and
stage export. Total time starts inside the pipeline after imports and stops
before the final consolidated report serialization. Nested operations are
reported separately without adding inclusive parent totals: spatial selection,
forward/reverse LK including retries, bounded initializer, gyro integration,
parallax ranking, bootstrap selection/RANSAC/pose recovery/triangulation,
incremental feature extraction, **all** PnP attempts including a failed normal
fit before fallback, local patch searches, epipolar search, track merging,
triangulation and local BA. Unattributed time includes other geometric
bookkeeping and I/O. The new PnP/growth instrumentation changes no geometry.

Per-pair reports include accepted track count/error, gyro availability/sample
coverage, residual parallax and selected count. Per-camera reports include final
PnP support, every attempt, confidence exclusions, matching/BA time and bounded
refinement decisions. Final RMSE is recomputed from the exported scene's actual
residuals. Triangulation statistics describe each landmark's best current ray
angle. BA may change angles after initial triangulation; weak points remain in
the scene but confidence gates exclude them from pose estimation.

Descriptor cost separates the initializer's bidirectional exhaustive upper bound
from actual guided corridor candidates, including reverse checks. Patch search
centers are reported separately and cannot be added to descriptor counts as if
they were equivalent operations. Different accepted observations, reduced camera
coverage and arbitrary visual scale prevent interpreting lower RMSE alone as
better pose accuracy. See the [P8 experiment](../experiments/p8_incremental.md)
for the fixed P0.2 comparison and real capture results.
