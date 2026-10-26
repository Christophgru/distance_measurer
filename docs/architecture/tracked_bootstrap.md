# P4: bootstrap from temporal tracks

Run from the repository root with the Python environment active:

```bash
python python/scripts/bootstrap_tracks.py \
  data/processed/measurement01/prototype_parallax_p3_repeat \
  --output data/processed/measurement01/prototype_bootstrap_p4
```

The input is a self-contained [P3 export](gyro_parallax.md), including per-image
working intrinsics. Older exports without `images` must be regenerated. The
output directory must be new and outside the input and `data/raw`; symlink
targets are checked. The command reads saved correspondences and performs no
image decoding, feature extraction, descriptor matching or gyro integration.
It hashes the consumed P3 manifest/arrays before and after processing. Raw-input
hashes are carried as provenance without reopening or revalidating raw files.

## Selection and reuse

`vision/bootstrap.py` provides `BootstrapConfig`, `BootstrapPair`,
`evaluate_bootstrap_pair`, `select_bootstrap_pair`, `estimate_bootstrap_pose`
and `triangulate_bootstrap`. `vision/bootstrap_pipeline.py:bootstrap_session`
connects them to P3 artifacts and existing scene persistence/preview helpers.

Pairs are visited in consecutive selected capture order. The first pair passing
selection, relative pose and landmark gates becomes the bootstrap; later pairs
are not evaluated. A failed stage records its reason and allows the next pair
to be attempted. Coverage counts occupied cells separately in both views using
the existing P2 tile mapping. An explanatory score is
`min(source_cells, target_cells) * min(median_residual_px, 32) * min(N/min_tracks, 1)`.
Chronological selection takes precedence over score.

P3's gyro-compensated residual is an input gate. Because its timing/mounting may
be inaccurate, a separate rotation-only visual check normalizes the rays, uses
SciPy's existing Wahba rotation solver (`Rotation.align_vectors`), and reuses
P3's pixel rotation/residual helper. An obvious pure rotation is rejected even
if incorrect gyro compensation produced large residuals. This fit is a
conservative screening diagnostic; it does not establish translation or remove
the need for geometric verification.

The implementation reuses `CameraIntrinsics.normalize_pixels`,
`vision.pose.estimate_essential`, `recover_relative_pose`, `chain_camera` and
`vision.triangulation.triangulate_point`. P3 pixels/K already describe ideal
undistorted working pixels, so they are not undistorted again. The pixel RANSAC
tolerance is converted with the smallest singular sampling of the two intrinsic
matrices, following the existing pose pipeline. Essential support, cheirality,
pose parallax and inlier counts must pass before triangulation.

The first camera fixes the world frame. Relative pose satisfies
`X_second = R_second_first @ X_first + t_second_first_direction`, with unit
translation direction; its center is `-R.T @ t`. The baseline length is one
arbitrary reconstruction unit. Original P2 temporal IDs survive as scene
track/point IDs; observation feature IDs also hold those temporal IDs and the
scene metadata records that convention. Rejected tracks retain their reasons.

## Default gates and diagnostics

All configuration values are exposed as CLI flags (underscores become hyphens),
printed at startup, and saved in `bootstrap.json`. See `--help` for all flags.

| Gate | Default |
| --- | --- |
| Selected surviving tracks | At least 24 |
| Spatial coverage | At least 6 occupied cells per view in a 6×8 grid |
| Median P3 residual parallax | At least 3 px |
| Median residual after visual rotation-only fit | At least 3 px |
| Essential RANSAC threshold | 1.5 working px |
| Essential inlier fraction | At least 0.5 |
| Pose inliers | At least 12 |
| Median pose parallax | At least 0.25° |
| Landmark reprojection error | At most 3 px in each view |
| Landmark intersection angle | At least 1° |
| Landmark budget | At least 8 accepted, at most 100 |
| Random seed | 0 |

Landmarks additionally require finite, in-bounds observations in both views
and positive depth in both cameras. Quality gates are never relaxed to fill
the budget. Coverage gates apply to the input correspondences; consensus and
accepted landmark counts are reported separately.

Outputs:

- `bootstrap.json`: visited pair scores/rejections, settings, input hashes,
  counts, E/R/unit t, cheirality/parallax, per-stage/runtime totals, per-track
  landmark positions/errors/angles/depths and quality/provenance warnings.
- `pair_AAA_BBB_bootstrap.npz`: selected temporal IDs/ideal pixels plus aligned
  essential and pose masks and normalized Sampson errors for each attempted pose.
- `scene.json`: existing sparse-scene schema, emitted only after complete
  success, with two cameras and accepted/rejected temporal tracks.
- `bootstrap_preview.png`: existing headless scene/camera preview, labelled
  with unresolved geometry/scale status.

Exit status is 0 for success, 1 when no candidate passes all gates, and 2 for
invalid input/configuration/output paths. A normal geometric failure still
writes diagnostics but emits no scene or preview. Input errors may leave a
partial new diagnostic directory; use a fresh output path after correcting them.

## Fixed baseline, 2026-10-25

The command above and a repeat using `prototype_bootstrap_p4_repeat` consumed
the same [P0 images 0/1/2](../experiments/prototype_baseline.md) through P2/P3.

| Result | Pair 0→1 | Pair 1→2 |
| --- | --- | --- |
| Selected input tracks | 30 | 30 |
| Source/target occupied cells | 5 / 4 | 10 / 13 |
| Score | 128 | 320 |
| Outcome | Rejected: require 6 cells in each view | First complete bootstrap |
| Visual rotation-only median residual | Not evaluated after coverage failure | 22.853 px |
| Essential / pose inliers | — | 22 / 15 |
| Accepted landmarks | — | 11 |

The accepted observation RMSE is **0.335464 px**, maximum per-observation error
0.873194 px, and triangulation angles range from 1.052° to 3.308°.
Eight tracks are essential outliers, seven fail pose recovery, and four fail
the landmark angle gate. Image 0 remains unregistered. Pose recovery takes
0.036–0.038 s and triangulation 0.033–0.039 s; total processing, including
JSON/NPZ output and the Matplotlib preview, takes 2.35–2.64 s. These are P4-only
times and exclude P2/P3 and Python/import startup. The repeat reproduces the
scene JSON, coordinates, Sampson errors and masks exactly.

Focused tests cover known poses with noisy/outlier observations, chronological
selection, wrong-gyro pure rotation, quality rejection, stable IDs/gauge,
saved-input consistency, command exits, failure fallback and output protection.
All 24 new bootstrap tests and 170 regressions across pose, triangulation,
rotation/parallax, tracking, scene persistence and their stage CLIs pass.
Repository Ruff lint/format checks and relative documentation links also pass.
No dependency was added and the existing reconstruction path is unchanged.

This is a visual bootstrap with **arbitrary scale and unverified physical
geometry**. Approximate K, request timestamps and nominal camera/IMU mounting
limitations from P3 remain in the output. The visual and gyro pair rotations
disagree by 24.945°; the gyro rotation is reported as a diagnostic and does not
constrain the calibrated visual solution. Low reprojection error alone does
not validate those assumptions or establish metric accuracy. P5 registration
and P6 refinement are still separate tasks.
