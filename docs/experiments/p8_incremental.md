# P8: one-command reconstruction and fixed-baseline comparison

2026-10-27. **The composed prototype works on the supported captures, but this
experiment does not demonstrate a speedup or preserved pose quality on the
fixed P0.2 baseline.** Lower descriptor cost alone is not sufficient. No
geometric thresholds were relaxed to improve the comparison.

## Fixed dataset and commands

Both runs use `data/raw/measurement01`, images **0/1/2**, identical raw SHA-256
fingerprints, identical metadata-derived intrinsics, and a 1600-pixel long edge.
Commands ran sequentially with OpenCV, OpenBLAS and OMP set to one thread:

```bash
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
.venv/bin/python python/scripts/benchmark_prototype_baseline.py \
  --output data/processed/measurement01/p8_baseline
.venv/bin/python python/scripts/reconstruct_incremental.py data/raw/measurement01 \
  --image-indices 0 1 2 --initialize-motion \
  --output data/processed/measurement01/prototype_incremental_p8
```

The preserved P0.2 command uses global mutual SIFT matching with up to 6000
features per image and global BA. P8 composes the existing P2–P6 stages: 800
spatial points, optional 640-pixel/600-feature SIFT warp initialization, P3 pool
of 200 tracks, P4 cap of 100 initial landmarks, 1200-feature guided growth,
PnP at least eight inliers within three pixels, confidence 0.2, local BA and at
most two refinement rounds. It does not register backwards from a later seed.

| Measurement | Fresh P0.2 baseline | P8 incremental |
|---|---:|---:|
| Pipeline time (s) | 33.185 | 34.474 |
| Cameras recovered | 3/3: 0, 1, 2 | 2/3: 1, 2 |
| Landmarks | 206 | 106 |
| Accepted landmark observations | 421 | 212 |
| Final reprojection RMSE (working px) | 0.269012 | 0.337796 |
| Median best triangulation angle (degrees) | 19.676 | 21.303 |
| Global bidirectional descriptor-distance upper bound | 54,398,912 | 1,440,000 |
| Actual guided descriptor candidates, including reverse checks | — | 4,886 |
| Local patch centers evaluated | — | 0 |

P8 bootstraps from 1→2 because 0→1 does not pass its geometric preselection.
Image 0 remains explicitly unregistered; the command exits **1**, retains its
partial scene and states why. The old baseline recovers all three cameras.
Relative pose for the common pair 1→2 differs by **2.342° in rotation** and
**5.691° in translation direction**, measured independently of world gauge and
visual scale. There is no pose ground truth. RMSE and ray-angle statistics use
different accepted points/observations and camera coverage; they cannot establish
improved accuracy. The reduced descriptor budget also accompanies reduced work
and coverage, so it is not an equivalent-quality performance win.

The descriptor upper bounds count logical exhaustive BF distance candidates,
not instrumented kernel operations. P8's guided counts measure the candidates
actually supplied to local descriptor searches. Patch centers are a separate
unit. Raw input/selection/intrinsics equality and the gauge-invariant pose
comparison are recorded in [comparison.json](../../data/processed/p8_benchmark/comparison.json).

Timings start after imports. P8 includes root final hash verification and scene
export (0.378 s); P0.2's preserved timer stops before its final hash check. These
are single runs on one host, so the small runtime difference is not a general
performance claim. About 25 s of P8's baseline run lies outside the measured
algorithm components: repeated validation/decoding, hashes, geometric
bookkeeping, visualization and export remain substantial costs.

## Real capture acceptance

All captures below use the new command with `--initialize-motion`. The five-image
subset is explicitly selected with `--image-indices 0 1 3 4 5`; all other image
selections are complete sessions. Exact commands and full process wall times
are in [runs.json](../../data/processed/p8_benchmark/runs.json).

| Session / selection | Cameras | Landmarks | Observations | RMSE (px) | Pipeline time (s) | Exit |
|---|---:|---:|---:|---:|---:|---:|
| October 25 reference, all nine | 9/9 | 1140 | 2523 | 0.900490 | 234.447 | 0 |
| October 27, IDs 0/1/3/4/5 | 5/5 | 696 | 1696 | 1.064560 | 119.030 | 0 |
| October 27, all nine | 2/9 | 180 | 360 | 0.501371 | 85.040 | 1 |

The reference and subset exports reproduce the earlier independently staged P6
**cameras, tracks and points exactly**. They have respectively 1054/599 trusted
landmarks and 86/97 excluded landmarks. Maximum current trusted observation
error is 2.418/2.287 px; the complete scene also retains weak points and larger
residuals, reported rather than hidden. The full October 27 sequence still loses
all tracks at 1→2 and nearly all at 6→7; it cannot bridge these breaks with the
current forward map. Its lower residual describes only two recovered cameras.
This confirms the earlier [dataset assessment](../../data/processed/2026-10-27_115100_304_3ca6165e/DATASET_ASSESSMENT.md),
not successful reconstruction of the full difficult-lighting capture.

Consolidated reports and scenes:

- [Nine-image reference](../../data/processed/2026-10-25_182438_864_e3177490/prototype_incremental_p8/REPORT.md).
- [Five-image lighting subset](../../data/processed/2026-10-27_115100_304_3ca6165e/prototype_incremental_p8/REPORT.md).
- [Full lighting stress case](../../data/processed/2026-10-27_115100_304_3ca6165e/prototype_incremental_p8_full/REPORT.md).

The reference report attributes 63.4 s to local BA, 40.9 s to local patch search,
11.0 s to incremental feature extraction, 9.0 s to LK and 1.24 s to PnP. The
remaining time includes I/O and other bookkeeping. Reducing repeated image
validation/decoding and caching undistorted images is a measured next performance
target; recovering pre-bootstrap or disconnected cameras is separate geometry
work. This task adds orchestration and diagnostics, with no new dependency or
solver. See the [command contract](../architecture/incremental_command.md).

Verification covers raw hashes, complete/partial status, positive depths, finite
geometry, fixed world gauge/baseline, PnP support, bounded refinement,
nonincreasing accepted BA RMSE and nonoverlapping timing totals. All **58 artifact
checks** pass in [verification.json](../../data/processed/p8_benchmark/verification.json).
The focused batch passes **49 tests**; the subsequent P2–P4 stage regression
batch, including additional blank-gyro and PnP-attempt checks, passes **114 tests**.
Repository Ruff lint and formatting checks pass. Test logs, comparison
reproduction code and source fingerprints are under `data/processed/p8_benchmark/`.
P7 native execution and
physical preview/capture acceptance remain pending; P8 supplies offline evidence.
