# M3 camera/calibration verification

M3 implements the local lecture's homogeneous 3×4 camera model and noncoplanar
DLT/minimum-eigenvector/RQ calibration. The checkerboard path adds a multi-view
planar initialization and geometric refinement. Original derivations and slide
references are in `docs/math/camera_model.md` and `camera_calibration.md`.

## Synthetic checks

- The reproducible `tools/calibration/synthetic_demo.py` uses 80 known noncoplanar
  positions. A representative noiseless run recovered K within approximately
  `8e-13` pixels and reprojection RMSE around `2e-13` pixels. Tests use less
  platform-sensitive tolerances of `1e-7` pixels for K and reprojection.
- RQ decomposition recovers the same K/R/C for positive, negative, and extreme
  projective scales. The tests also verify `M[C_w,1]^T=0` and world/camera round
  trips, including a change of world units and coordinate origin.
- Exact multi-view planar observations recover K and target-view poses. Added
  radial/tangential distortion and `0.03`-pixel coordinate noise are refined to
  below `0.06`-pixel Euclidean reprojection RMSE, with focal lengths within 1%.
  This test does not claim that weakly observed higher-order distortion
  coefficients are individually accurate.
- An end-to-end test renders five differently tilted checkerboard images,
  detects their corners, and exports a usable YAML file. A representative run
  achieved approximately `0.073`-pixel reprojection RMSE. A blank image is skipped
  and reported. These are generated test images, not calibration of a real phone.
- Point distortion/undistortion round trips stay below `1e-6` pixels, including
  nonzero skew. Image remap coordinates agree with an independent OpenCV map
  within `1e-5` pixels. Raster interpolation is compared using matching bilinear
  operations because OpenCV versions can implement `undistort` interpolation
  differently.

The full Python suite passes 189 tests. Lint and formatting checks pass. Synthetic
profiles and correspondences are written to ignored `data/processed/m3_synthetic/`.
No test requires the lecture file or private recorded photos.

## Current real recording

`inspect_camera.py data/raw/measurement01` reports **metadata-derived
(approximate)** intrinsics for logical camera `0` and stored size `4096×3072`:

```text
K ≈ [[2705.35714,    0,       2047.5],
     [   0,      2705.35714, 1535.5],
     [   0,         0,          1  ]]
```

The estimate assumes physical focal length/logical sensor size, centered aspect
crop and principal point, perpendicular pixel axes, and zero unknown distortion.
Actual active crop, stabilization, zoom, and focus have not been calibrated.
The 90° EXIF rotation is not applied to stored image coordinates or K.

The generated `data/processed/measurement01/camera_intrinsics.json` retains source
and assumptions. A real checkerboard capture with matching settings is required
before replacing it with a measured phone calibration; the five existing scene
photos do not provide known calibration-target positions. Calibration-target
extrinsics must not be mistaken for reconstructed poses of these scene photos.
