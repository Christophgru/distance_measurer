# P3 relative gyro rotation, pixel parallax and track ranking

P3.1–P3.4 reuse the existing recorded-session loader, midpoint gyro integrator,
nominal back-camera mounting, intrinsic normalization/projection, P2 tile mapping
and observations. No dependency, Android capture behavior, pose solver or metric
scale implementation is added. This stage analyzes existing P2 tracks; it does
not change those tracks or fit the gyro to a visual reconstruction.

## Run on the fixed baseline

After the [P2 tracking command](temporal_tracking.md), run from the repository root:

```bash
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
  .venv/bin/python python/scripts/analyze_track_parallax.py \
  data/raw/measurement01 \
  data/processed/measurement01/prototype_tracking_p2_repeat \
  --best-n 30 --output data/processed/measurement01/prototype_parallax_p3
```

Use a new output directory per run. Outputs inside raw data, the source session
or the P2 directory, including symlink aliases, are refused. P2 source hashes,
IDs, timestamps, masks and observation history are checked; inputs are fingerprinted
again at the end. No raw/P2 file is modified.

- `parallax.json` reports each pair's rotation matrix, gyro sample counts/spans,
  exact nanosecond endpoints, timestamp/mounting assumptions, residual statistics,
  requested/eligible/selected counts, source/target coverage, per-track score
  components, runtime and warnings. It retains working intrinsics and P2 provenance.
- `pair_AAA_BBB_parallax.npz` retains accepted P2 row indices/track IDs, observed
  and rotation-only pixels, residual vectors, `parallax_px`, prediction validity
  and bounds, ranking eligibility/scores and selected indices. Invalid predictions
  have aligned NaN values and false masks; JSON summaries never emit NaN.
- `pair_AAA_BBB_parallax.jpg` shows blue rotation-only predictions, white observed
  positions and yellow selected tracks. Blue arrows point toward offscreen
  predictions; endpoints at the frame edge are display clipping, not observations.

Exit 0 means all pairs' relative rotations were available, even if no tracks
qualify for ranking; exit 1 records unavailable rotations with their reasons;
exit 2 reports malformed/incompatible inputs or bad options. Missing rotations
are never replaced by identity. A shortfall returns fewer than N tracks explicitly.

## Gyro windows and coordinate convention

`inertial.rotation.integrate_gyro_interval(session, start_timestamp_ns,
end_timestamp_ns, *, max_gap_s=0.25) -> GyroInterval` selects the actual bracketing
gyro samples and interpolates their rates to exact image endpoints. It subtracts
integer nanoseconds before converting to seconds, including for timestamps above
floating-point integer precision. It reuses
`inertial.experimental.gyro_orientation_interpolator`, which integrates midpoint
device-frame increments on the right. Bias is not estimated; there is no position,
accelerometer integration or scale fit. Extrapolation and excessive sensor gaps
fail explicitly.

Reports distinguish recorded `sample_count`/`sample_span_s` from the clipped
`integration_sample_count`/`interval_duration_s` and count interpolated endpoints.
The CLI requires the primary image and gyro clock to explicitly share Android
elapsed realtime. Button-request times remain usable but are flagged as
approximate image times; no callback-time offset is silently substituted.

Android device axes are native-screen X right, Y up and Z out of the screen.
Positive gyro rates follow the right-hand rule, as documented by
[Android's gyro specification](https://source.android.com/docs/core/interaction/sensors/sensor-types#gyroscope).
Stored camera axes are X right, Y down, Z forward. Reuse the existing nominal
mounting helper, with sensor orientation `s`:

```text
M = R_camera_device = Rz(s).T @ diag(1, -1, -1)
```

This follows the clockwise stored-image-to-native rotation definition of
[SENSOR_ORIENTATION](https://developer.android.com/reference/android/hardware/camera2/CameraCharacteristics#SENSOR_ORIENTATION),
and the existing [inertial convention notes](../math/inertial_ruler_retry.md).
No EXIF display rotation is applied to P2's ideal working pixels. As in the
existing heading workflow, the supported nominal model requires an unmirrored
back camera at native target rotation with a known quarter-turn sensor orientation.
Known logical/physical lens switches are rejected. Missing physical IDs do not
certify that the lens stayed the same. Camera/IMU extrinsics remain uncalibrated.

Let `Q = R_world_device2` be the integrated **active** device orientation, seeded
with identity at the first image. A fixed scene ray transforms **passively**:

```text
R_device2_device1 = Q.T
R_camera2_camera1 = M2 @ Q.T @ M1.T
```

`camera_ray_rotation(...)` exposes that camera ray transform. Inverting Q is
essential: rotating the phone and rotating a fixed scene ray have opposite signs.
Synthetic tests cover all four mounts and noncommuting gyro increments. For the
recorded 90° mount, positive device X pitch moves the principal ray right; positive
device Y yaw moves it up; positive device Z roll moves a right-offset ray down.
These labels specify device axes, not an ENU heading convention. Both endpoint
mounts are composed explicitly.

## Rotation-only pixels and residuals

`vision.parallax.rotation_compensated_parallax(first_pixels, second_pixels,
first_camera, second_camera, R_second_first) -> RotationParallax` uses existing
intrinsic normalization and projection:

```text
predicted_second ~ K2 @ R_camera2_camera1 @ inverse(K1) @ first_pixel
residual_vectors_px = observed_second - predicted_second
parallax_px = Euclidean norm(residual_vectors_px)
```

This supports different focal lengths, principal points and skew in the two
images. Inputs must already be undistorted. Behind-camera/near-horizon rays have
invalid predictions; forward but offscreen predictions remain measurable and
carry a separate bounds flag. `parallax_px` is a pixel residual, never metric
translation or a direct depth estimate. It also contains optical-flow, gyro,
timing, mounting, distortion/calibration and rolling-shutter error.

Synthetic rotation-only scenes give median residual below `1e-10` pixels; a
translated scene gives the independently expected depth-dependent displacement.
Image-motion signs are tested in pixels as well as in camera rays.

## Request the best N tracks

`vision.track_ranking.rank_tracks(..., best_n=N, config=RankingConfig(...))`
returns selected row indices, eligibility, scores/components and both cell IDs.
Default gates are age at least 2, finite quality/residuals, FB error at most
1.5 px and residual parallax at least 2 px. Invalid or rotation-only tracks are
not selected merely to fill N.

The score is the equal mean of five bounded terms:

1. `min(age / 5, 1)`;
2. detector response divided by its eligible-pool 90th percentile, capped at 1;
3. `1 / (1 + FB_error_px)`;
4. `min(parallax_px / 32, 1)`;
5. `1 / sqrt(source_cell_count * target_cell_count)`.

Grid defaults are 6×8 and reuse P2's exact pixel-aligned partition. Greedy
selection favors less-used cells in **both** images before score, then breaks
ties by track ID. Ordering is therefore coverage-aware rather than globally
descending raw score. Available and selected coverage are both reported; the
selector cannot invent coverage outside the supplied correspondences. The gates,
grid and saturations are configuration values; CLI flags expose N, grid, minimum
parallax, maximum FB error and gyro gap. No learned model or geometric confidence
claim is attached to the score. Huge residuals saturate rather than receiving
unlimited preference.

## Baseline result and limits

For the fixed P2 three-image tracks with `--best-n 30`:

| Pair | Gyro samples / source span | Estimated angle | Accepted P2 tracks | Median residual | Selected coverage: source / target |
| --- | --- | --- | --- | --- | --- |
| 0→1 | 311 / 3.124074 s | 77.365° | 36 | 2000.355 px | 5 / 4 cells |
| 1→2 | 206 / 2.065923 s | 32.060° | 75 | 580.892 px | 10 / 13 cells |

Each pair supplies 30 eligible selected tracks. Selection covers every available
eligible cell in these pools, but the first pair remains spatially concentrated.
The first run took 5.194 s including validation, image loading, hashes and debug
exports. Gyro interval work took 0.065/0.039 s and projection/ranking took
0.005/0.008 s for the pairs. These are stage diagnostics, not reconstruction timings.

The large residuals are **not evidence of validated translational baseline**.
Observed median image displacements are only 813.044 and 209.869 px, while just
0/36 and 27/75 rotation-only predictions lie inside the target frame. The command
warns when nominal compensation increases median displacement by more than 1%
(with a `1e-6` px numerical allowance). The baseline has button-request timestamps,
approximate intrinsics, uncalibrated mounting and unverified P2 correspondences.
The report retains these limitations; it does not tune a time offset or mounting
against the visual reconstruction. Later calibrated verification must decide
whether a selected track/pair is useful for pose recovery.

The repeat run to `prototype_parallax_p3_repeat` took **4.961 s** and reproduced
gyro matrices, residuals, selected IDs/pixels/ages and all score components
exactly. The full five-image analysis also completes: gyro sample counts are
311, 206, 292 and 535, with selected track counts 30, 30, 3 and 0. The last pair
has a valid gyro estimate but no surviving P2 tracks, so no parallax or useful
track is invented.

**122 focused/regression tests pass**, including P3 gyro/axis/ranking/synthetic
projection tests, command exports and failure paths, the existing inertial
experiment tests and P2 tracking/unit/CLI tests. Repository Ruff lint/format and
whitespace checks pass. Raw data and P2 source fingerprints remain unchanged;
the saved prediction/selection overlays were inspected. Physical synchronization
and camera/IMU mounting remain unvalidated on a controlled phone capture.
