# Relative pose in the lecture's camera conventions

These are original implementation notes. Page references use **1-based PPTX
slide order** in local `002 - Image Formation and Acquisition.pptx`. The deck and
its extracted references stay ignored under `local/lecture/`. Existing
[camera model](camera_model.md), [calibration](camera_calibration.md), and
[matching](feature_matching.md) references are retained.

| Lecture page | Connection to M5 |
| --- | --- |
| 39–45 | Homogeneous rigid transformations compose by matrix multiplication |
| 65–68 | Homogeneous coordinates and arbitrary projective scale |
| 70–76 | K maps optical rays to pixel samples |
| 81–83 | External R, camera center C, origin column O, full 3×4 M |
| 98, 101 | Minimum eigenvector of a homogeneous least-squares system; reused for eight-point estimation |
| 132–136 | Two calibrated views, optical rays and triangulation |
| 137–138 | Correspondences and epipolar geometry |

**Essential matrices, five/eight-point estimation, SVD pose decomposition, and
cheirality selection are extensions of those lecture concepts.** The deck does
not supply these algorithms explicitly. Further references are Hartley and
Zisserman's [Multiple View Geometry, chapter 9](https://www.robots.ox.ac.uk/~vgg/hzbook/hzbook2/WebPage/chapter_9.html)
and [OpenCV's essential matrix API](https://docs.opencv.org/4.x/d9/d0c/group__calib3d.html).
Code implements the decomposition and candidate selection explicitly, using
OpenCV for the five-point RANSAC proposal. Tests independently compare the chosen
pose to OpenCV's recovery function.

## From K and the 3×4 cameras to E

M4 exports ideal, undistorted **working** pixels and the applicable working K.
M5 uses each image's own K, including unequal fx/fy or skew:

```text
q1 ~ K1^-1 [u1,v1,1]^T; q2 ~ K2^-1 [u2,v2,1]^T
q1 = [x1,y1,1]^T;       q2 = [x2,y2,1]^T

M1_normalized = [I | 0]
M2_normalized = [R_second_first | t_second_first]

P_second = R_second_first P_first + t_second_first
E = [t_second_first]_x R_second_first
q2^T E q1 = 0
E ~ K2^T F K1
```

The translation here is the lecture's **origin column O**, not its camera center
C. In the first-camera reference frame, `C_second = -R_second_first^T t`.
Neither M4's rectification H nor a second lens-undistortion step belongs here.
The original calibration sources and assumptions travel into the pose report;
applying an inverse K does not turn an approximate calibration into a measured one.

## Estimation and eigenvectors

The clean-data eight-point solver builds `A e = 0` from rows `kron(q2,q1)` using
row-major `e = vec(E)`. Each point set is centered and scaled to RMS radius sqrt(2)
for conditioning. With `q1'=T1 q1`, `q2'=T2 q2`, the fitted matrix is returned to
the original normalized-ray coordinates by `E = T2^T E' T1`.

The lecture's pages 98/101 solve a homogeneous camera-calibration system by its
smallest eigenvector. M5 reuses this method as
`e = eigenvector_min(A^T A)`, with the appropriate **nine** unknown entries of E.
This is a new correspondence equation, not the lecture's twelve-entry 3×4 M
calibration system. Fitting requires rank at least eight; coincident, collinear,
and exact planar ambiguities fail the clean-data linear solver explicitly.

E has singular values `(s,s,0)`. An SVD projects the estimate onto this essential
constraint and fixes Frobenius norm one. For noisy/outlier input, five-point
RANSAC proposes E; the eight-point consensus refit is retained only if it improves
inlier count, or median residual at equal count. The final mask uses square-root
Sampson error in normalized coordinates. The threshold is working-pixel tolerance
divided by the smallest singular value of either camera's upper-left K block.
This is a conservative circular normalized tolerance, not an exact anisotropic
pixel-error ellipse. Default RANSAC probability is 0.999, iteration cap 10000,
and serial RNG seed 0. At least eight observations/inliers are required by this
initial implementation even though the proposal solver is minimal-five-point.

## Four candidates and cheirality

Write `E=U diag(s,s,0) V^T`, with U and V made proper rotations, and define:

```text
    [ 0 -1  0 ]
W = [ 1  0  0 ]
    [ 0  0  1 ]

R_a = U W V^T;  R_b = U W^T V^T;  t_direction = U[:,2]
```

The four hypotheses are `(R_a, ±t_direction)`, `(R_b, ±t_direction)`. Each t has
unit norm; its sign cannot be chosen by E alone. Using the normalized 3×4 cameras
above, temporary two-view DLT points are obtained from cross-multiplied
homogeneous projection equations and a batched SVD. These points exist only to
test the candidate poses. They are not feature tracks or the M6 sparse scene.

The winning candidate has the most E inliers with finite homogeneous division
and **positive Z in both cameras**. It must have at least eight such points and
at least 60% of E inliers. Its final pose mask also requires normalized reprojection
error at most twice the normalized RANSAC threshold, and a ray-intersection angle
of at least 0.25°. At least eight pose observations must survive, and median
parallax must pass the same threshold. Pure rotation and tiny baselines can
satisfy the epipolar equation while failing these translation checks. The report
retains all four cheirality counts and the reason when a pose is rejected.

## Chaining and unresolved scales

The first selected camera is the world frame, `R0=I, C0=0`. For a successful edge
from a to b and an explicitly chosen positive baseline `s_ab`:

```text
R_b_world = R_b_a R_a_world
O_b_world = R_b_a O_a_world + s_ab t_b_a_direction
C_b_world = -R_b_world^T O_b_world
T_b_world = T_b_a T_a_world
M_b       = K_b [R_b_world | O_b_world]
```

M5's display sets both adjacent baseline lengths to **1**. Those lengths are
independently unresolved: the resulting arrangement is not yet a consistent
monocular scene with only one unknown global metric scale. A correct relative
baseline ratio still needs to be established using multiview structure or other
constraints. The report, command output and figure label this explicitly.

The independently estimated first-to-third edge provides a rotation loop check.
The angle between its R and the chained R should be small; an application warning
threshold of 5° marks a disagreement. This threshold is a diagnostic choice, not
a lecture theorem or an accuracy guarantee. The translation-direction discrepancy
is reported separately and also depends on the arbitrary baseline ratio.

Planar geometry, repeated patterns, approximate K and outliers can still produce
plausible pairwise poses. Cheirality and rotation-loop checks expose some failures
but cannot prove scene correctness or physical distance.
