# October 23 image-only camera reference

Capture: `2026-10-23_091847_034_be3e7ada` (eight 4096×3072 photographs).
This is an independent COLMAP 4.0.3 reference for inspecting the existing
lecture-based reconstruction. It uses no acceleration, compass or known ruler
length. Metric scale is shelved for this workstream.

All eight cameras register in one connected visual model, with 5,885 sparse
points and 13,478 observations. Mean training reprojection error is 0.763 native
pixels. The shared SIMPLE_RADIAL lens fit gives focal length 2772.140 pixels,
principal point (2048, 1536) and radial coefficient −0.0007092. The metadata
initial focal length was approximately 2705.36 pixels. Principal point remains
fixed; focal and radial parameters refine jointly with camera poses and points.
These residuals describe training fit, rather than independent pose accuracy.

The three common cameras compare as follows. Baseline ratios are relative to
the camera 2–3 baseline in each reconstruction; a global similarity cannot hide
their differences.

| Camera pair | Relative rotation difference | Baseline-ratio difference |
| --- | ---: | ---: |
| 2–3 | 0.32° | 0.00% (reference baseline) |
| 2–7 | 1.38° | 2.09% |
| 3–7 | 1.09° | 2.48% |

The two camera estimates broadly agree, while those differences can still
matter for triangulation. Neither is ground truth. The inspection viewer can
overlay the previous estimate in orange after aligning only its arbitrary
global coordinate gauge. A larger point count does not itself improve camera
accuracy.

COLMAP's undistorted MVS model provides the exact pinhole calibration for
dense reconstruction and previews. Import converts its pixel-corner origin
to this project's pixel-center origin by subtracting 0.5 from principal points
and measured observations together. It preserves R, C, physical point IDs and
world coordinates. Duplicate SIFT orientation observations within one image
collapse to the observation with the smallest reprojection residual.

Synthetic checks verify the half-pixel conversion, fixed pose import and gauge
comparison. Removing `sensors.csv` from a fixture still permits reference image
preparation. Source hashes protect capture JSON, exclusion polygons and all
photographs. See [workflow and lecture references](../architecture/visual_reference.md).
