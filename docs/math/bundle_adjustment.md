# Bundle adjustment in the lecture's camera model

These are original notes. Lecture pages mean **1-based PPTX slide order** in the
local `002 - Image Formation and Acquisition.pptx`; source assets remain ignored
under `local/lecture/`. Earlier [camera](camera_model.md),
[pose](relative_pose.md) and [triangulation](sparse_reconstruction.md) references
are retained.

| Lecture page | M7 connection |
| --- | --- |
| 39–45 | Rotation composition and homogeneous frame transformations |
| 65–68 | Homogeneous perspective projection and division by depth |
| 70–76 | K converts normalized rays to image samples |
| 81–83 | R, C, O=-R C, and the full 3×4 M=K[R|O] |
| 98, 101 | Earlier homogeneous least-squares initialization in M3/M6 |
| 133–136 | Optical rays and approximate reconstruction with least squares |

Page 136 motivates least squares because observed rays do not intersect exactly.
**Joint nonlinear camera/point refinement, robust losses, gauge fixing and sparse
Jacobians are extensions**, not algorithms specified in this deck. The
implementation uses [SciPy's least_squares API](https://docs.scipy.org/doc/scipy/reference/generated/scipy.optimize.least_squares.html).
Related multiview examples appear in [Hartley and Zisserman's chapter 18 figures](https://www.robots.ox.ac.uk/~vgg/hzbook/hzbook2/WebPage/chapter_18.html).

## Reprojection residuals

For world point P_j observed in camera i, the existing lecture model gives:

```text
P_c = R_i (P_j-C_i)
rho*m_predicted = K_i P_c = K_i [R_i|-R_i C_i] [P_j,1]^T
predicted_pixel = (m_x/m_z, m_y/m_z)
r_ij = predicted_pixel - observed_pixel_ij
```

The public residual vector concatenates `(delta_u,delta_v)` in scene track and
observation order, using accepted points only. It has two entries per observation.
The scene contains **ideal M4 working pixels**, so K is the working K, distortion
is already removed, and neither EXIF rotation nor rectification H is applied.
K, its source/assumptions and all measured observation coordinates stay fixed.

The fit minimizes `0.5*f_scale²*sum(rho((r/f_scale)²))`. The default `soft_l1`
loss reduces influence from large residual components. Alternatives are `linear`,
`huber`, and `cauchy`; `f_scale` is in working pixels. The loss applies separately
to u and v components, not to an observation's Euclidean norm. A robust fit can
lower this objective while increasing unweighted RMSE, so both are reported.
RMSE is `sqrt(mean(delta_u²+delta_v²))`, consistent with M6's observation metric.

## Exact gauge fixing

Visual reprojection has a seven-dimensional similarity freedom: global rotation,
translation and scale. The first scene camera's complete R/C pose is held fixed;
its six pose variables never enter the optimizer. The first-to-second center
distance L is also fixed, removing the remaining global scale variable.

The second center has **two**, rather than three, free parameters. Let d0 be its
initial unit direction from the first center, and B a 3×2 orthonormal tangent
basis perpendicular to d0. For tangent coordinates a and theta=||a||:

```text
d(a) = cos(theta)*d0 + sinc(theta/pi)*B*a
C_second = C_first + L*d(a)
||d(a)|| = 1
```

This local sphere chart enforces baseline length throughout optimization, without
a soft scale penalty or redundant radial variable. Every free rotation is an
SO(3) rotation-vector increment composed with the initial world-to-camera R.
Other centers and all point positions are free. For three cameras and N points,
the parameter count is `11+3N`. A synthetic numerical Jacobian test verifies full
column rank after this gauge reduction on nondegenerate geometry.

Points and centers are represented internally relative to C_first in units of L.
This improves numerical conditioning without changing pixels or the exported
world frame. Pose decoding retains the lecture's distinction between a center C
and the translation column O=-R C. The first camera is reused exactly; the fixed
baseline is preserved to floating-point precision.

Holding L fixes a **convention**, not a measured physical length. Later cameras'
baseline lengths can change as shared tracks constrain them. No metric scale is
inferred. A connected observation graph and sufficient equation count are checked
before solving; these necessary checks do not establish full observability for
every planar, weak-parallax or insufficiently shared-view configuration.

## Sparse solve and physical checks

Each observation depends on its own camera block and its shared point block.
The numerical Jacobian sparsity follows those dependencies. The optimizer uses
TRF with LSMR, Jacobian column scaling and configurable robust loss; the report
records tolerances and the evaluation limit. Accurate inner linear steps matter
for weakly constrained geometry: the real-data example converges with tighter
LSMR tolerances where a looser inner solve exhausted its evaluation budget.

Trial iterates may cross zero depth. The internal solver therefore divides by
`max(Z,1e-8)` in normalized baseline units and adds a third residual per
observation, `10*max_sampling*max(1e-8-Z,0)`. This finite, piecewise differentiable
guard is an implementation extension, not part of the physical camera model.
It is zero for valid depths. The public pixel residuals and reported pixel RMSE
exclude it. Initial points must have depth above that floor in every observing
camera; a final candidate must also satisfy it for **every** retained observation.

A candidate is used only if its parameters/residuals are finite, depths pass,
and robust objective does not increase beyond numerical tolerance. Otherwise
the original scene geometry is exported with a rejection report. Acceptance and
solver convergence are separate fields: a useful iteration-limited candidate
can be saved without claiming convergence or a global optimum.

## Persistent observations and interpretation

No observations or accepted point IDs are pruned during this fit. Rejected M6
tracks remain rejected; they are not resurrected. Before/after statistics use the
same observation set. Every accepted observation error and point intersection
angle is recomputed for the output scene. Rejected tracks retain their original
diagnostics because they have no candidate point to reproject.

The report retains input scale/quality/baseline/rotation-loop records. The output
updates current baseline lengths and labels its scale and geometry as unverified.
The historical M5 loop discrepancy is not silently presented as a fresh test of
the refined poses. Calibration assumptions and inherited warnings remain.
Reducing reprojection error does not validate repeated-pattern matches, repair
unknown K automatically, or establish real-world distances.
