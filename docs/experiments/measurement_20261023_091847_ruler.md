# New recording: ruler test stops at camera reconstruction

**Follow-up:** the user subsequently permitted excluding unusable views. The
[subset retry](measurement_20261023_091847_subset.md) retains photos 2, 3, 7,
passes camera validation and reaches the ruler check. That check fails the
10% gate; metric scale remains unaccepted. The record below is the original
strict all-eight-view attempt.

The ruler-reference attempt on `2026-10-23_091847_034_be3e7ada` **aborted before
ruler triangulation**. Photo **4** could not be registered reliably. There is
no accepted ruler length, centimetre error or acceleration-derived scale from
this run. The existing 10% ruler requirement remains unchanged and was not
reached. M9 remains incomplete.

Camera geometry is established first in arbitrary visual units. Acceleration
must enter only later as a scale estimate, with the visual camera locations
and relative orientations frozen. It was not used to fill this missing pose.

## Reproduce

```bash
.venv/bin/python tools/validation/audit_correspondences.py \
  data/raw/2026-10-23_091847_034_be3e7ada \
  --output data/processed/2026-10-23_091847_034_be3e7ada/visual_matching_precision \
  --image-indices 0 1 2 3 4 5 6 7 \
  --max-image-size 2400 --max-features 2500 --max-tilt 3 --ratio .75 \
  --regions tools/validation/measurement_20261023_091847_regions.json

.venv/bin/python tools/validation/reconstruct_visual.py \
  data/processed/2026-10-23_091847_034_be3e7ada/visual_matching_precision/correspondences.json \
  --output data/processed/2026-10-23_091847_034_be3e7ada/visual_cameras_precision
```

The audit succeeds; reconstruction exits **2** with:

```text
Visual registration failed (insufficient shared points, failed PnP, or
unconstrained visual baselines) for remaining images [4]; shared counts: {4: 2}.
Aborting.
```

Ignored local artifacts include:

- `visual_matching_precision/review.html`: offline point-association review for
  all eight photos; each pair displays its two participating images.
- `visual_matching_precision/correspondences.json`: complete pairwise audit,
  settings, source hashes and connected-overlap result.
- `visual_cameras_precision/camera_validation.json`: rejected camera prerequisite,
  the failure reason, ruler stage aborted and acceleration stage not run.
- `ruler_validation/excluded_regions.jpg`: visually checked screen/ruler masks.
- `ruler_validation/pairwise_support.png`: match-count heatmap, not a pose or
  measurement accuracy certificate.

No partial scene is exported by the failed reconstruction. Earlier exploratory
matching directories are not accepted substitutes for this run. Preliminary
ruler strips/mark sketches are not finalized annotations or measurement results;
the old measurement01 ruler coordinates were not reused.

## What passed and what failed

All eight images were selected. The entire reference ruler and both screens,
including the repeated Dell-logo regions, were excluded from feature search.
The final audit found **2,227** usable unique pairwise correspondences after
bidirectional descriptor, epipolar, held-out patch-correlation and spatial
deduplication checks. Its overlap graph is connected. These are pairwise
correspondences, not 2,227 independent 3D landmarks.

Before camera fitting, **335 complete linked feature tracks** were reserved for
validation. Their observations never entered essential-pose initialization,
PnP or bundle adjustment. Ruler points and IMU values also never entered fitting.

Photo 4's pairwise support remains weak:

| Other photo | 0 | 1 | 2 | 3 | 5 | 6 | 7 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Usable pairwise matches to photo 4 | 12 | 21 | 0 | 0 | 0 | 2 | 11 |

After incremental visual reconstruction, only **two** of photo 4's locations
linked to accepted existing 3D points, below the eight-point registration gate.
The fallback using two existing visual baselines also failed its geometry/
optimization checks. Two-image-only tracks are supported, but their baselines
must supply a consistent and nondegenerate camera loop. A connected graph of
2D matches alone does not establish the camera centers.

The broader affine search improved matching over the previous max-tilt-2 run:
that reconstruction left photos 4 and 6 unresolved; this run left only 4.
Camera accuracy thresholds were not relaxed. The seven initialized views are
not presented as a validated seven-photo measurement: all eight selected views
are required and the final full-scene hold-out validation was not reached.

This does not establish that photo 4 physically lacks overlap. It establishes
that the current automatically checked observations do not support the required
visual reconstruction. The next geometry work is to inspect/strengthen photo
4's associations with stable shelf/cabinet/desk features across its neighbors.

## Precision and scaling limits

`K` is still approximate metadata-derived calibration, with unknown distortion
set to zero. Even successful small reprojection residuals would not certify
precise absolute camera locations. Independent intrinsic calibration remains
necessary to substantiate that claim. The camera fit retains fixed `K` and uses
the lecture's homogeneous `M=K[R|-RC]` model; see [camera validation equations
and fixed quality gates](../math/visual_camera_validation.md).

Ruler reference scale must be tested only after the camera prerequisite passes.
Known ruler lengths must not optimize the cameras or select the visual seed.
Acceleration scaling must then be evaluated against the ruler independently;
it must not change relative camera geometry. No bias, timing offset, heading or
scale was tuned to make this failed camera run produce a plausible length.

Lecture 002 references remain in code: **pp. 70–76** (`K`), **81–83** (external
parameters and homogeneous 3×4 projection), **98/101** (least-squares eigenvalue
formulation), **133–138** (rays, triangulation and epipolar geometry), and
**141–144** (patch correlation). Essential/PnP initialization, bundle adjustment,
hold-out validation and acceptance thresholds are implementation extensions.

## Verification

The full Python suite passed **483 tests**; the subsequently added CLI abort
regression passed separately (**484 tests collected** in the final tree).
Synthetic cases cover eight-camera reconstruction, disjoint held-out tracks,
rejection of bad held-out projections despite low training error, pair-only
noncollinear camera loops, rejection of underconstrained collinear loops and
camera-preserving outlier removal. Integration checks cover four-image matching,
exclusion masks and a failed camera prerequisite that exports no scene or scale.
Ruff lint/format and `git diff --check` pass. Capture source hashes were checked
against the earlier IMU-comparison report and remain unchanged.
