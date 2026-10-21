# Tracks and sparse triangulation in the lecture's camera model

These original notes retain the references in [camera_model.md](camera_model.md),
[feature_matching.md](feature_matching.md) and [relative_pose.md](relative_pose.md).
Pages mean **1-based PPTX slide order** in local
`002 - Image Formation and Acquisition.pptx`. The deck and extracted reference
material remain ignored under `local/lecture/`.

| Lecture page | M6 connection |
| --- | --- |
| 65–68 | Homogeneous point coordinates and division by scale |
| 70–76 | Internal K and pixel-to-ray conversion |
| 82–83 | External R/C/O and full 3×4 projection M |
| 98, 101 | Minimum eigenvector for homogeneous least squares, reused with point unknowns |
| 132–134 | Calibrated stereo and optical rays from known camera centers |
| 136 | Approximate intersection of corresponding rays, using least squares |
| 137–138 | Correct image correspondences and epipolar geometry |

The explicit multiview DLT equations, union-find track algorithm, JSON schema and
rejection thresholds are implementation extensions. The deck motivates the
geometry but does not specify those algorithms. Related illustrations appear
in [Hartley and Zisserman's chapter 12 figures](https://www.robots.ox.ac.uk/~vgg/hzbook/hzbook2/WebPage/chapter_12.html).
Tests compare clean two-view results with the independent triangulation function
in [OpenCV's calibration API](https://docs.opencv.org/4.x/d9/d0c/group__calib3d.html).

## Track identity and conflicts

An observation key is `(recorded_image_id, feature_row_id)`. A track represents
one hypothesized physical point seen in multiple images, as required on pages
136–137. M6 uses **M5 pose inliers**, preserving their original M4 feature IDs.
M4 ratio/F rejects and M5 pose rejects cannot enter the union graph.

Each edge has a nonnegative score. The pipeline uses normalized Sampson error
divided by its pair's normalized threshold. Lower values receive priority.
Endpoint order and score ties are canonicalized so input shuffling and reversing
pair directions do not change the result. This greedy choice is deterministic,
not a guarantee of the globally optimal correspondence assignment.

Union-find components carry a map from image IDs to feature IDs. A proposed
union is rejected **before mutation** if both components already contain an
image: because components are disjoint, they must contain different features of
that image. A redundant link within the same component is allowed and counted;
this includes consistent triangle closure. Same-image edges are rejected.
Singletons left over from rejected edges do not become tracks.

Each resulting track has at least two distinct images. Tracks are sorted by
their observation-key tuples and assigned deterministic integer IDs. These IDs
are stable for identical inputs/settings, not across changed correspondence sets.
Conflict records retain the rejected edge, score and reason. The scene also
checks that a feature cannot belong to two separate tracks.

## Linear triangulation

For an ideal observed pixel `m_i=[u_i,v_i,1]^T`, pages 70–83 give:

```text
q_i ~ K_i^-1 m_i = [x_i,y_i,1]^T
M_i = K_i [R_i | O_i],  O_i = -R_i C_i
rho_i q_i = [R_i | O_i] P_h
```

No EXIF rotation, rectification homography, or second lens correction is applied.
K and the observed pixels both belong to M4's ideal working image frame. Cameras
with nonzero distortion are rejected by the triangulation API to make that
contract explicit.

Eliminate the unknown depth/scale by cross-multiplying the projection. If `B_i`
is the normalized 3×4 camera matrix, two independent rows are:

```text
(x_i B_i[2,:] - B_i[0,:]) P_h = 0
(y_i B_i[2,:] - B_i[1,:]) P_h = 0
```

Stack every supplied view into a `2N×4` matrix A. The point minimizes `||A P_h||`
subject to `||P_h||=1`. The default SVD takes the last right singular vector.
The optional `eigh` path takes the minimum eigenvector of `A^T A`, reusing the
technique on lecture pages 98/101. Here the unknown has **four point entries**;
it is not the twelve-entry camera calibration system or nine-entry E fit.
SVD avoids squaring the condition number and is the default. This is algebraic
least squares in normalized coordinates, not nonlinear pixel-error refinement.

Before solving, express the world near the first camera with a unit equal to
the largest camera-center separation. This improves conditioning when scene
coordinates have large offsets or different length units:

```text
P_world = C_first + baseline_unit * P_conditioned
B_i = [R_i | R_i (C_first-C_i)/baseline_unit]
```

After homogeneous division, convert the point back to the supplied world. Tests
verify this change of reference frame, scale and origin. The scene keeps K/R/C;
its full 3×4 M and external 4×4 transform are derived from those parameters.

## Rejection checks

The solver rejects coincident camera centers, rank-deficient ray equations,
near-zero homogeneous scale (a point at infinity), and nonfinite results. A
finite candidate must also pass all three geometric checks:

1. **Forward depth:** `(R_i (P-C_i))_z > 0` in every observing camera. The sign
   of an arbitrarily scaled projection M is not a physical depth test.
2. **Reprojection:** project using the same camera K/R/C and require Euclidean
   error below the configured threshold in **every** observed view; default
   3 working pixels. This maximum error differs from the reported aggregate RMSE.
3. **Useful intersection angle:** require at least one view pair with an acute
   ray-line angle of at least 1° by default. The acute angle also rejects nearly
   antiparallel rays, since collinear opposing rays provide poor depth geometry.

A contradictory third observation rejects the complete track; M6 does not
silently discard it and export a two-view point. Rejected tracks remain in the
scene with null `point_id` and a reason. Available per-observation errors remain
attached, and diagnostic angle/error summaries are saved separately.

## What the point cloud establishes

Triangulation fits the supplied cameras. It cannot resolve M5's independent unit
baseline ratio or repair inconsistent rotations. M6 therefore preserves scale
status, pose-loop diagnostics, calibration sources, assumptions and warnings.
Small reprojection errors on accepted tracks do not prove correct physical
correspondences, a consistent multiview scene, or metric distances. In particular,
two-view subsets can fit well while the complete three-view geometry is wrong.
The persisted observations and camera parameters provide input for subsequent
refinement; M7 bundle adjustment remains separate.
