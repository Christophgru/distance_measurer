# Establish cameras before metric scale

Camera fitting uses only image observations and fixed intrinsics `K`. It cannot
read acceleration, compass headings, ruler coordinates or ruler lengths.
All selected images must register. A connected matching graph alone is not
sufficient to establish their camera centers.

The correspondence audit supports two or more images. Normalized search masks
can exclude changing screens, repeated logos and the whole reference ruler.
Pairwise matching does not require a feature to appear in three photos.

Before pose initialization, reserve every fifth linked feature track, including
all of its observations. These tracks never enter camera fitting. They are a
validation split of automatically checked matches, not manual ground truth.

An essential-matrix seed supplies `R` and a unit translation direction. The
conditioned seed heuristic requires at least 30 pose inliers and 1–45 degrees
of acute parallax, then maximizes support times squared sine of parallax.
This is a numerical initialization choice, not a camera-accuracy certificate.
Incremental PnP uses already triangulated tracks, followed by bundle adjustment.

If only two-view tracks are available, two existing cameras can constrain a new
center using their visual translation directions. Their relative rotations must
agree within 3 degrees, the center intersection must be well conditioned and
forward along both rays, and the line miss must be within 5% of the existing
baseline. A lone baseline or collinear arrangement cannot determine the missing
relative baseline length and is rejected. Pair-only tracks therefore remain
usable without claiming that every pairwise overlap determines a full trajectory.

This fallback permits 15 px only for initial triangulation of the new view,
then optimizes, prunes training outliers and optimizes again. Accepted observations
must meet the normal 3 px reprojection limit. No relaxed initializer is an
accepted measurement.

Final camera validation requires converged bundle adjustment, training RMS at
most 1 px, 95th percentile at most 2 px, maximum at most 3 px, at least 20
training observations per camera, at most 10% removed training observations,
at least 90% accepted held-out tracks and at least eight accepted held-out
observations per camera. Held-out points are triangulated with the cameras
fixed; their acceptance uses the normal 3 px and 1 degree gates. Validation
does not move cameras to improve held-out reprojections.

Only after these prerequisites pass may the ruler be triangulated with frozen
cameras. One marked interval supplies a temporary reference scale; other
intervals must independently satisfy the existing 10% requirement. Only then
should acceleration be tried for metric scale. Acceleration must not repair
camera positions or rotations, fill missing visual overlap, or choose the
visual solution according to the ruler result.

Low reprojection error is necessary but cannot prove precise absolute camera
locations. Intrinsic calibration, lens distortion, correspondence mistakes and
weak scene geometry remain limitations. Positions retain arbitrary visual units
until an independent scale is accepted.

Lecture 002 **pp. 70–76** defines `K`, **pp. 81–83** the external parameters and
homogeneous 3×4 matrix `M=K[R|-RC]`, **pp. 98/101** homogeneous least squares,
and **pp. 133–138** calibrated rays, triangulation and epipolar constraints.
The hold-out split, essential/PnP initialization, center intersection and
quality thresholds are implementation extensions; corresponding comments retain
the slide/page references.
