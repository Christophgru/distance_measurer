# P6: local refinement and guided retries

P6 adds an optional refinement loop to the [P5 incremental command](tracked_incremental.md).
It reuses the existing SciPy bundle-adjustment solver, patch/descriptor search,
track merging, triangulation and scene schema. No dependency was added.

```bash
python python/scripts/extend_tracked_scene.py \
  data/raw/measurement01 \
  data/processed/measurement01/prototype_tracking_p2_full \
  data/processed/measurement01/prototype_bootstrap_p4_full \
  --refine --output data/processed/measurement01/prototype_refinement_p6
```

Choose a new output directory for every run. P5's raw/input validation, hashes,
pixel conventions, output protection and optional gyro-prior behavior still
apply. Without `--refine`, the command retains its P5 registration behavior.
The input remains a P4 bootstrap; P6 is performed after initial seed enrichment
and after each subsequently registered camera, rather than by resuming an old
P5 output directory. `--no-seed-growth` disables the initial P5 enrichment;
accepted P6 refinement can still trigger seed-pair correspondence retries.

## Local bundle adjustment and the global gauge

`optimization/local_bundle.py:local_bundle_adjust(scene, newest_image_id, ...)`
selects the newest camera and up to two preceding registered neighbors by
default. Neighbors without sufficient trusted support stay fixed; insufficient
support on the newest camera skips the solve. Only trusted landmarks observed
in that window become variables, but **all observations of those landmarks**
remain constraints, including observations outside the window. Outside cameras
and unselected landmark positions stay fixed.

The original `optimization/bundle.py:bundle_adjust` now accepts optional
`variable_camera_ids`, `variable_point_ids` and `observation_point_ids`.
Its default whole-scene behavior and solver remain available. The sparse
Jacobian has columns only for selected variables. It never rebases the world
frame onto a sliding window: the original first camera stays fixed, and the
original first-to-second baseline length remains fixed through the existing
spherical parameterization whenever the second camera is variable. Otherwise
both gauge cameras are fixed boundary poses. Components without enough fixed
camera/structure anchors are rejected rather than introducing a free local
scale. A single fixed boundary camera with otherwise free local structure is
insufficient.

The solver retains its robust-cost and positive-depth acceptance gates. The
local wrapper additionally requires **both selected-observation and full-scene
ordinary reprojection RMSE to decrease or stay within tolerance**, on the same
observations before and after each solve. A candidate that fits trusted points
while degrading full-scene RMSE is rejected. Invalid or under-supported local
problems preserve the input geometry and report their reason. Solver convergence
and acceptance are separate fields; an iteration-limited accepted improvement
does not imply convergence.

## Bounded correspondence rounds

`vision/refinement.py:refine_registered_camera` performs at most two rounds:

1. Recompute confidence and run local BA using the trusted landmark set.
2. If BA is accepted and ordinary RMSE is at most 1 px, halve the current local
   patch radius, down to 4 px. A radius initially below the floor never grows.
3. Reproject landmarks missing from the registered target and retry bounded
   NCC with the refined pose. Reverse/ambiguity checks still apply. New
   observations additionally need an unused target feature and at most 3 px
   reprojection error against that pose.
4. Re-run the existing capped epipolar descriptor search and conflict-aware
   triangulation to confirm newly complete tracks and grow landmarks.

`retry_unmatched_observations` allocates new feature IDs without replacing
already confirmed observations. Appearance alone cannot add an observation
that fails geometry. Existing point positions are subsequently refined by BA;
correspondence retries do not move them to manufacture support. The radius rule
is an RMSE-based heuristic, not a covariance or calibrated uncertainty estimate.
A new unregistered frame starts with the configured coarse-search radius.

Retry feature IDs are allocated above the complete P2 temporal namespace,
including IDs whose observations occur in later frames. P5's temporal landmark
growth can therefore retain persistent IDs without collisions with P6 retries.
The original seed's temporal support remains governed by P4; new temporal
landmarks are added after additional cameras pass PnP.

Stop reasons distinguish no accepted local refinement, no meaningful RMSE or
correspondence improvement, no new guided correspondences despite a useful BA
step, and the fixed round limit. No additions ends the guided loop; an unchanged
perfect scene stops after the first round. Initial SIFT extraction is cached,
and no global all-pairs matching or full-image rectification is introduced.

## Landmark confidence

`vision/landmark_confidence.py:score_landmarks` recomputes reprojection errors
and ray-angle diagnostics from **current geometry**, reusing P5's quality helper.
Cached error/angle metadata cannot make a poor point trustworthy. Nonpositive
depths, out-of-bounds observations and nonfinite residuals exclude a point even
with a zero score threshold.

For each geometrically eligible landmark:

```text
angle       = min(median_acute_ray_angle / 3 degrees, 1)
views       = min(observation_count / 3, 1)
error       = 1 / (1 + (observation_RMSE / 1.5 px)^2)
consistency = 1 / (1 + ((max_view_error - min_view_error) / 1.5 px)^2)
confidence  = angle * views * error * consistency
```

All normalizers and the threshold are configurable. The default threshold is
0.2. This score is a heuristic in [0,1], not a probability. With P6 enabled, only
points meeting the threshold supply temporal/patch correspondences to PnP or
constraints/variables to local BA. Insufficient trusted support is reported;
there is no fallback to excluded points. Missing observations may be confirmed
against an already registered pose without using them to fit that pose; their
updated confidence is available for the next round.

## Configuration and diagnostics

All parameters are printed, saved and listed in `--help`.

| CLI flags | Defaults |
| --- | --- |
| `--ba-window-size`, `--ba-min-landmarks` | 3 cameras including newest; 8 trusted landmarks per variable camera |
| `--ba-max-nfev`, `--ba-loss`, `--ba-f-scale-px` | 100; `soft_l1`; 1 px |
| `--ba-rmse-tolerance-px` | 1e-9 px |
| `--refinement-max-rounds` | 2; only 1 or 2 permitted |
| `--refinement-radius-shrink-factor`, `--refinement-min-search-radius-px` | 0.5; 4 px |
| `--refinement-tighten-rmse-px` | 1 px |
| `--refinement-min-rmse-improvement-px` | 0.001 px, distinguishing unchanged from useful BA steps |
| `--confidence-min-score` | 0.2 |
| `--confidence-target-angle-degrees`, `--confidence-target-observation-count` | 3°; 3 observations |
| `--confidence-error-scale-px` | 1.5 px |

`incremental.json` adds `seed_refinement`, per-registered-image `refinement`,
and separate total BA/refinement-matching runtimes. Each round includes window
and variable IDs, trusted support per camera, confidence scores/exclusion counts,
same-set before/after/candidate RMSE, solver convergence/evaluations, radius,
retry decisions, new observations/landmarks and its stop reason. Per-image PnP
reports also record excluded points. The final scene contains refreshed
per-landmark quality/confidence; the refinement report includes the final pose.

`image_NNN_round_RR_projections.jpg` and `image_NNN_round_RR_corridors.jpg`
visualize each accepted refinement's retries. Existing scene, preview and P5
diagnostics are retained. Output geometry and scale remain explicitly unverified.

With `--refine`, exit 0 means either a camera was added or at least one local BA
candidate was accepted. `registration_success` and `refinement_success` distinguish
these outcomes; unregistered frames retain their failure reasons. Exit 1 means
neither happened, with the retained scene/diagnostics still saved. Exit 2 denotes
invalid input/options/output. P5 without refinement retains its previous statuses.

## Fixed baseline, 2026-10-26

The command above and `prototype_refinement_p6_repeat` consume the same five-image
P2 export and P4 seed 1→2 used for P5. P5's initial corridor stage grows 11→20
landmarks before P6 begins.

| Round | BA observation set | RMSE before → after | Guided growth | Patch radius |
| --- | --- | --- | --- | --- |
| 1 | 20 points / 40 observations | 0.338839 → 0.160348 px | 2 landmarks | 32 → 16 px |
| 2 | 22 points / 44 observations | 0.237967 → 0.172251 px | 0 landmarks | 16 → 8 px |

The second before/after comparison uses the expanded point set; its RMSE is not
the first round's after metric. Both accepted solves converge, in 9 and 6
function evaluations. Round 2 stops with `no_new_guided_correspondences`.
The output has **2 cameras, 22 landmarks**, RMSE **0.172251 px**, and maximum
observation error **0.439300 px**. Camera 1 stays fixed and the 1→2 baseline
remains one unit to floating-point precision. Both rounds trust all available
points at the default 0.2 threshold. Tests with a stricter threshold demonstrate
exclusion and insufficient-support behavior without relaxing that threshold.

BA takes **0.64–0.88 s total**, separately from **0.73–0.88 s** of guided retry,
track merging and triangulation. Full P5/P6 processing, including validation,
image loading, SIFT extraction and diagnostics/preview output, takes
**13.5–14.6 s**; earlier P2/P3/P4 work and Python startup are excluded.
The repeat reproduces camera/point geometry, observation IDs/pixels/errors and
input fingerprints exactly; runtime metadata varies between runs.

Image 0 precedes the seed. Images 3/4 still lack eight verified 3D–2D matches
(one/zero final candidates) and remain unregistered. P6 improves the known
reconstruction without implying successful registration of those frames.
Approximate K, request timestamps, nominal IMU mounting, arbitrary scale and
unverified physical geometry remain limitations; low reprojection error alone
does not resolve them.

Synthetic and generated-recording tests cover fixed outside poses/points,
original gauge, sparse dependencies, under-anchored local scale, RMSE rejection,
patch recovery of missing observations, geometry rejection after appearance
matches, radius/round bounds, early exit, confidence from current geometry,
excluded-point PnP, CLI artifacts and unchanged source inputs. Existing global
BA and P2–P5 geometry/correspondence paths are regression-tested.

Validation: **182 focused/regression tests pass**, including global/local BA,
confidence and bounded retries, PnP/correlation/track/scene helpers, the P4/P5
commands and refinement CLI. Repository Ruff lint/format checks, whitespace
checks and relative documentation links pass. Raw and prior-stage inputs remain
unchanged.
