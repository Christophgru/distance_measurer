# Camera model: from world points to pixels

These are original implementation notes, following the locally available lecture
`002 - Image Formation and Acquisition.pptx`. Slide numbers refer to PPTX order.
The slides and extracted reference material remain under ignored `local/lecture/`.

## Notation and conventions

| Lecture notation | Code | Meaning |
| --- | --- | --- |
| `K`, also `M_int` | `CameraIntrinsics.K` | 3×3 intrinsic/internal matrix |
| `x0,y0`, also `Cx,Cy` | `cx,cy` | Principal point in pixels |
| `R` | `R_cw` | World-to-camera rotation |
| `C`, called `T` in earlier slides | `C_w` | Camera center in world coordinates |
| `O = -R C` | `O_cw` | World-origin translation column in camera coordinates |
| `T_scene`, also the world/camera block | `T_scene` | 4×4 extrinsic transform |
| `P0 = [I | 0]` | `P0` | 3×4 canonical projection |
| `M` | `projection_matrix(K)` | Full 3×4 ideal projection matrix |

The distinction between `C_w` and `O_cw` is essential. The earlier expression
`P_c = R(P_w-T)` uses a camera-center translation *before* rotation. In a matrix
block `[R | O]`, the translation column is instead `O = -R C`.

We choose the lecture's forward-axis/virtual-image-plane convention: camera X
points right, Y down, and Z forward, with positive focal lengths. Some lecture
diagrams use a physical image plane and negative focal signs. Those are different
coordinate conventions, not negative focal lengths to copy into this model.

Image coordinates are `(u,v) = (column,row)` with zero-based pixel centers and
origin at the upper left. A centered principal point is `((width-1)/2,
(height-1)/2)`. Lecture examples that number pixels from one need their image
origins shifted before comparison. Input JPEGs are decoded **without EXIF
rotation**, so K refers to the stored width/height recorded by Android.

## Homogeneous coordinates (slides 39–45, 65–68)

Append a coordinate to represent affine translation through matrix multiplication:

```text
P_w_h = [X_w, Y_w, Z_w, 1]^T
m_h   = [u, v, 1]^T
[a,b,c]^T  ~  [lambda*a, lambda*b, lambda*c]^T  for lambda != 0
```

The Cartesian pixel is obtained by dividing the first two homogeneous components
by the third. This division is the nonlinear part of perspective projection. A
zero final coordinate represents a point at infinity and has no finite pixel.
`to_homogeneous` and `from_homogeneous` implement these operations. NumPy batches
store points as rows, so code uses `points_h @ M.T`, equivalent to `M @ P_w_h`.

## External and internal matrices (slides 70–83, 102)

```text
P_c = R_cw (P_w - C_w) = R_cw P_w + O_cw

             [ R_cw  O_cw ]         [ 1 0 0 0 ]
T_scene  =   [             ]    P0 = [ 0 1 0 0 ]
             [ 0 0 0   1  ]         [ 0 0 1 0 ]

    [ fx  skew  cx ]
K = [  0    fy  cy ]
    [  0     0   1 ]

rho m_h = M P_w_h
M = K P0 T_scene = K [R_cw | -R_cw C_w]       (3×4, rank 3)
```

`fx` and `fy` combine physical focal length and pixel sampling. `skew` here is the
actual pixel-unit K(0,1) entry; the lecture also factors that term into focal and
axis-angle components. The default perpendicular-axis hypothesis sets it to zero;
the model and optional calibration fit also support a nonzero value.

K does not change merely because the camera moves. R and C describe its pose with
respect to a chosen world frame. Lens/focus/zoom, cropping, or changing image
sampling can change the applicable K. The general M is only defined up to nonzero
scale; physical depth is evaluated from `P_c`, not the sign of an arbitrary M.

The optical center provides a useful check:

```text
M [C_w,1]^T = K (R_cw C_w - R_cw C_w) = 0
```

This is why the camera center belongs to M's nullspace. It is not a principal
point or the top-left image pixel. Tests verify the nullspace identity, the full
matrix product, projective scale invariance, and world/camera round trips.

## Lens distortion: extension of the ideal pinhole matrix

The 3×4 matrix describes ideal perspective projection. Real JPEG pixels may also
contain lens distortion. We add the standard five-coefficient radial/tangential
model after dividing camera X/Y by Z and before applying K:

```text
r2 = x²+y²; radial = 1+k1*r2+k2*r2²+k3*r2³
xd = x*radial + 2*p1*x*y + p2*(r2+2*x²)
yd = y*radial + p1*(r2+2*y²) + 2*p2*x*y
```

The coefficient order is `(k1,k2,p1,p2,k3)`. `distort_points` maps ideal pixels to
distorted pixels. `undistort_points` uses Newton iterations with an explicit 2×2
Jacobian; normalized output gives rays `(x,y,1)`. Singular or non-convergent
inversion is an error. Synthetic round trips must stay below `1e-6` pixel.

`undistort_image` inverse-maps each ideal output pixel into the distorted input
using bilinear interpolation. It retains image size and K; border pixels may be
black. Tiled map construction limits temporary memory for full-resolution phone
photos. Once an image has been undistorted, use `without_distortion()` to avoid
applying distortion a second time. EXIF orientation is not applied by these APIs.
