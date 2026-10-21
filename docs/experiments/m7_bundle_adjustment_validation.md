# M7 bundle-adjustment verification

M7 jointly refines camera extrinsics and accepted scene points with fixed K,
using sparse nonlinear least squares and configurable robust loss. Code comments
retain local lecture 002 references by PPTX slide/page order: homogeneous
projection on 65–68, K on 70–76, R/C/O and 3×4 M on 81–83, and the stereo
least-squares motivation on 133–136. Rotation increments also reference 39–45.
Joint bundle adjustment and its gauge/loss machinery are identified as extensions
in the [original math notes](../math/bundle_adjustment.md). Earlier eigenvector
initialization references and private lecture assets remain unchanged/local.

## Automated checks

The full Python suite passes **353 tests**, including **23 new M7 tests**.
Repository Ruff lint and formatting checks pass. Synthetic fixtures do not need
private photographs or lecture material.

- Perfect calibrated observations give a two-component residual vector below
  `1e-12` pixels, in defined track/observation order, with the expected sign when
  one measured pixel is displaced.
- With noisy pixels and perturbed camera/point initialization, joint refinement
  converges and lowers RMSE by more than a factor of ten to below 0.4 pixels.
  Camera rotation error stays below 0.006 radians and center error below 0.04
  scene units for the seeded fixture.
- The first camera is reused exactly and the first baseline length remains fixed
  within `1e-12`. All point/track/feature IDs, fixed K and rejected tracks survive.
  Saved accepted-observation errors agree with independently recomputed public
  reprojections. Source scene objects are unchanged.
- A finite-difference check finds no Jacobian dependencies outside the declared
  sparse pattern. On nondegenerate three-view geometry the gauge-reduced
  Jacobian has full column rank, with `11+3N` variables.
- With six corrupted observations among 70 three-view points, `soft_l1` at
  `f_scale=0.5` converges. A representative run gives summed camera-center error
  0.0161 scene units, compared with 0.3731 for ordinary least squares. Median
  unaffected-point error is 0.0636 versus 1.2361. Tests require absolute bounds
  and a substantial relative improvement in unaffected-point geometry.
- Linear, Huber and Cauchy losses preserve perfect geometry. Invalid configuration,
  empty/disconnected scenes and initial points behind cameras fail explicitly.
  An injected invalid final-depth candidate preserves original geometry. A
  one-evaluation limit is reported as unconverged while retaining a useful scene.
- End-to-end CLI export preserves scene IDs, exact source hashes, metadata and
  large integer timestamps. Before/after metrics are printed. Raw/input output
  aliases are rejected, and old scale/rotation-loop records are retained as
  input provenance rather than relabeled as newly validated geometry.

## measurement01 refinement

```bash
.venv/bin/python python/scripts/refine_scene.py \
    data/processed/measurement01/m6/scene.json \
    --output data/processed/measurement01/m7
```

The run uses `soft_l1`, loss scale 1 working pixel, a 300-evaluation limit, fixed
metadata-derived K, the exact first camera and its approximately unit first
baseline. SciPy is 1.18.1. The sparse LSMR steps use `atol=btol=1e-8` and at most
1000 inner iterations. This accuracy was needed for reliable progress on the
weakly shared geometry: an initial looser inner solve reached the outer limit;
the final configuration converges by the function-tolerance criterion in **47**
outer evaluations.

| Metric, same 421 accepted observations | Before | After |
| --- | ---: | ---: |
| Euclidean reprojection RMSE, working pixels | 0.864250 | 0.269012 |
| Median observation error, pixels | 0.262814 | 0.156776 |
| Maximum observation error, pixels | 2.989133 | 1.140713 |
| Robust objective | 114.466491 | 14.448316 |

All 206 accepted point IDs remain. The 32 rejected M6 tracks remain rejected.
No observation is dropped to reduce these errors. The first camera and first
baseline were checked unchanged; the second adjacent baseline changes from its
arbitrary initial unit value to approximately **0.86251** in first-baseline units.
That ratio is a visual fit, not a physical measurement or accuracy claim.

The saved scene loads successfully. All **28** raw/M4/M5/M6 input files retain
their SHA-256 hashes. Output `scene.json` and `bundle_adjustment.json` remain under
ignored `data/processed/measurement01/m7/`.

Current metadata is `quality_status="bundle_adjusted_unverified"` and
`scale_status="arbitrary_visual_scale_unverified"`. Original approximate-K
assumptions, the unresolved initial baseline convention and M5's approximately
17° rotation-loop discrepancy remain explicitly recorded as input diagnostics.
The old loop test has not been recomputed against an independent refined-pose
estimate. Lower residuals and solver convergence do not establish correct
physical correspondences, calibrated intrinsics, a global optimum, or metric
distance. Most retained points still have only two-view support.
