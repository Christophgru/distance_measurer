# P0.2 recorded prototype baseline

Established 2026-10-25 using the existing OpenCV/SciPy M4–M7 pipeline at
repository revision `3867d58` (plus the P0 documentation/benchmark wrapper).
The [reuse map](../architecture/prototype_reuse_map.md) identifies the underlying
implementations. No reconstruction implementation or dependency was changed.

## Dataset and fixed command

Use the existing local capture `data/raw/measurement01`, recorded session ID
`2026-10-21_135144_305_d3fb3516`. It contains five decoded 4096×3072 JPEGs in an
18.754-second recording, with 1,856 accelerometer, 1,855 gyro and 1,852 rotation
vector samples. The baseline uses **three images, recorded indices 0, 1, 2**, at
3.673505, 6.790371 and 8.851280 seconds after timeline start. The loader validates
the entire capture and sensor coverage around the images. Gyro is available for
later tasks; this visual baseline does not use it to estimate poses or scale.

With the existing editable Python package installed in `.venv`, run from the
repository root:

```bash
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
  .venv/bin/python python/scripts/benchmark_prototype_baseline.py \
  --output data/processed/measurement01/prototype_baseline_p0
```

The script fixes image selection and reconstruction settings, sets OpenCV to one
thread, reruns every stage and prints the summary JSON to stdout and stage times
to stderr. Use a **new output directory** for every comparison (e.g.
`prototype_baseline_after_p2`). Existing outputs and destinations under `data/raw`
are refused. The captured files remain local and ignored by Git; running elsewhere
requires a copy of the same session at that path, without modifying its contents.

Settings: longest image edge 1600 (1600×1200 working pixels), at most 6000 SIFT
features/image, Lowe ratio 0.75, mutual matching, F/E thresholds 1.5 working
pixels, RANSAC seed 0, pose minimum parallax 0.25°, triangulation maximum error
3 working pixels and minimum angle 1°, SVD triangulation. BA uses fixed K,
`soft_l1`, loss scale 1 pixel, at most 300 evaluations, a fixed first camera and
fixed first-baseline length. Intrinsics come from the recorded metadata.

Outputs under the chosen directory:

- `baseline.json`: counts, per-stage/total runtime, software versions, raw input
  SHA-256 hashes, validation warnings, triangulation and BA diagnostics.
- `m4/`: features, pair matches, masks, match/rectification images and `matches.json`.
- `m5/`: pair poses/masks, camera plot and `poses.json`.
- `m6/`: unrefined sparse `scene.json` and `triangulation.json`.
- `m7/`: refined sparse `scene.json` and `bundle_adjustment.json`.

`runtime_s.total` measures session loading/validation, initial source hashing,
matching, pose recovery, triangulation, BA and their artifact writes. Python
startup/imports, the final source-integrity check and summary serialization are
outside that timer. Matching includes loading/validation repeated by the existing
M4 pipeline, image resize/undistortion and debug exports; it is not detector-only
time. Compare on the same host/software/thread settings and note cache/load
effects. There is no feature or reconstruction cache reuse in this command.

## Measured result

Host: Intel Core i7-6820HQ @ 2.70 GHz, Linux x86_64; Python 3.12.15,
OpenCV 5.0.0, NumPy 2.5.4, SciPy 1.18.1. First full run:

| Metric | Result |
| --- | ---: |
| Selected / recorded images | 3 / 5 |
| Total runtime | 31.506 s |
| Loading and validation | 2.796 s |
| Matching, including diagnostics | 20.235 s |
| Essential matrices / poses | 1.522 s |
| Tracks / triangulation | 0.561 s |
| Bundle adjustment | 6.026 s |
| Verified F matches, summed over pairs | 318 |
| Essential / pose inlier matches, summed over pairs | 283 / 283 |
| Reconstructed cameras | 3 |
| Tracks / sparse points | 238 / 206 |
| Accepted two-view / three-view points | 197 / 9 |
| Accepted observations | 421 |
| Reprojection RMSE before / after BA | 0.864250 / 0.269012 working pixels |
| BA accepted / converged / evaluations | yes / yes / 47 |

The verified-match count is the sum of F-RANSAC **pair correspondences**, not
unique multi-image tracks. For pairs 0–1, 1–2, 0–2 the F inlier counts are
49, 169, 100 and the E/pose inlier counts are 46, 158, 79. All 206 points and
421 accepted observations survive BA. RMSE is
`sqrt(mean(du**2 + dv**2))` over accepted observations, in ideal working pixels.
The 32 rejected tracks remain in the scene with their high-error rejection reason.

A second full run to `prototype_baseline_p0_repeat` took **31.470 s** (loading
0.173 s, validation 2.488 s, matching 20.332 s, poses 1.547 s, triangulation
0.653 s, BA 5.955 s). Source hashes, per-pair counts, triangulation diagnostics
and the saved refined cameras, points and observations matched the first run
exactly. Existing-output and raw-output rejection were also checked.

Validation: **111 existing tests passed** across the matching, pose,
reconstruction and refinement integration suites and the relative-pose,
triangulation, pairwise-PnP and bundle-adjustment synthetic suites. Ruff lint
and formatting checks passed for the new benchmark script.

## Input identity and interpretation

The first run's `baseline.json` hashes all five images and the three recording
files and verifies they are unchanged after reconstruction. The selected inputs
have these SHA-256 values:

| Relative path in `measurement01` | SHA-256 |
| --- | --- |
| `session.json` | `ec7dc959401b00e904b9f7a5f8f905aef11d4ff0eb67a9246c2d5c9568d8bb69` |
| `events.jsonl` | `7ea7579e77e341000a96cdc55ad223b0c3ffdbf530fc4b510b68e3ba12aed02c` |
| `sensors.csv` | `8412a91c06f05369b1e71f812a9c5c81d8dde6458f61684b5af22a70b0aecc6f` |
| `images/image_000.jpg` | `8db01a89e8f1bc3678c0ccd2cd15c6d0a20ad48f5c7c2525479f5606ee1df21e` |
| `images/image_001.jpg` | `d4d5029db3b5718f38a8892475d2d749f33c268f9e1378f427ea648358c01fbf` |
| `images/image_002.jpg` | `c381bded666f011b1b198f427e4f0ac70c2a1bc3ed26d966c9b32a6f2efc329f` |

The capture passes validation with warnings: its image times are button requests,
and exact exposure timestamps are unavailable. Consequently it is suitable for
a repeatable visual benchmark, while gyro/image synchronization remains
approximate. Intrinsics are metadata-derived and approximate. Initial pairwise
poses have a 17° rotation-loop discrepancy; BA reports
`bundle_adjusted_unverified` and `arbitrary_visual_scale_unverified`. The lower
residual measures fit to retained correspondences, not physical pose accuracy.
Most points have only two-view support. No metric scale or ruler length is used.

Keep these limitations visible when comparing later stages. Compare runtime,
per-pair inliers, camera count, point/observation counts and reprojection errors
together; a lower RMSE after discarding difficult observations is not by itself
evidence of improved geometry.
