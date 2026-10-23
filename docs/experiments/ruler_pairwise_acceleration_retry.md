# Ruler retry with pairwise matching and acceleration

The new pairwise correspondence audit substantially improves ruler geometry.
An independent acceleration-derived metric scale still fails. This is a
follow-up to the [original M9 failure](m9_ruler_validation.md), not completion of
the M9 measurement engine or the IMU milestones.

A [subsequent scale diagnosis](measurement01_scale_discrepancy.md) finds fused
orientation jumps unsupported by the gyro. Keeping these matches/poses fixed
and using gyro-integrated orientation brings the nominal button-time estimate
to approximately 4.95 cm for a 5 cm interval. Bias/timing sensitivity still
prevents acceptance of metric scale; the results below retain the original
rotation-vector experiment for comparison.

## Reproduce

From the repository root with the recorded session and matching-review artifacts:

```bash
.venv/bin/python tools/validation/retry_ruler.py \
  --orientation-source rotation_vector \
  --output data/processed/measurement01/ruler_retry
```

Outputs stay in ignored `data/processed/measurement01/ruler_retry/`:

These flags retain this original experiment. The command's current default is
the [visual-heading gyro path](../math/visual_heading.md), which writes to a
separate `ruler_visual_heading_retry/` directory and keeps metric scale unaccepted.

- `ruler_retry.json`: old/new checks, initialization, optimization, orientation
  comparison, acceleration/velocity/scale trials and source hashes.
- `scene_before.json`, `scene.json`: rebuilt and refined arbitrary-unit scenes.
- `pair_*_scene.json`: independently initialized pairwise comparison scenes.
- `ruler_comparison.png`: ruler-reference geometry versus independent IMU scale.
- `acceleration_diagnostics.png`, `imu_world_acceleration.npz`: rotated
  acceleration and scale sensitivity.

A completed experiment exits successfully even when its metric quality gate
fails. Read `absolute_metric_scale_accepted`, not the process exit code, for
metric readiness. No input scene, capture, matching report or calibration file
is modified.

## Geometry result

The inputs contain 28, 113 and 135 distinct passing matches in pairs 0–1, 0–2
and 1–2. Essential-pose support is 26, 100 and 114 respectively. The seed is
therefore **1–2**, chosen by support before inspecting ruler lengths. Its 114
direct pose inliers initialize the scene. Fourteen seed points overlap image 0;
PnP accepts 13 of them. The connected reconstruction contains **257 points**
and **532 observations**, including points observed in only two images.

Existing robust bundle adjustment reduces observation RMSE from approximately
**0.737 to 0.591 pixels**. One training observation grows above the three-pixel
threshold (maximum approximately 4.57 px); robust aggregate improvement does
not imply that every training observation passed a new per-point gate. That
observation is reported, not silently removed. The independent ruler checks
below all pass the ordinary threshold.

As before, use only the **5–10 cm** interval to set a temporary ruler-reference
scale and test the separate **10–15 and 15–20 cm** intervals. Each should be 5 cm.
The same approximate manual tick observations are retained, and no tick is used
to estimate or refine the camera parameters.

| Views | 10–15 cm interval | 15–20 cm interval | Worst interval error | Largest ruler reprojection error |
| --- | ---: | ---: | ---: | ---: |
| 0–1 | 4.917 cm | 4.920 cm | **1.66%** | 0.53 px |
| 0–2 | 5.077 cm | 5.049 cm | **1.55%** | 1.81 px |
| 1–2 | 5.027 cm | 4.998 cm | **0.54%** | 0.16 px |
| 0,1,2 | 4.974 cm | 4.966 cm | **0.68%** | 2.83 px |

All pass the unchanged **10% interval / 3-pixel reprojection gate**. The original
M7 scene failed every ruler triangulation; its worst pairwise diagnostic interval
deviation was **19.31%**, and its maximum three-view ruler reprojection error was
about **151.46 px**. Those old intervals came from rejected points, so they are
diagnostics rather than accepted old measurements.

Independent pairwise pose fitting alone is not sufficient: pair 0–1's separate
initialization still gives a roughly 19% interval error. Connecting image 0 to
the stronger 1–2 reconstruction through shared 3D points fixes that initialization
problem without fitting known ruler lengths. Whole-scene physical accuracy and
camera calibration remain unverified; this check establishes local ruler-shape
consistency using an explicit reference interval.

## Acceleration result

Orientation is interpolated from recorded rotation-vector quaternions, rotated
acceleration is gravity-compensated, and scale is fitted together with unknown
initial velocity. The scale fit receives visual camera centers and inertial
integrals, **no ruler distance**. See [frame equations and assumptions](../math/inertial_ruler_retry.md).

With nominal vertical gravity and button-request timing, the fixed-bias trial
estimates approximately **0.02626 m per reconstruction unit**. Applying that
candidate to the accepted 1–2 ruler geometry yields:

| Marked interval | Actual | Acceleration-scale estimate | Error |
| --- | ---: | ---: | ---: |
| 5–10 cm | 5 cm | 0.515 cm | 89.70% |
| 10–15 cm | 5 cm | 0.518 cm | 89.64% |
| 15–20 cm | 5 cm | 0.515 cm | 89.70% |

Midpoint timing produces roughly **0.227 cm** intervals. Callback timing gives
a negative scale, which is rejected. Using the early rotated acceleration
median as a gravity/bias proxy also gives negative scales under all three timing
hypotheses. No negative solution is made positive or applied to a scene.

The input records button requests rather than sensor exposure timestamps.
Request-to-callback delays are approximately **0.613, 0.524 and 0.378 s** for the
three selected photos. Angular velocity near the button times is approximately
0.10–0.17 rad/s RMS, so zero velocity at the photos is not treated as a known
constraint. The nominal camera/IMU rotation and zero lever arm are assumptions,
not calibration results.

Each scale trial has fixed-bias design rank four, but allowing a free acceleration
bias gives rank **six for seven unknowns**. Thus the recording does not supply
enough constraints to jointly establish scale, initial velocity and bias from
these three positions. Acceleration scale is **not accepted**. The stored scene
remains in arbitrary units; its ruler-reference scale is only a validation
comparison. Exposure timing and inertial calibration remain the next bottlenecks.

## Validation and lecture references

Synthetic checks reconstruct a nonplanar scene from pair overlaps with **no
closed three-view descriptor triangles**, retain two-view-only points, recover
known 3D coordinates up to scale, and refuse inconsistent feature coordinates
or inadequate PnP overlap. Inertial checks cover nominal Android camera axes,
quaternion ordering/signs, rotating stationary gravity, exact integration on
irregular intervals, known scale/velocity recovery, rank-deficient motion and
rejection of negative scales.

The full Python suite passes **426 tests**, including 17 new inertial and
pairwise-reconstruction checks. Repository Ruff lint/format checks also pass.

Code comments keep lecture 002 **pp. 36–45, 81–83, 93 and 133–138**, with existing
homogeneous/eigenvector references preserved in the reused solvers. Android
sensor conventions, PnP, acceleration integration and scale observability are
identified as additional material, with primary sources linked in the math note.
Generated plots were visually inspected. Source hashes, including the original
M7 scene and raw sensor recording, are verified unchanged.
