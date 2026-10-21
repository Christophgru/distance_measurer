# Sparse correspondence and rectification

These original notes retain the camera conventions in [camera_model.md](camera_model.md).
Lecture references mean **1-based PPTX slide/page order** in the local
`002 - Image Formation and Acquisition.pptx`. The source deck, extracted text,
and reference images remain ignored under `local/lecture/`.

| Lecture page | Idea used here | Code |
| --- | --- | --- |
| 65–68 | Homogeneous pixel coordinates and division by scale | `vision/epipolar.py` |
| 70–76, 82–83, 102 | Internal K, external transform, full 3×4 M | `vision/pipeline.py`, existing `camera/` |
| 136–137 | Corresponding observations are needed before triangulation | `vision/features.py`, `matching.py` |
| 138 | A corresponding point lies on its epipolar line | `vision/epipolar.py` |
| 139–140 | Rectification gives parallel epipolar lines and common scanlines | `rectify_matches`, `visualization/matches.py` |
| 141–144 | Matching costs and difficulty in regions with little texture | Empty-feature diagnostics; sparse SIFT extension |

The lecture's matching examples use SSD/correlation blocks. **SIFT, its ratio
test, RANSAC, F estimation, and Sampson distance are extensions**, not algorithms
claimed to appear in this deck. SIFT and distinctive nearest-neighbor matching
follow [Lowe's original paper](https://www.cs.ubc.ca/~lowe/papers/ijcv04.pdf),
particularly sections 3–6 and 7.1. This backend uses OpenCV SIFT with L2 descriptors;
its calls are documented in [OpenCV's matching tutorial](https://docs.opencv.org/4.x/dc/dc3/tutorial_py_matcher.html).
The geometry calls use [OpenCV's calibration reference](https://docs.opencv.org/4.x/d9/d0c/group__calib3d.html).
Backend results are exposed through a small `FeatureBackend` protocol so later
methods can keep the correspondence/geometry stages.

## Coordinates and calibration

The command validates the recording, chooses exactly three recorded image IDs,
and decodes stored JPEG pixels without EXIF rotation. By default it reduces the
longest side to 1600 pixels for feature extraction. A resize with actual factors
`sx = working_width/stored_width`, `sy = working_height/stored_height` changes
zero-based pixel centers by:

```text
    [ sx   0   (sx-1)/2 ]
A = [  0  sy   (sy-1)/2 ]       K_working = A K_stored
    [  0   0       1    ]
```

This carries the lecture's pixel-sampling part of K into the working frame. The
external parameters are unchanged; the full matrix becomes `M_working = A M`.
Lens coefficients operate on normalized rays and remain unchanged by resizing.
The image is then undistorted with M3's model **before** SIFT extraction, so
features, F, threshold, and the images being warped all use ideal working pixels.
The exported working K has zero distortion because this correction has already
been applied. Applying the correction again would be an error.

`stored_ideal_to_working_ideal_affine` maps ideal stored pixels to ideal working
pixels. It is not the complete mapping from distorted JPEG pixels. To return a
feature to a measured JPEG coordinate: apply `A^-1`, divide the homogeneous
coordinate, then call the original camera's `distort_points`. For M5, normalize
the exported ideal working points using the exported working K, without another
undistortion. The original K, lens coefficients, source and assumptions remain in
the report. Metadata-derived and heuristic K stay explicitly approximate.

## Filtering and epipolar verification

Each descriptor row has the same feature ID as its coordinate, response, diameter
(`size`), angle (degrees), and packed OpenCV octave. SIFT results are ordered by
response with deterministic coordinate/scale/orientation tie breakers. The cap
is applied after ordering. Exact repeatability is tested on one OpenCV build;
different builds and platforms may change detection details.

Matching reports separate counts:

1. `raw_count`: query rows having a nearest train descriptor.
2. `two_neighbor_count`: query rows with two candidates for a ratio test.
3. `ratio_count`: rows satisfying `d_best < ratio * d_second`, default ratio 0.75.
4. `retained_count`: rows remaining after optional reciprocal nearest-neighbor
   filtering (enabled by default). The reverse check uses one nearest neighbor;
   it does not impose a second reverse ratio test.
5. `inlier_count` / `outlier_count`: the retained rows classified by F.

Strict inequality rejects equally good candidates, including zero/zero ties.
Without two candidates, the ratio test cannot retain the query. Empty arrays are
normal results for images with insufficient texture.

For ideal homogeneous observations `x1 = [u1,v1,1]^T`, `x2 = [u2,v2,1]^T`,
page 138's epipolar constraint is expressed algebraically as:

```text
l2 = F x1;  l1 = F^T x2;  x2^T F x1 = 0

                         |x2^T F x1|
Sampson error = -----------------------------------
                sqrt(l2_x²+l2_y²+l1_x²+l1_y²)
```

F is a rank-two 3×3 matrix, distinct from either camera's rank-three **3×4 M**.
RANSAC estimates F; an SVD removes any numerical third singular value and F is
normalized to Frobenius norm one. The final exported mask uses the square-root
Sampson error above, default threshold 1.5 working pixels. OpenCV's RANSAC proposal
uses its own point-to-line criterion with the same numerical threshold; its
internal mask is not exported as if it were the Sampson mask. At least eight
retained matches and eight final inliers are required. Coincident/collinear
coordinates, missing F, and unsuccessful rectification produce explicit failure
messages. A fixed seed makes serial RANSAC calls repeatable; OpenCV's RNG is
shared state, so these calls are not a concurrent-reproducibility guarantee.

## Rectified diagnostics

Uncalibrated rectification estimates image homographies `H1,H2` from verified F
and its inliers. In transformed coordinates:

```text
x1_rect ~ H1 x1; x2_rect ~ H2 x2
F_rect ~ H2^-T F H1^-1
v1_rect ≈ v2_rect
```

These H matrices are projective image transforms, not external camera rotations
or metric camera poses. A common vertical scale/offset and independent horizontal
offsets bring the match support onto the output canvas while preserving shared
scanlines. The viewport uses full inlier vertical bounds and 2nd–98th percentile
horizontal support so an extreme horizontal disparity does not shrink the whole
display. Some features/image regions can still be clipped or black; each preview
reports how many match lines are actually drawn. Vertical error statistics are
in rectified output pixels and depend on the viewport scale.

Each diagnostic has an accepted row in green and a rejected row in red, with
text labels as well as color. Rectified views include horizontal guides. Each
row draws at most 60 visible matches, sampled deterministically; the complete
data and mask remain in NPZ files. The side-by-side preview may be reduced to
800 pixels per panel for readability; the exported H transforms refer to the
working image size, before that display reduction.

F consistency alone cannot establish the correct physical correspondence in
repeated patterns, or distinguish general motion from planar/pure-rotation
degeneracies. It establishes neither a valid translation nor metric distance.
Relative pose and cheirality checks remain M5; triangulation remains M6.
