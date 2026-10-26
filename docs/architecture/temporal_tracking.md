# P2 distributed points and temporal tracking

P2.1–P2.4 run offline with the existing OpenCV dependency. No dependencies,
Android code, pose solver or scene schema are changed. Session loading,
intrinsics/undistortion, the pixel-center resize convention and match-diagnostic
renderer are reused. SIFT and descriptor matching are reused only for the
explicit optional initialization described below.

## Commands and outputs

From the repository root, run the three-image P0 dataset:

```bash
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
  .venv/bin/python python/scripts/track_session.py data/raw/measurement01 \
  --image-indices 0 1 2 --initialize-motion \
  --output data/processed/measurement01/prototype_tracking_p2
```

Use a new output directory for each run. Omit `--image-indices` to process all
five images, and omit `--initialize-motion` for pure temporal LK. `--calibration`
accepts the existing calibration format. Raw/source output paths and symlink
aliases are rejected. Raw source hashes are checked before/after processing.
The CLI sets OpenCV to one thread for repeatable benchmarking.

Each output contains:

- `tracking.json`: configuration, camera models, exact integer image timestamps,
  source hashes, per-frame coverage/replenishment, per-pair counts/errors/timing,
  optional initializer diagnostics, track-length histogram and warnings.
- `observations.jsonl`: every seed, successful continuation and rejected attempt,
  including image ID, track ID, pixel, age, birth detector response, status,
  `lk_error` and `forward_backward_error_px`. Failed coordinates/errors are null.
- `image_NNN_tracks.jpg`: active-point distribution over the configured grid.
- `pair_AAA_BBB_flow.npz`: source track IDs/pixels, proposed destinations, both
  LK status arrays, errors and final accepted mask with aligned rows. Undefined
  values are NaN in NPZ, never non-finite JSON numbers.
- `pair_AAA_BBB_flow.jpg`: accepted/rejected diagnostics using the existing
  renderer; only the displayed lines are sampled, not the persisted data.

Exit codes are 0 when some tracks persist across images, 1 for a completed run
with no persistent tracks, and 2 for input/options/I/O errors. An existing output
directory is refused. A zero-survivor pair warns that temporal continuity broke;
command success does not imply every selected camera can be reconstructed.

## Interfaces and behavior

`distance_measuring.vision.tracking` provides:

- `TrackingConfig`: defaults to 800 points, 6×8 tiles, 12-pixel spacing, quality
  level 0.01, 31×31 LK window, pyramid maximum level 4, round-trip threshold
  1.5 working pixels and replenishment below 75% of the point budget.
- `select_track_points(image, config, *, active_points=None) -> TrackingSeeds`:
  uses OpenCV `goodFeaturesToTrackWithQuality` (Shi–Tomasi), retains its minimum
  eigenvalue response and sorts ties deterministically. Tile quotas sum to the
  maximum budget; texture-rich tiles cannot take another tile's quota. Existing
  and new points obey global spacing, including across tile boundaries. Detection
  runs on deficient tile ROIs, not repeatedly on a full image with different masks.
  Shortfalls on textureless/spacing-limited tiles are retained honestly.
- `track_points(first, second, points, config) -> FlowResult`: forward pyramidal
  LK, followed by reverse LK only from valid in-bounds destinations. Rejects bad
  status, undefined coordinates/errors, out-of-bounds forward/reverse results and
  excessive round-trip error. Masks, statuses and reasons preserve input-row IDs.
- `TemporalTracker(config).process(image, image_id) -> TrackingFrame`: processes
  strictly increasing recorded IDs, preserves accepted track IDs and responses,
  and allocates monotonically increasing IDs for new seeds. Age starts at 1 and
  increases per processed observation; a rejected attempt has its attempted age
  but does not count toward the accepted track length. Lost IDs are retired.
  Detection is skipped while the active budget remains above the threshold.

`vision.tracking_pipeline.track_session(...)` implements the command. Images use
the existing ideal working-pixel frame, before EXIF display rotation. Per-image K
and stored-to-working affine are exported. Consecutive frames must have equal
working dimensions. The state holds only the previous grayscale image and active
observations; the command streams observation history to disk. This online state
does not replace the existing descriptor `FeatureTrack`/scene representations.

`lk_error` is OpenCV's mean L1 patch intensity difference, not a pixel-position
error. Round-trip error is Euclidean distance in the source image's working
pixels. Reports include the median over all finite reverse results and a separate
median over accepted rows. The former can be large when most proposals fail.
Flow acceptance is not epipolar verification or proof of physical correspondence.

## Optional initialization for sparse still captures

The baseline has multi-second gaps and large changes of viewpoint. Pure,
uninitialized LK loses every track on its first pair. Changing pyramid/window
settings alone did not produce a useful three-image track set at the fixed
1.5-pixel threshold.

`--initialize-motion` explicitly permits a bounded descriptor-assisted retry when
fewer than `max(8, ceil(input_count * motion_retry_ratio))` tracks survive (capped
at the input count). Default ratio is 0.25. It reuses `SiftBackend` with at most
600 features per image at longest edge 640, the existing ratio/mutual matcher,
and OpenCV homography RANSAC (seed 0, 3 coarse-pixel threshold, at least eight
inliers). The homography is converted using the same pixel-center resize rule.
It warps the source image/points before a residual forward/reverse LK pass with
at most two pyramid levels. Reverse coordinates map back to the original source
frame **before** the unchanged 1.5-pixel round-trip gate. Padded/out-of-bounds
results are rejected. The retry replaces pure LK only if it retains more tracks.

This option pays for global descriptors; it is not a flow-only speed result.
Initialization is skipped on healthy frame sequences and is never run by default.
Feature/match/inlier counts, descriptor-distance upper bound, matrix, runtime and
whether the retry was selected are logged. Default caps imply at most 720,000
pairwise descriptor-distance evaluations per attempted initialization, rather
than the old 6000-feature all-pairs session path. No descriptor matches are
inserted directly as temporal observations. Failure retains the pure LK result.
The warp is an approximate image alignment, not a recovered camera pose; it may
support only one plane. Future gyro guidance can replace its rotation component.

## Recorded results and validation

On the fixed 1600×1200 baseline (OpenCV 5.0.0, NumPy 2.5.4), the first run selected
**765 seeds in all 48 cells**. It retained 36/765 tracks for 0→1 and 75/795 for
1→2, with accepted median round-trip errors **0.0677 px** and **0.1147 px**.
There were **100 tracks with at least two accepted observations, including 11
spanning all three images**. Active counts after replenishment were 765, 795 and
792; all frames occupied all 48 cells.

Tracking took **3.615 s**, including seed/replenishment and optional initialization;
the total command took **9.487 s**, including session validation, loading, source
hash checks and diagnostics. Pair flow times including initialization/retry were
1.256 s and 1.754 s; initializer times within those totals were 0.580 s and
0.777 s. Total timing starts after imports and ends before summary serialization;
loading and debug exports are excluded from the separate tracking total. This
stage does not produce poses/3D points, so its runtime is not directly comparable
to the complete P0 reconstruction.

The five-image run retained active counts **765, 795, 792, 784, 780** with all
48 cells populated. Its pair survivor counts were **36, 75, 3, 0**: continuity
breaks on the last pair, and no track spans all five views. Replenishment keeps
the budget alive but cannot repair absent correspondence. A separate synthetic
13-frame test with deliberate losses preserves long-lived IDs/ages while
replenishing only deficient cells and staying near the requested budget.

Focused unit/integration tests verify coverage, spacing, quality, deterministic
ordering, exact known translation/large rotation, source-frame round-trip units,
status/bounds/FB rejection, empty inputs, persistence, replenishment and skip
behavior, export alignment, calibration reuse and raw-data protection. The
matching/descriptor-track regression suites remain included. Geometry remains
unverified until the later calibrated checks; low FB error alone is insufficient.

**87 focused and regression tests pass**, as do repository Ruff lint/format and
whitespace checks. A repeat to `prototype_tracking_p2_repeat` took 3.596 s for
tracking and 9.370 s total, reproducing input hashes, pair acceptance masks, IDs,
accepted pixels, ages and detector responses exactly. The saved point-grid and
accepted/rejected pair visualizations were also inspected.
