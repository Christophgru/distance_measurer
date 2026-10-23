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

## Accepted million-point inspection model

The accepted density target was revised to **1,000,000 points**. The final
export contains 5,885 sparse feature points and 994,115 native fused depth
samples, selected from 1,043,882 eligible fused locations. No exact duplicate
positions required removal. All eight camera poses and the shared lens fit
remain unchanged during densification. No metric scale is calculated.

The dense pass uses NCC ≥0.3, at least two distinct supporting views, a
1-pixel fusion reprojection limit, 0.5% relative depth disagreement and
10-degree normal disagreement. Fusion aggregates at most four measured depth
pixels per point. The point inspector distinguishes supporting-view projections
from original sparse feature observations; native fusion does not provide a
measured reprojection residual for each exported dense point.

Among dense samples, 806,022 have two supporting views, 139,890 have three and
48,203 have four. The GUI's minimum-observation control exposes this tradeoff.
A separate strict three-view/NCC-0.5 model retains 111,299 fused samples plus
the same 5,885 sparse points. Both remain available for comparing coverage.

The main viewer is <http://localhost:8765/>, with a copy under
`interactive_scene/million_reference/`. The strict reference is under
`interactive_scene/strict_reference/`; `previous_20k.html` preserves the earlier
standalone export. The main cloud uses **20 BSON batches**, totaling about
185.3 MiB uncompressed and 43.8 MiB in gzip transport copies. Record fields are
unchanged; no database server is involved.

Validation includes 516 Python-suite passes, targeted checks for BSON boundaries,
pixel-origin conversion, support IDs, duplicate removal and reference export,
and browser loading/picking/navigation/filter/mobile checks. A separate temporary
synthetic 2M BSON fixture exercised the same worker and GPU buffers with 19 draw
calls and was removed afterward. It is not part of the reconstructed scene.
