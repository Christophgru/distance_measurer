# Compass reference, image headings and gyro propagation

The compass supplies one overall world reference. Camera orientations relative
to each other come from the visual reconstruction. Gyro integration provides
motion between image timestamps; image headings constrain its accumulated yaw
error. Later compass samples are retained for inspection but cannot change
relative image headings. This is offline processing, using both endpoint images
when interpolating a correction between photos.

## Capture and run

Install app 0.2 from `android/app/build/outputs/apk/debug/app-debug.apk`. Its
availability map includes optional magnetometer and game rotation vector as well
as the original streams. Start a recording with the phone completely still for
at least two seconds, capture connected overlapping photos, then hold still for
two seconds before ending. The default declared rest window is 0.1–1.6 seconds.
Use a different window explicitly if the capture protocol requires it.

Once the selected photos have a visual scene:

```bash
.venv/bin/python python/scripts/prepare_heading.py data/raw/measurement01 \
  --scene data/processed/measurement01/ruler_retry/scene.json \
  --output data/processed/measurement01/visual_heading
```

Use the appropriate capture/scene paths for another recording. Outputs are:

- `scene.json`: all visual cameras and points rotated into the reference frame.
- `heading.json`: compass/rest validity, overlap, image headings, source hashes
  and the single world rotation. Full visual camera orientations and continuous
  gyro orientations are identified separately.
- `orientation.npz`: sampled gyro-only and heading-corrected device orientations
  in xyzw order, with elapsed seconds.
- `heading.png`: Matplotlib comparison with image heading markers and correction
  magnitudes.

Disconnected selected cameras abort before heading export. The
[overlap gate](../architecture/overlap_gate.md) requires a connected graph, not
overlap between every pair. Compass/gyro data never supply a missing visual edge.
Inputs and source captures remain unchanged. No metric scale is applied.

## One global compass reference

The stationary window must pass coverage and stability checks before estimating
gyro offset or using compass data. These checks require at least one second,
30 accelerometer/gyro samples, gaps at most 0.05 seconds, acceleration standard
deviation at most 0.05 m/s² per axis, gyro standard deviation at most 0.005 rad/s
per axis, mean gyro norm at most 0.03 rad/s, and a gravity proxy norm within
9.3–10.3 m/s². The protocol must actually hold still: sensor checks alone cannot
distinguish every slow motion from bias. A single rest pose does not identify
full accelerometer bias.

Compass samples in that window require accuracy status at least 2, sufficient
coverage, and deviation from their median at most 10% of its norm. The configured
field plausibility gate is 10–100 microtesla, with at least 5 microtesla of
horizontal field. These are implementation quality gates, not proof of an
undisturbed magnetic environment. The game rotation vector is recorded for
future comparisons; this pipeline does not substitute it for image headings.

Let `u` be the normalized stationary acceleration (up direction in device
coordinates), and `b` the median magnetic field. Construct:

```text
n = normalize(b - dot(b,u)*u)
e = cross(n,u)
R_reference_device = rows(e, n, u)
```

The reference is east/**magnetic** north/up. No declination correction to true
north is claimed. Android magnetic-field readings already include the device's
sensor calibration; this code does not estimate hard/soft-iron calibration.
See [Android sensor definitions](https://source.android.com/docs/core/interaction/sensors/sensor-types).

After subtracting the stationary gyro mean, propagate from this initial
orientation. With nominal device-to-camera mounting `R_cd` and the first visual
camera's external rotation `R_cw`, align the visual world once:

```text
Q       = R_reference_device(t_first) * R_cd.T * R_cw_first
P_new   = Q * P_old
C_new   = Q * C_old
R_new   = R_old * Q.T
```

Lecture 002 **pp. 36–45 and 81–83** supplies this orthonormal frame composition.
Using homogeneous coordinates (**pp. 65–68**), `H=diag(Q,1)` gives
`P_new_h=H*P_old_h` and the 3×4 camera matrix transforms as `M_new=M_old*H^-1`.
Projection, every inter-camera rotation and all distances are preserved. Compass
does not modify K, triangulation or image associations.

## Heading corrections preserve the gyro gravity direction

The visual scene retains its full image-derived rotations. For continuous
inertial propagation, only heading corrections are applied to the gyro:

```text
R_corrected(t) = Rz(delta_heading(t)) * R_gyro(t)
```

A single device-fixed image axis defines heading for the whole sequence. Choose
the best-conditioned camera forward/up/right axis from its horizontal projections
in both visual and gyro poses. This handles desk-facing and upright photos;
the selected axis is recorded so azimuth has an explicit meaning. An almost
vertical reference axis cannot supply a reliable heading.

At each photo, calculate the signed horizontal angle from the gyro-predicted
axis to the visual axis. Unwrap the correction angles and interpolate them
between photos; outside the image interval, hold the correction and continue
gyro propagation. The scene's photo-to-photo rotations remain visual. Compass
samples after the initial reference are not used in these corrections.

Since `Rz*up=up`,
`R_corrected.T*up = R_gyro.T*up`: heading correction preserves the gyro's tilt
and gravity direction exactly. Full visual tilt corrections would affect
gravity removal; they are not applied to the acceleration integration. Gyro tilt
drift and accelerometer bias remain limitations over longer recordings.

The heading-corrected gyro and full visual orientation therefore need not agree
in tilt. Their reported headings agree at image times. This agreement is imposed
by the images, not an independent orientation accuracy test. The algorithm
extends the lecture's rotation geometry; it is not a lecture calibration method.

## Missing data and current recording

If the raw compass is missing or rejected, retain an explicitly unverified
initial fused orientation, or a gravity-only arbitrary heading if a valid rest
window exists. No reliable north heading is fabricated. A failed stationary
window leaves gyro bias unestimated. Gyro gaps above 0.25 seconds abort continuous
orientation propagation.

`measurement01` has no raw compass/game-vector samples, no actual frame
timestamps, and motion in the default starting rest window. It can exercise
visual relative headings and gyro interpolation, but validating the new compass
reference and bias correction requires a new capture. Its globally rotated scene
changes reprojection residuals by less than 5e-13 pixels, and the continuous
heading correction changes the gyro gravity direction by less than 5e-16.

Metric scale remains unaccepted under the existing 10% ruler gate. The
measurement01-specific `retry_ruler.py` now defaults to the visual-heading gyro
path and writes to `ruler_visual_heading_retry/`; it is not a generic ruler
annotation tool for new recordings. Original rotation-vector experiments can be
reproduced explicitly with `--orientation-source rotation_vector` and a chosen
output directory. No thresholds or biases are tuned to make ruler lengths pass.

With image heading corrections, the nominal button-time scale estimates the
three 5 cm ruler intervals as **4.948, 4.975 and 4.946 cm** (maximum error 1.08%).
The callback hypothesis still reaches roughly 13.4% error, and gravity/bias-proxy
trials reach roughly 18–31%. These sensitivity failures keep metric scale
unaccepted. Image-constrained heading agreement is not independent validation.

The complete Python suite passes **468 tests**. The capture APK builds; its
**17 JVM tests** and Android lint pass. Repository Ruff lint/format checks pass.
Tests cover compass quality rejection, stationary-window failure, exact visual
heading anchors, preserved gyro tilt, wrap-boundary continuity, missing compass
or fused orientation, camera-frame timing, reprojection invariance, disconnected
overlaps and unchanged source hashes. Physical-device validation of new compass
and JPEG frame-timestamp recording remains outstanding.
