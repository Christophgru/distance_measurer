# Camera calibration tools

Activate the Python environment and install the editable package as described in
`python/README.md`. Run these commands from the repository root. No lecture slides
or extracted images are needed by any tool or test.

## Reproduce the lecture's eigenvector/RQ solution

```bash
python tools/calibration/synthetic_demo.py
python tools/calibration/calibrate_correspondences.py \
  data/processed/m3_synthetic/known_correspondences.json \
  --camera-id synthetic --output data/processed/m3_synthetic/recovered.yaml
```

The demo constructs a known noncoplanar 3D target and camera, then recovers K,
external pose, and the 3×4 M through normalized DLT, minimum-eigenvector solution,
and RQ decomposition. `--noise-px 0.1` adds controlled image noise. The generated
profiles describe a synthetic camera and must not be applied to phone images.

For measured 3D correspondences, provide JSON with these fields:

```json
{
  "world_points": [[0, 0, 0], [0.1, 0, 0]],
  "image_points": [[123.5, 201.2], [150.1, 202.8]],
  "image_width": 4096,
  "image_height": 3072,
  "coordinate_units": "m"
}
```

The short JSON only illustrates field layout: a solve needs at least six paired,
noncoplanar positions, preferably many more. Image points must be ideal or
previously undistorted. Omit `coordinate_units` if unknown; it defaults to
`world_units`. `--solver svd` selects the equivalent singular-vector solution;
the default `eigh` explicitly solves the lecture's eigenvalue problem.

## Calibrate the physical phone with a flat checkerboard

1. Use a rigid, flat, accurately printed checkerboard with a surrounding margin.
   Count **inner corners**, not squares. For example, a 10×7-square board has
   9×6 inner corners. Measure a square edge in meters.
2. Capture roughly 15–25 sharp views with the same phone camera, capture mode,
   JPEG dimensions, zoom, and practical focus range as the reconstruction photos.
   Tilt the board about both axes and place it across the image, including near
   the edges. Translations of one front-facing board alone are insufficient.
   Keep all corners visible and avoid glare or bent paper.
3. Use the Android app to record these views and export normally. Use the exported
   `images/` directory directly; keep its JPEGs unchanged and retain EXIF tags.
4. Run, replacing the folder and square size with your actual values:

```bash
python tools/calibration/calibrate_checkerboard.py \
  data/raw/calibration_session/images \
  --columns 9 --rows 6 --square-size-m 0.025 --camera-id 0 \
  --output data/processed/calibration/camera0.yaml
```

Images without a full detected board are reported as skipped. Mixed stored image
sizes and fewer than three usable, independently tilted views are errors. The
tool detects corners in stored pixels **without EXIF rotation**, fits shared K
and distortion, estimates per-view extrinsics, prints before/after reprojection
RMSE, and writes YAML. `--fit-skew` also estimates the K(0,1) term; normally keep
the perpendicular-pixel-axis default. Low reprojection error is a fitting
diagnostic, not a measurement-accuracy certificate.

Output contains the README's `image_width`, `image_height`, `camera_matrix`, and
`distortion` fields, plus source/provenance, the stored-pixel convention, solver
diagnostics, and each view's `R_cw`, `C_w`, `O_cw`, 4×4 `T_scene`, and 3×4 `M`.
Board-frame translations use meters because the supplied square size does.

See [the camera model](../../docs/math/camera_model.md) and
[calibration derivation](../../docs/math/camera_calibration.md) for equations,
lecture-slide references, and the difference between full 3D DLT and flat-board
multi-view calibration.

## Apply or inspect an intrinsic profile

```bash
python python/scripts/inspect_camera.py data/raw/measurement01
python python/scripts/inspect_camera.py data/raw/measurement01 \
  --calibration data/processed/calibration/camera0.yaml \
  --output data/processed/measurement01/camera_intrinsics.json
```

The command clearly prints `calibrated`, `metadata-derived (approximate)`, or
`heuristic (approximate)`. With no profile, physical focal length and sensor size
are used first under an explicit centered-crop assumption. A 35mm-equivalent
diagonal assumption is a secondary fallback; otherwise a 60-degree horizontal
field of view is a declared heuristic. Unknown distortion is set to zero and
reported as an assumption, never as measured lens behavior.

A supplied profile must match stored dimensions and, when specified, camera ID.
The tool refuses to silently rotate or resize K or replace an invalid supplied
calibration with an approximation. All CLI outputs stay outside `data/raw/`.

```python
from distance_measuring.camera import load_intrinsics

camera = load_intrinsics("data/processed/calibration/camera0.yaml")
ideal_pixels = camera.undistort_points(distorted_pixels)
normalized_rays_xy = camera.undistort_points(distorted_pixels, normalized=True)
ideal_image = camera.undistort_image(stored_pixels_image)
ideal_camera = camera.without_distortion()
```

Undistorted image size and K are retained, with constant black borders. Do not
rotate image arrays from EXIF before using a stored-pixel calibration. If a later
pipeline chooses upright images, it must transform pixels, K, and the camera
frame consistently rather than swapping width and height alone.
