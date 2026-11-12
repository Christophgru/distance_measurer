# P2–P6 sequence refinement, 2026-10-26

The nine-image session `2026-10-25_182438_864_e3177490` now registers all cameras
in capture order. The earlier P6 run registered only images 0–1. The offline
sequence gate for continuing P7 is successful camera registration across the
recording, verified PnP support, preserved gauge, accepted bounded local
refinement, and repeatable geometry. Device acceptance remains a separate P7 task.

## Causes and changes

The initial P3 budget selected 50 tracks for bootstrap, leaving only 28 mapped
landmarks. A larger bounded ranking budget of 200 supplies 55 bootstrap landmarks
on the same P2 observations. P5 then failed to triangulate newly shared temporal
tracks: it grew only descriptor-based landmarks, so persistent tracks could not
maintain map support. Growth now verifies shared P2 tracks after each newly
registered camera, preserves their IDs, and requires new views before retrying
old rejected tracks. P4 still owns the seed's support and budget.

The 6→7 transition has 15 verified two-view pose inliers but few mapped anchors.
Constant visual velocity misses the large approach toward the scene. The new
search prior reuses P4's essential/RANSAC/cheirality solver, aligns one visual
translation magnitude using trusted map anchors, and normalizes local patch
scale/orientation through each landmark's depth. It never registers a camera.
Final PnP still requires at least eight inliers within 3 px. No quality threshold
was weakened, no global descriptor matching was added to P5, and no dependency
was added. See [implementation and thresholds](../architecture/tracked_incremental.md).

P6 retry feature IDs now reserve all P2 IDs, including future observations.
Already verified temporal inliers skip redundant appearance searches. Reports
explicitly distinguish local refinement success from complete registration.

## Reproduction

Use a fresh output directory for each stage. From the repository root, with
`OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1`:

```bash
.venv/bin/python python/scripts/track_session.py \
  data/raw/2026-10-25_182438_864_e3177490 --initialize-motion \
  --output NEW_P2_DIRECTORY
.venv/bin/python python/scripts/analyze_track_parallax.py \
  data/raw/2026-10-25_182438_864_e3177490 NEW_P2_DIRECTORY \
  --best-n 200 --output NEW_P3_DIRECTORY
.venv/bin/python python/scripts/bootstrap_tracks.py \
  NEW_P3_DIRECTORY --output NEW_P4_DIRECTORY
.venv/bin/python python/scripts/extend_tracked_scene.py \
  data/raw/2026-10-25_182438_864_e3177490 NEW_P2_DIRECTORY NEW_P4_DIRECTORY \
  --refine --output NEW_P6_DIRECTORY
```

The measured runs reuse the original `prototype_tracking_p2`, with P3/P4 exports
under `data/processed/2026-10-25_182438_864_e3177490/p26_refinement/`.
The primary output is `prototype_refinement_p6_v2` under that session's processed
directory. `p26_refinement/repeat` holds the independent repeat. The larger
tracking-budget experiment is not required by the successful command.

## Results

| Pipeline | Registered cameras | Landmarks | Final RMSE (px) |
| --- | --- | --- | --- |
| Original P6 | 2 / 9 | 49 | 0.500219 |
| Temporal growth and larger bootstrap budget | 7 / 9 | 884 | 0.944801 |
| Temporal growth and verified two-view search prior | 9 / 9 | 1,140 | 0.900490 |

These RMSE values use different observation sets and are not a before/after
optimization comparison. Every accepted local BA step preserves or reduces
ordinary RMSE on the same selected and full-scene observations.

| Added image | Verified PnP inliers | Final PnP inputs |
| --- | --- | --- |
| 2 | 11 | 19 |
| 3 | 39 | 69 |
| 4 | 45 | 114 |
| 5 | 49 | 129 |
| 6 | 38 | 82 |
| 7 | 10 | 19 |
| 8 | 15 | 36 |

Images 7 and 8 use the two-view search prior. Final confidence filtering trusts
1,054 points and excludes 86 at the unchanged 0.2 threshold. Five local BA steps
are accepted; rejected candidates preserve the previous geometry. Final adjacent
visual/gyro rotation differences range from 0.382° to 2.076°. The nominal gyro
warnings about increased displacement do not by themselves establish a mounting
error: translation can oppose rotational image motion.

The exported diagnostic scene retains low-confidence points, including a maximum
8.011 px observation error after pose refinement. All points above the trust
threshold remain within 2.419 px in every view; every point exceeding 3 px is
excluded from subsequent pose work. This distinction matters when inspecting the
full point cloud rather than the pose-support subset.

The original `measurement01` P6 baseline retains its two cameras, 22 landmarks
and 0.172251 px RMSE. Its unsupported later frames remain unregistered.

## Validation

The independent repeat reproduces camera poses, point coordinates, track IDs,
observations and reprojection errors exactly. Raw and consumed stage-input
fingerprints are unchanged. Seventeen artifact checks pass, including original
gauge, positive depths, bounded rounds, accepted same-set BA RMSE and exact
original-baseline geometry. All **226 focused/regression tests pass** (123 tracking,
guided registration, local refinement and CLI tests; 103 global geometry,
tracking/parallax and export regressions). Python lint/format and whitespace
checks pass.

The P5/P6 stage takes 282.38 s in the primary run and 277.74 s in the repeat,
including 95.44 s of local BA and 30.97 s of refinement matching in the first run.
P2–P4, Python startup and standalone verification are excluded. Other regression
processes ran concurrently, so these are diagnostic runtimes rather than an
isolated performance comparison. The full report, exact commands, source hashes,
pair consistency and test logs are retained with the processed results.

## Limits and P7 continuation

This establishes offline registration on this recording, not general robustness
or physical pose accuracy. The still sequence uses optional bounded descriptor
initialization across gaps of 2–4.5 seconds. Live preview has a different cadence
and must be measured on a physical device. Calibration, metric scale, rolling
shutter and camera/IMU extrinsics remain unverified. P7.2/P7.3 still require native
tracking and JPEG/sensor coexistence checks on the phone before completion.
