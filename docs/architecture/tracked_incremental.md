# P5: incremental registration and guided correspondence

P5 extends the [tracked bootstrap](tracked_bootstrap.md) with existing-landmark
PnP, projected local patch windows, pose-derived epipolar descriptor corridors
and conflict-aware landmark growth. It uses the existing NumPy/SciPy/OpenCV
dependencies. Bundle adjustment and repeated search after refinement remain P6.

## Command and saved inputs

Run from the repository root with the Python environment active:

```bash
python python/scripts/extend_tracked_scene.py \
  data/raw/measurement01 \
  data/processed/measurement01/prototype_tracking_p2_full \
  data/processed/measurement01/prototype_bootstrap_p4_full \
  --output data/processed/measurement01/prototype_incremental_p5_final
```

Use a fresh output directory for every run. P2 and P4 must describe the same
session and raw-file hashes, with identical bootstrap pixels and working K.
The command validates the recording, accepted temporal history, transforms and
input provenance. It reconstructs P2's resize/undistortion from saved intrinsics,
preserving stored pixels and ignoring EXIF display rotation. It hashes consumed
inputs before and after processing. Outputs inside raw or any input directory,
including symlink aliases, are rejected; existing output directories are refused.

The command processes selected images **after** the bootstrap pair in capture
order. Earlier unregistered images are reported separately. A failed frame does
not prevent a later sufficiently supported image from registering. Input is a P4
two-camera bootstrap, rather than a resumable P5 checkpoint.

## Reused implementations and new interfaces

| Module / interface | Behavior |
| --- | --- |
| `vision/pnp.py: estimate_pnp(world, pixels, camera, config=...)` | Extracts the existing `solvePnPRansac`/`solvePnPRefineLM` registration from `pairwise_scene.py`; both reconstruction paths now call it. Returns pose, aligned masks/errors, counts and runtime. Normalizes K before OpenCV so unequal sampling/skew work, then verifies positive depth and exact pixel errors. |
| `vision/guided.py: predict_pose`, `project_landmarks` | Predicts visual rotation/center velocity using timestamp intervals. Each projected landmark records its pixel, positive depth, visibility, observation count and search radius. |
| `vision/guided.py: search_landmark_patches` | Reuses `correlation.correlate_local_point` and `correlate_patch`: bounded NCC, minimum score, best/second-peak margin, optional reverse consistency and unique target patches. The existing helper gains configurable score/margin and candidate counts with its previous defaults preserved. |
| `vision/guided.py: fundamental_from_cameras`, `epipolar_search` | Derives `F = K2^-T [t]_x R K1^-1` from registered cameras. Only descriptors within the target epipolar corridor are compared, with the existing descriptor ratio test and optional reverse nearest match in the reverse corridor. No rectification is performed. |
| `vision/incremental.py: register_camera` | Tries temporal 3D–2D PnP first. Uses that pose, or a coarse prediction after failure, for local patch recovery; verifies the combined evidence with PnP. Only final inliers become observations. Failure returns the original scene without a placeholder pose. |
| `vision/incremental.py: grow_landmarks`, `with_landmark_quality` | Reuses `MatchEdge`/`build_tracks` and `triangulate_point`. Preserves existing point positions/IDs and the bootstrap gauge, links new observations without conflicting image features, and records per-point observation count, best/median acute ray angle and reprojection errors/RMSE. |
| `vision/pose_prior.py: tracked_motion_prior` | After failed PnP/local search, reuses P4 calibrated two-view pose estimation on shared P2 tracks. At least three trusted map anchors align its translation magnitude with the existing arbitrary visual scale. This is a search prior; a new camera still needs the original PnP acceptance gates. |
| `vision/incremental_pipeline.py: extend_tracked_scene` | Loads validated artifacts, caches image/SIFT extraction, writes diagnostics and saves the existing scene schema/headless preview. |

By default, corridor search first enriches the known bootstrap pair. After each
successful registration it searches between the latest registered reference and
the new image. `--no-seed-growth` disables the initial enrichment. SIFT is
extracted once per required registered image; image-wide descriptor pairs are
never matched. Non-landmark queries are capped and duplicate source locations
are collapsed before matching.

After a new camera passes PnP, growth also consumes shared accepted P2 tracks,
using their original feature/track IDs. Positive depth, reprojection and angle
gates verify these tracks; forward/backward flow alone cannot create a landmark.
Old rejected tracks require a new view and remain linked to their original
history. Established landmark observations are committed by PnP, not by this
growth step. Temporal seed enrichment is left to P4's ranked support and budget.

Verified temporal PnP inliers skip redundant patch searches. If temporal PnP and
the normal bounded search both fail, the default `--use-two-view-prior` fallback
estimates relative motion from shared P2 tracks using P4's saved pose settings.
For a source-frame point X, target ray y and unit translation t,
`y × (R X + s t) = 0` fits one translation magnitude in existing reconstruction
units. At least `--min-prior-landmarks 3` trusted anchors must have positive depth
and predictions within the configured search radius. Missing, degenerate,
negative-scale or inconsistent support makes the prior unavailable.

This prior only guides another bounded appearance search. A local
source-frontoparallel plane through each landmark normalizes patch scale and
orientation using the existing homography correlation helper. It is a patch
approximation, not surface reconstruction. The search grid/radius is in source
template pixels under this normalization; its target footprint follows the warp.
Ambiguity and reverse checks remain enabled, and the final pose still requires
eight PnP inliers within 3 px at default settings. No camera is created from a
relative pose or three anchors alone. `--no-use-two-view-prior` disables the retry.

Existing observations within the merge radius anchor feature identities. New
feature/track IDs are allocated beyond all P2 temporal IDs, preventing collisions
with later temporal observations. Existing track IDs survive merging; aliases
for merged rejected tracks are saved. Previously rejected tracks require a new
accepted guided edge before retriangulation. Geometric rejection alone is never
silently reversed by rerunning the old observations. New landmarks require
positive depths, bounded per-view error and sufficient intersection angle.

An optional `--parallax-directory P3_DIRECTORY` enables a recorded gyro rotation
prior. It must reference the same P2 manifest. The prior is used only if its
predicted rotation agrees with the visual velocity prediction within the
configured angle. Translation remains the visual prediction; gyro never creates
a registered camera or establishes metric scale. Default behavior uses no gyro
prior. This consistency gate is a heuristic, not camera/IMU calibration.

## Default thresholds and costs

All settings are printed and saved; scalar flags use hyphens. `--help` lists all
options, including `--no-mutual` and the `--pnp-*` parameters.

| Setting | Default |
| --- | --- |
| PnP verified support | 8 landmarks |
| PnP RANSAC | 3 px, 10,000 iterations, confidence 0.999, seed 0 |
| Patch window | Radius 32 px, 21×21 template |
| NCC / separated peak margin | 0.75 / 0.05 |
| Mutual patch consistency | Enabled, return within 2 px |
| Epipolar corridor | 2 px in both directions |
| Descriptor ratio / mutual | 0.75 / enabled |
| Feature budget / corridor queries | 1,200 SIFT rows per image / at most 300 queries per pair |
| Observation merge radius | 2 px |
| New landmark quality | Maximum 3 px per view, minimum 1° ray angle |
| Optional gyro disagreement limit | 15° |

Patch candidates are pixel centers: an evaluated radius-32 window examines
4,225 centers. Descriptor candidates are SIFT rows inside the corridor. Reports
separate these units and include reverse-check costs. Exhaustive denominators
are theoretical counts for the same target image/pool; no exhaustive comparison
is executed. Patch averages include every projected landmark, with an additional
average over actually evaluated windows to distinguish visibility/border culling
from the reduction caused by bounding the search.

The local patch model assumes translation within the window. Strong rotation,
scale change, repeated texture, borders, inaccurate prediction or large still
image gaps can defeat it. Neither image-wide relocalization nor an unverified
camera pose is substituted after failure. SIFT feature detection still scans
each required image, and its runtime is reported separately from matching.

## Outputs and failure behavior

- `incremental.json`: settings, registration failures/successes, PnP counts,
  R/center, aligned point IDs/pixels/inlier decisions, projections, patch scores,
  corridor lines/candidate counts/matches, track conflicts, triangulations,
  timings, input hashes and unregistered image IDs.
- The summary explicitly includes `all_selected_cameras_registered` and final
  `reprojection_rmse_px`; refinement success alone does not mean a sequence was
  registered. `two_view_prior` records relative-pose verification, scale anchors,
  bounded retry costs and final PnP. `image_NNN_two_view_prior.jpg` shows its
  separate search predictions when available.
- `image_NNN_projections.jpg`: prediction centers and accepted/rejected local
  windows, with the source of the coarse pose labelled.
- `pair_AAA_BBB_corridors.jpg`: selected sparse corridor examples including
  accepted and rejected candidates where available. The JSON contains all queries.
- `scene.json`: compatible scene with existing and new cameras/landmarks,
  rejected tracks and `metadata.landmark_quality` keyed by point ID. It retains
  arbitrary scale and input warnings. Existing accepted landmark positions and
  registered camera poses are not optimized by P5.
- `incremental_preview.png`: existing headless scene/camera preview.

Exit 0 means at least one camera was added. Exit 1 means no later camera had
enough verified support; the retained scene, any seed enrichment and failure
diagnostics are still saved. Exit 2 means invalid input/options/output. A partial
new output directory can remain after a late input error; correct it and choose
a new output path.

## Recorded baseline, 2026-10-25

The final run above and `prototype_incremental_p5_final_repeat` use all five
recorded images and the P4 seed 1→2. Their geometry, projections, correspondence
decisions and consumed-input hashes repeat exactly. Image 0 precedes the seed
and is not attempted by this forward incremental stage.

The seed corridor evaluates **593 descriptors over 300 queries**: **1.977 per
query versus 1,200** target rows for exhaustive matching, about 607 times fewer
forward descriptor comparisons. Reverse checks examine another 121 rows.
Twenty-four descriptor correspondences pass the corridor/appearance checks;
geometric triangulation and track association retain **9 new landmarks**, growing
the scene from **11 to 20 points**. Combined accepted reprojection RMSE is
**0.338839 px** (maximum observation error 0.873194 px); it does not imply improved
accuracy relative to P4 because the accepted point set has changed.

The seed growth stage takes 0.48–0.54 s, including its matching/merging and
triangulation but excluding image loading and SIFT extraction. Image 3's patch
search evaluates two full windows: 4,225 centers each versus 1,864,400 centers
for exhaustive patch search. Its whole registration attempt takes about 0.06 s;
image 4 takes about 0.006 s. Total P5 processing, including validation, image
loading, feature extraction, JSON and debug/preview output, takes 13.7–14.4 s.
These times exclude P2/P3/P4 and Python/import startup.

**No post-bootstrap camera is added on this recording.** None of P4's landmarks
have surviving P2 observations in image 3 or 4, and bounded patches supply no
verified additions. Image 3 has some visible predictions but mostly unsupported
or border-crossing windows; image 4 has no in-bounds predicted landmarks. Both
end with `insufficient_3d_2d_correspondences`, retaining the two known cameras.
This is a measured support failure, not a successful registration demonstration.

Synthetic tests exercise the sufficient-support acceptance case: a known pose
with noise/outliers and skew is recovered to below 0.2° rotation and 1°
translation-direction error; a generated recording adds a third camera and
landmarks through the complete CLI. Tests also recover a camera from local
patches with no surviving temporal landmarks, allow a later camera after a
failed frame, reject inconsistent gyro priors and weak triangulation, preserve
IDs/gauge, and protect raw/input artifacts. Existing pairwise PnP, correlation,
track/scene, bootstrap and geometry regressions remain covered.

Validation passes across 27 new focused tests and 102 regressions (129 total),
plus repository Ruff lint/format checks, whitespace checks and relative
documentation links. Raw captures and P2/P3/P4 inputs remain unchanged.

The scene remains physically unverified and has arbitrary visual scale. The
recording's approximate K, request timestamps and nominal IMU mounting remain
limitations. No ruler, acceleration-derived scale, dense reconstruction or
extra dependency enters P5.
