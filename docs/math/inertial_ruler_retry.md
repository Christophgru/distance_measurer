# Pairwise ruler geometry and an independent inertial scale trial

These original notes keep lecture 002 page references in **one-based PPTX slide
order**. The local lecture and captured images remain ignored.

## Rebuild geometry without fitting the ruler

The input is the audited pairwise correspondence JSON, not the old M7 camera
poses. A point observed in two images remains eligible. The retry:

1. Fits each pair's essential matrix from ideal working pixels and K.
2. Selects the seed pair by **pose inlier count**, before looking at ruler ticks.
3. Triangulates its direct pose-inlier matches and uses shared 3D points to
   initialize the remaining camera with PnP/RANSAC.
4. Links pairwise location-group IDs into tracks, checks every supplied view,
   triangulates, and runs the existing bundle adjustment with fixed K.

This removes the need to chain the weakest pair's independently estimated pose.
Open two-edge tracks can connect the scene; a closed three-view descriptor
triangle is not required. PnP and essential-matrix initialization are extensions
of the lecture's **pp. 81–83** camera equation, `m ~ K[R|-RC] Pw`. The origin
column is `O=-RC`; PnP's translation is O, so the camera center is `C=-R.T O`.
Triangulation uses **pp. 133–136**, with existing homogeneous/SVD/eigenvector
implementation comments retained.

The unchanged manually annotated ruler ticks never enter camera fitting. The
5–10 cm interval supplies a **reference scale for the shape check only**;
10–15 and 15–20 cm are held out. The ordinary three-pixel reprojection threshold
and ten-percent interval limit remain unchanged. Reference scale is not applied
to the exported arbitrary-unit scene.

## Android sensor frames and timing

Android accelerometer axes are fixed to the device's native screen: X right,
Y up, Z out of the screen. They do not rotate with display orientation. See
[Android sensor coordinate conventions](https://developer.android.com/develop/sensors-and-location/sensors/sensors_overview#sensors-coords).

The recorded rotation vector stores quaternion **(x,y,z,w)**. It is interpolated
with SLERP as a device-to-world rotation `R_wd`, where the world is east/north/up
(ENU). See [Android motion sensors](https://developer.android.com/develop/sensors-and-location/sensors/sensors_motion#sensors-motion-rotate)
and [SensorManager's rotation-matrix API](https://developer.android.com/reference/android/hardware/SensorManager#getRotationMatrixFromVector(float[],%20float[])).
Tests verify quaternion ordering, sign-equivalent quaternions and rotating a
stationary gravity vector into a fixed world frame.

For an unmirrored back camera at native screen rotation, nominal native-camera
axes are `(device X, -device Y, -device Z)`. The recorded sensor orientation
rotates the stored image clockwise into the native view, according to
[CameraCharacteristics.SENSOR_ORIENTATION](https://developer.android.com/reference/android/hardware/camera2/CameraCharacteristics#SENSOR_ORIENTATION).
Thus the nominal device-to-stored-camera rotation is:

```text
R_camera_device = Rz(sensor_orientation).T @ diag(1,-1,-1)

sensor_orientation = 90 degrees:
R_camera_device = [[ 0,-1, 0],
                   [-1, 0, 0],
                   [ 0, 0,-1]]
```

This is an assumed mounting, not a calibrated camera/IMU extrinsic. The lever
arm between camera and accelerometer is assumed zero. These choices compose
rigid frames using lecture **pp. 36–45 and 81–83**; the sensor calibration itself
is additional material.

The recording has button-request timestamps and JPEG callback times, with no
sensor exposure timestamps. Three declared timing hypotheses—button, midpoint,
callback—are compared. They are sensitivity trials, not measured exposure times
or offsets selected to minimize ruler error. Integer nanosecond offsets are
formed before conversion to seconds.

## Acceleration integration and scale observability

The accelerometer includes gravity. Rotate before subtracting it:

```text
a_ENU(t) = R_wd(t) a_device(t) - g_ENU
```

The primary hypothesis uses nominal vertical gravity `[0,0,9.80665] m/s²` and zero
accelerometer bias. A separate trial subtracts the first 0.7-second rotated
median as a gravity-plus-bias proxy. That window is not certified stationary;
the proxy is not a calibration. Gyro and gravity-removed acceleration near each
photo are reported rather than silently assuming the phone is stopped.

Piecewise-linear acceleration is integrated exactly on the irregular sample
timeline, including interpolated interval endpoints:

```text
Δp = v*h + a0*h²/2 + (a1-a0)*h²/6
Δv = (a0+a1)*h/2
```

The returned position integral assumes initial velocity zero. Actual displacement
also contains `v0*dt`. Naive zero-initial-velocity pair integrations are exported
only as diagnostics. Acceleration integration is not a formula from the camera
lecture.

The scale trial instead fits an unknown common initial velocity together with
one visual scale, without any ruler distance:

```text
s * (C_i - C_0) = v0*dt_i + integral_0^dt_i ((dt_i-t) * a_visual_world(t)) dt
```

For three photos this supplies six scalar equations for scale and three velocity
components. The fixed-bias design has four columns. Its rank is checked; a
nonpositive scale is rejected, never converted with `abs()` or clipped to pass.

Allowing a constant world acceleration bias adds three more unknowns. The same
three poses then supply six equations for seven unknowns, so scale/velocity/bias
are jointly underdetermined. The implementation reports this augmented rank.
A physical constant device bias would require rotation-dependent integral terms;
it still adds three unknown components. Timing and mounting uncertainty add
further constraints that these three photos do not supply.

A positive fixed-bias solution is therefore a diagnostic candidate. Only after
fitting it are reconstructed ruler lengths compared with centimetre markings.
Neither the reference interval nor held-out ruler lengths influence that scale
fit. The retry does not attach this unvalidated scale to the scene.

## Diagnose a small scale before changing units

For exactly three positions, divide the two displacement equations by `t1,t2`
and subtract to remove initial velocity. Writing the resulting vectors as
`q_visual` and `q_imu` gives `s=dot(q_visual,q_imu)/norm(q_visual)^2`. Thus a
nearly perpendicular inertial vector produces a small fitted scale even when
its magnitude is reasonable. This is the same least-squares solution as the
joint scale/velocity fit, not a new scale estimator.

A constant world acceleration error `b` satisfies
`q_imu = s*q_visual + (t2-t1)*b/2`. For any assumed scale, choose
`b=2*(q_imu-s*q_visual)/(t2-t1)` and recover an initial velocity that fits all
three positions. The exported error family demonstrates non-uniqueness; a
ruler-reference member must never be presented as independent bias calibration.

The diagnostic also compares the fused rotation vector with gyro integration.
For device-frame angular velocity, orientation increments multiply on the
right: `R_wd_next = R_wd * Exp(omega_device*dt)`. Midpoint integration starts
from one measured rotation-vector orientation and assumes zero gyro bias.
Its world frame initially shares ENU; subsequent drift is not corrected. An
unsupported fused heading jump and an inaccurate gravity direction are distinct
effects: a pure world-yaw correction does not itself tilt gravity.

See [the quantified measurement01 experiment](../experiments/measurement01_scale_discrepancy.md).
