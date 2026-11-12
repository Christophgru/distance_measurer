# Saved-session compatibility rerun, 2026-11-12

All eight historical reference configurations reproduce their saved camera
geometry, points, tracks and observations exactly. The current runner can read
all seven saved recordings, spanning capture app versions 0.1.0–0.6.1. The
older still-only recordings need their original initializer/selection options
to reproduce the published results; default LK tracking has lower coverage.

The rerun covers **15 configurations**, including current P8/P9 reconstruction
and the original M4–M7 three-image baseline. No case returns software/input-error
exit 2. Fourteen scenes export successfully and pass the browser regression
checks. The remaining default-LK case reports unsupported geometry, exit 1,
and retains its diagnostics without creating a scene.

## Historical reference configurations

Still-only P8 rows below use `--initialize-motion`. Hybrid rows use unchanged
defaults. Selection is by recorded image ID. Counts and errors reproduce the
corresponding previous reports; camera matrices/centers, point coordinates,
tracks and observations are identical. A few diagnostic reductions differ only
at roughly 1e-15; all quality fields match within absolute tolerance 1e-10.

| Recording / method and selection | Cameras | Points | RMSE (working px) | Exit |
|---|---:|---:|---:|---:|
| `measurement01`, original M4–M7, IDs 0/1/2 | 3/3 | 206 | 0.269012 | 0 |
| `measurement01`, P8, IDs 0/1/2 | 2/3 | 106 | 0.337796 | 1 |
| October 25, all nine stills | 9/9 | 1,140 | 0.900490 | 0 |
| October 27 11:51, all nine stills | 2/9 | 180 | 0.501371 | 1 |
| October 27 11:51, IDs 0/1/3/4/5 | 5/5 | 696 | 1.064560 | 0 |
| October 27 15:33, all 29 JPEGs with previews | 24/29 | 3,632 | 1.056617 | 1 |
| October 28 15:27, all 16 JPEGs with previews | 16/16 | 2,604 | 1.159677 | 0 |
| October 28 22:56, all 15 JPEGs with previews | 15/15 | 1,872 | 1.063969 | 0 |

The 29-photo recording retains the same missing cameras **4, 5, 26, 27, 28**.
The last JPEG still lacks a following saved preview. The October 27 full
still-only recording retains cameras 0/1; its declared five-image subset
recovers all five. The incremental `measurement01` reference retains cameras
1/2 and cannot register camera 0 before its later bootstrap. These partial
results are the historical behavior, not new compatibility failures.

## Additional coverage on the older recordings

These full-recording/subset cases establish current P8 results where no matching
historical P8 report was available:

| Recording / initialized still selection | Cameras | Retained IDs | Points | RMSE (px) |
|---|---:|---|---:|---:|
| `measurement01`, all five | 2/5 | 1, 2 | 106 | 0.337796 |
| October 23, all eight | 4/8 | 1, 2, 3, 4 | 617 | 0.671646 |
| October 23, IDs 2/3/7 | 3/3 | 2, 3, 7 | 566 | 0.832697 |

The October 23 rows are current incremental reconstructions on that older
recording. They use different matching/refinement from the earlier visual-camera
validation experiment. A selected sequence can recover more useful views than
the full forward sequence when an intervening view breaks track continuity.

## Default still-only tracking

Without `--initialize-motion`, the current runner uses ordinary LK on still-only
recordings. Wide gaps and motion can leave too little geometric support:

| Recording, all photos | Cameras | Points | Outcome |
|---|---:|---:|---|
| `measurement01` | 0/5 | 0 | No supported bootstrap; no scene |
| October 23 | 3/8 | 604 | Partial scene |
| October 25 | 2/9 | 76 | Partial scene |
| October 27 11:51 | 2/9 | 192 | Partial scene |

All four return exit 1 and retain diagnostics. The three partial scenes export
and render normally. Full camera recovery still depends on the recording and
chosen method; successful loading or a viewer export is not full reconstruction
or physical measurement acceptance.

## Commands for older data

Run from the repository root, with the existing Python environment. The runner
creates a fresh timestamped output folder, prints its reports and serves the UI
when `--serve` is supplied. For the nine-image reference:

```bash
.venv/bin/python python/scripts/process_session.py \
  data/raw/2026-10-25_182438_864_e3177490 --initialize-motion --serve
```

For the supported October 27 subset:

```bash
.venv/bin/python python/scripts/process_session.py \
  data/raw/2026-10-27_115100_304_3ca6165e \
  --initialize-motion --image-indices 0 1 3 4 5 --serve
```

For the October 23 subset:

```bash
.venv/bin/python python/scripts/process_session.py \
  data/raw/2026-10-23_091847_034_be3e7ada \
  --initialize-motion --image-indices 2 3 7 --serve
```

For the preserved original three-image method, choose a fresh output path:

```bash
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 .venv/bin/python \
  python/scripts/benchmark_prototype_baseline.py \
  --output data/processed/measurement01/legacy_rerun_new
```

See the [dataset walkthrough](../architecture/run_dataset.md) for the current
runner, individual stages and browser exports, and the
[original baseline](prototype_baseline.md) for M4–M7 details.

## Integrity and verification

- All seven recordings pass structural inspection. Existing timestamp,
  dropped-sample and camera-setting warnings remain visible.
- Before/after inventories confirm **all 1,023 raw-file hashes and file paths
  are unchanged**. Historical input hashes also match each reference run.
- All eight historical geometry comparisons pass, with **zero numerical change**
  in saved K/R/C and point positions and identical tracks/observations.
- All **14 generated viewers** pass offline Chromium GPU picking, stable-ID
  search, orbit/pan/zoom, filters, camera views and mobile-layout checks, including
  the original M4–M7 scene and all exported partial scenes.
- Thresholds, selected historical image IDs, resolution, correspondence budgets,
  PnP gates and refinement defaults were preserved. No algorithm changes were
  needed for this rerun.

The three-worker batch records timings, but concurrent execution makes these
runs unsuitable for a controlled speed comparison. All captures and results
remain in arbitrary visual units with their existing quality limitations.

Full local evidence is in the Git-ignored directory
`data/processed/session_regression_20261112_141935/`: `batch.json` records every
exact command and result; `comparisons.json`, `browser_results.json` and
`verification.json` record the checks. The two `raw_sha256_*.json` files preserve
the complete inventories. Each case has fresh reports, logs and viewer output.
