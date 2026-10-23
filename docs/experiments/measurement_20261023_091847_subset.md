# Usable-view retry: cameras pass, metric scale is rejected

Following the instruction to continue when at least two usable images remain,
the retry retains **photos 2, 3 and 7** from
`2026-10-23_091847_034_be3e7ada`. Their visual camera validation passes.
The remaining photos are excluded from this fit; they are not deleted or
declared unusable for every possible reconstruction.

The reference ruler is then checked with **photos 2 and 3**, after rejecting
inconsistent ruler observations in photo 7. One held-out **4 cm** interval
reconstructs as **3.58177 cm**, a **10.4558% deviation**. The requested 10% gate
therefore fails and measurement work stops. The independent acceleration fit
also has a negative signed scale. **No metric scale is accepted or applied.**

## Camera reconstruction comes first

The same matching audit and screen/ruler exclusions from the [original
all-eight-view attempt](measurement_20261023_091847_ruler.md) are reused. No
feature extraction, raw image, intrinsic calibration or matching threshold is
changed. The original validation tracks are split once before fitting any
candidate. Their observations never enter camera optimization.

The usable-view mode excludes unregistered cameras, then tries smaller groups
ranked by visual training support, larger groups first. Validation chooses the
first passing candidate. Every attempt and exclusion is recorded. The reserved
tracks are therefore a model-selection validation set, not an independent final
accuracy certificate. Ruler lengths and acceleration cannot rank or select
camera groups.

| Retained candidate | Accepted validation tracks | Result |
| --- | ---: | --- |
| 0, 1, 2, 3, 5, 6, 7 | 82.07% | Reject |
| 1, 2, 3, 7 | 70.65% | Reject |
| 1, 3, 5, 7 | 84.43% | Reject |
| **2, 3, 7** | **93.39%** | **Pass** |

The final fit has **0.80075 px** training RMS, **1.52682 px** 95th percentile
and **2.93135 px** maximum error. Only **0.496%** of training observations
are removed. Bundle adjustment converges. All three views pass the unchanged
minimum observation and validation checks. Camera centers and rotations are
then frozen in arbitrary visual units.

This is still not proof of precise absolute camera positions. `K` remains
approximate metadata-derived calibration with unknown distortion set to zero.
The [camera-validation note](../math/visual_camera_validation.md) describes
the homogeneous `K[R|-RC]` model and its limits.

## Independent ruler check

The new annotation file is
`tools/validation/measurement_20261023_091847_ruler.json`. It records approximate
manual **8, 12, 16 and 20 cm** graduation/outer-edge intersections, raw stored
pixels, conversion to the 2400×1800 working frame, image hashes and the affine
display transforms. Annotation uncertainty is estimated at roughly two raw
pixels; it is not a verified error bound or subpixel ground truth. Coordinates
were frozen before ruler triangulation, with no equal-spacing constraint or
camera fitting, and were not changed after seeing the result.

All-three-view ruler observations fail the normal reprojection check, reaching
**5.205 px**. Optional observation exclusion tests the largest available groups
using only cheirality, ray angle and the unchanged **3 px** reprojection gate.
Only pair **2–3** accepts all four marks. Pair **2–7** rejects three marks and
pair **3–7** rejects one. Known cm spacings are not used to select that pair;
individual marks are not discarded to improve the length result.

The selected pair's ruler reprojection errors are **1.850–2.895 px**, with ray
angles **2.827–3.463°**. Use only the **8–12 cm** interval for a temporary ruler
reference, giving **0.246568 m per visual unit**. The remaining intervals are
held out:

| Interval | Expected | Reference-scaled result | Deviation |
| --- | ---: | ---: | ---: |
| 12–16 cm | 4 cm | 3.97211 cm | 0.6972% |
| 16–20 cm | 4 cm | 3.58177 cm | **10.4558%** |

The second interval fails. These are reference-scaled validation results, not
accepted physical measurements. Given approximate annotations and weak depth
conditioning, the small exceedance does not establish an exact physical error.
It also does not justify rounding the result below 10%, moving the annotations,
changing `K` or choosing a different pair based on its lengths. No further
measurement tuning is performed after this failure.

## Acceleration supplies scale only

The separate scale experiment uses frozen cameras **2, 3, 7**, their selected
photo timestamps and gyro propagation constrained by image headings. The
declared rest interval fails, so raw compass reference and extra gyro-bias
calibration are rejected; the existing initial fused-orientation fallback is
reported. Later fused heading corrections cannot move camera headings.

Rotate measured acceleration into the orientation reference, remove nominal
vertical gravity **9.80665 m/s²**, then rotate acceleration back into the frozen
visual world. Fit a single scale and unknown starting velocity against the
integrated acceleration. No ruler length enters this fit, and no individual
camera location or rotation is changed.

The signed solution is **−4.77506 m per visual unit**, with **0.33261 m** position
residual RMS. A negative scale is physically invalid; it is rejected rather than
replaced by its absolute value. Three views also cannot jointly identify one
scale, three velocity terms and three free constant world-acceleration bias
terms: that augmented design has rank **6 for 7 parameters**. Timing, tilt,
accelerometer bias and camera/IMU mounting remain unverified.

Two views are enough for visual geometry and a known-length reference scale.
For acceleration alone, two views with unknown starting velocity supply three
equations for four unknowns. That case now returns an explicit rank-deficient
trial instead of requiring a third image or inventing a positive scale.

## Reproduce

```bash
.venv/bin/python tools/validation/reconstruct_visual.py \
  data/processed/2026-10-23_091847_034_be3e7ada/visual_matching_precision/correspondences.json \
  --output data/processed/2026-10-23_091847_034_be3e7ada/visual_cameras_usable \
  --allow-unusable-images

.venv/bin/python tools/validation/estimate_scene_scale.py \
  data/raw/2026-10-23_091847_034_be3e7ada \
  --scene data/processed/2026-10-23_091847_034_be3e7ada/visual_cameras_usable/scene.json \
  --output data/processed/2026-10-23_091847_034_be3e7ada/scale_usable

.venv/bin/python tools/validation/validate_ruler_reference.py \
  data/processed/2026-10-23_091847_034_be3e7ada/visual_cameras_usable/scene.json \
  tools/validation/measurement_20261023_091847_ruler.json \
  --output data/processed/2026-10-23_091847_034_be3e7ada/ruler_usable_subset \
  --allow-unusable-observations
```

Camera validation exits **0**. The scale experiment exits **0** because the
experiment completed, but its `positive_scale_candidate_m_per_unit` is null
and `metric_scale_accepted` is false. The ruler check exits **1** at its gate.

Ignored outputs include `visual_cameras_usable/scene.json`,
`visual_cameras_usable/camera_validation.json`, `scale_usable/scale_candidate.json`,
`scale_usable/scale_candidate.png` and `ruler_usable_subset/ruler_reference.json`.
The plot shows the frozen camera locations and the rejected scale fit. The
three-view ruler diagnostic is retained in `ruler_usable/ruler_reference.json`.
`ruler_usable_subset/annotation_review.png` shows the frozen manual ruler marks.
All source files are hashed and remain unchanged.

Lecture 002 slide/page comments remain in code: **pp. 36–45** for orthonormal
reference changes, **70–76** for `K`, **81–83** for external parameters and the
homogeneous 3×4 projection, **98/101** for least squares and **133–138** for
triangulation and epipolar geometry. Inertial integration, subset selection and
validation gates are implementation extensions.

## Verification

The full Python suite passes **496 tests**. The subsequently added regression
for a failed larger component hiding a usable pair also passes; all three
camera CLI integration cases pass against the final code. The final tree
collects **497 tests**. Coverage includes two-view retention, nonconsecutive IDs,
unchanged validation tracks, visual-support subset ranking, scale ambiguity
with two views, known-scale recovery across a rotated reference frame without
moving cameras, ruler observation exclusion based only on geometry and a
wrong-spacing ruler rejected despite exact reprojections.

Ruff lint/format and `git diff --check` pass. Hashes of camera, scale, ruler and
the previous IMU-comparison inputs were verified unchanged. The generated motion
plot was inspected. M9 remains incomplete at the existing accuracy gate.
