# Calibration: recovering the matrices

This original note connects the implementation to the local lecture's slides
90–101. The notation follows [camera_model.md](camera_model.md). The camera model
uses K for internal parameters, R/C for pose, and M for the full 3×4 matrix.

## The lecture's known-3D calibration (slides 95–101)

Suppose a target supplies known, noncoplanar world positions `P_i`, and each point
has a measured ideal pixel `(u_i,v_i)`. Write the rows of M as `m1^T,m2^T,m3^T`.
Eliminating each unknown homogeneous scale gives two equations:

```text
m1^T P_i_h - u_i m3^T P_i_h = 0
m2^T P_i_h - v_i m3^T P_i_h = 0

      [ P_1_h^T     0       -u_1 P_1_h^T ]
A  =  [    0     P_1_h^T    -v_1 P_1_h^T ]    shape: 2N × 12
      [                 ...               ]

m = [m1^T, m2^T, m3^T]^T
```

The lecture uses a calligraphic P for this design matrix; code calls it A to
distinguish it from a world point and the projection operator P0. Solving `A m=0`
without a scale constraint admits the useless zero vector. Instead solve:

```text
min ||A m||²  subject to ||m||=1
           = min m^T (A^T A) m
```

The minimizing m is the unit eigenvector of `A^T A` with the smallest eigenvalue.
Reshape it by rows to obtain M. `estimate_projection_matrix` actually performs
`numpy.linalg.eigh(A.T @ A)` and takes that eigenvector. It saves the eigenvalue
spectrum and residual in diagnostics. A pseudoinverse with zero right-hand side
would not solve the constrained problem; the lecture's ordinary nonhomogeneous
least-squares discussion must not be substituted blindly here.

At least six general 3D/2D pairs are needed; extra well-distributed observations
are preferable. Coplanar target points do not determine all 12 coefficients. The
implementation rejects that degeneracy rather than exporting an arbitrary M.
M has 12 entries but only 11 degrees of freedom after removing overall scale:
five internal parameters, three rotational parameters, and three camera-center
coordinates. Each point contributes the two homogeneous equations above.

Before forming A, we center and isotropically scale 3D and pixel coordinates to
average distances sqrt(3) and sqrt(2), respectively. After solving:

```text
M = inverse(T_image) M_normalized T_world
```

This preserves the projection while improving numerical conditioning. Normal
equations square the condition number. An explicit `solver="svd"` alternative
uses the equivalent smallest right singular vector; SVD also checks whether the
calibration system has enough independent constraints. Normalization and rank
checks remain necessary for both solvers. Tiny negative minimum eigenvalues can
arise from floating-point roundoff in a positive-semidefinite normal matrix.

## Separating internal and external parameters (slides 90–91)

Partition `M=[M'|m4]`. The lecture writes `M'=K R`: an upper-triangular internal
matrix followed by an orthogonal rotation. This order requires **RQ**, rather
than swapping the factors of QR:

```text
K_raw, R_cw = rq(M')
C_w = -solve(M', m4)
O_cw = -R_cw C_w
```

Signs are selected so K has positive diagonal entries and R has determinant +1.
K is normalized so `K[2,2]=1`. Arbitrary projective magnitude/sign is removed
before these decisions. Tests recover K, R, and C from positive, negative, very
small, and very large multiples of the same M. World-unit changes alter camera
centers and target coordinates together while leaving K unchanged.

This DLT path assumes ideal, already undistorted correspondences. It does not
make lens distortion linear or estimate it from a single projective matrix.

## Why a flat checkerboard needs a multi-view extension

The lecture's coplanar-degeneracy warning also applies to a single flat board.
For practical calibration, the checkerboard utility implements the planar
constraints of [Zhang's calibration method](https://www.microsoft.com/en-us/research/wp-content/uploads/2016/02/tr98-71.pdf),
using the lecture's homogeneous least-squares approach. This extension is distinct
from the noncoplanar 3D DLT derivation above.

For target Z=0, each view has a homography `H=K[r1|r2|O_cw]`. Estimate H using
normalized homogeneous equations. A shared K implies `B=K^-T K^-1`; orthogonality
and equal lengths of r1/r2 give `h1^T B h2=0` and `h1^T B h1=h2^T B h2`.
Stack the resulting linear constraints on B's six symmetric entries over at
least three differently tilted views. The smallest eigenvector of `V^T V`
supplies B. Cholesky recovers K; each H then supplies its view's R and translation.
An SVD projects the estimated rotation to a proper orthogonal matrix.

The code uses shared image normalization so this second normal matrix is also
well conditioned. It rejects repeated/uninformative orientations or a conic that
is not positive definite. OpenCV detects subpixel corners; it does not estimate
K or poses in this implementation.

Finally, SciPy minimizes geometric pixel reprojection residuals over shared K,
five distortion coefficients, and each target-view pose. Focal lengths are
parameterized logarithmically. Target geometry fixes the world frame and units;
the scene gauge freedoms of later reconstruction are not being optimized here.
The default fixes skew at zero under the lecture's perpendicular-pixel-axis
hypothesis; `--fit-skew` relaxes it. Both initial and final Euclidean per-point
reprojection RMSE, per-view errors, and matrices are saved.

More diverse views improve observability. A small reprojection error alone cannot
prove accurate distortion outside the observed region, stable intrinsics under
focus/zoom changes, or correct physical measurements. Checkerboard poses are
relative to the target's coordinates for each view; they are not automatically
the poses of the unrelated `measurement01` photos.
