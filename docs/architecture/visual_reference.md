# Image-only scene recovery with an unknown scale

The visual reference is separate from metric scale estimation. Its inputs are
the photographs, capture JSON and display exclusion polygons. It never reads
`sensors.csv`, acceleration, compass headings or known ruler lengths. Ruler
texture is ordinary image texture. Camera centers and reconstructed points
share one arbitrary visual scale.

Install the optional reference backend on a Linux CUDA 12 host:

```bash
.venv/bin/pip install -e 'python[sfm-cuda12]'
```

Recover the October 23 capture at its native resolution:

```bash
.venv/bin/python python/scripts/reconstruct_visual_reference.py \
  data/raw/2026-10-23_091847_034_be3e7ada \
  --regions tools/validation/measurement_20261023_091847_regions.json \
  --output data/processed/2026-10-23_091847_034_be3e7ada/visual_reference
```

`--gpu-index` is the CUDA ordinal, which can differ from `nvidia-smi` order.
`CUDA_VISIBLE_DEVICES` can select a particular device by UUID. `--stage sparse`
and `--stage dense` allow separate runs. Raw photographs remain unchanged.

The independent reference uses shared SIMPLE_RADIAL self-calibration, native
SIFT, guided correspondence verification and joint bundle adjustment. Dense
PatchMatch estimates depth and normals in the saved undistorted pinhole frame.
Fusion requires three supporting pixels, at most 1 pixel reprojection error,
0.5% relative depth disagreement and 10 degrees normal disagreement. The final
point-list exporter additionally requires three **distinct** supporting views.
Camera-fit residuals and dense consistency checks do not certify physical pose
accuracy. Reflective surfaces and textureless areas can remain incomplete.

The default NCC threshold is 0.5. A separate two-view coverage experiment uses
`--min-ncc 0.3 --min-support-views 2`. It preserves the 1-pixel reprojection,
0.5% depth and 10-degree normal checks, while allowing pairwise overlap. These
are explicitly different support policies; the GUI's minimum-observation filter
can hide samples with only two supporting views. Keep separate processed output
directories to retain both native models. The October 23 experiment reuses the
same saved camera model for both passes; no inertial or scale fitting occurs.

Export a two-million-point inspection target, including sparse feature points:

```bash
.venv/bin/python python/scripts/export_visual_reference.py \
  data/processed/2026-10-23_091847_034_be3e7ada/visual_reference \
  --target-points 2000000 \
  --previous-scene data/processed/2026-10-23_091847_034_be3e7ada/visual_cameras_usable/scene.json \
  --output data/processed/2026-10-23_091847_034_be3e7ada/interactive_scene
.venv/bin/python -m http.server 8765 --bind 127.0.0.1 \
  --directory data/processed/2026-10-23_091847_034_be3e7ada/interactive_scene
```

Open <http://localhost:8765/>. This export uses local BSON sidecars and a browser
worker, so it requires HTTP. The previous standalone export is preserved as
`previous_20k.html`. Zoom, pan, orbit, camera views and stable point-ID inspection
work on typed GPU buffers. Dense-point photo markers are **projections into
supporting views**, rather than persisted sparse feature observations; per-point
reprojection errors are not supplied by native fusion.

An optional orange overlay compares the previous camera estimate with the new
reference. Only the global rotation, translation and arbitrary visual unit ratio
are aligned for display. Relative-rotation differences and baseline ratios
remain observable. Neither reconstruction is designated ground truth.

`reference_export.json` and the cloud manifest report the actual retained
count. The exporter samples supported native points when there are more than
the target, and reports a shortfall when there are fewer. It never repeats points
or interpolates an invented surface to reach a requested count. Large clouds
use 50,000-record BSON batches with unchanged record fields and gzip transport
copies. Sparse observations and camera metadata remain small JSON documents.

The existing pipeline and independent reference both use lecture 002's
homogeneous camera model, K and external parameters (pp. 65–83), homogeneous
least squares/SVD (pp. 98/101), rays and triangulation (pp. 133–136), and
correspondence/photometric consistency (pp. 137–144). SIFT, robust bundle
adjustment and PatchMatch extend those lecture concepts; the code identifies
that distinction. Private slides are not distributed.
