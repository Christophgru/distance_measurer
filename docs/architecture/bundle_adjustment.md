# Run M7 bundle adjustment

From the repository root with the Python environment active:

```bash
python python/scripts/refine_scene.py data/processed/measurement01/m6/scene.json \
    --output data/processed/measurement01/m7
```

Options are `--loss soft_l1` (`linear`, `huber`, `cauchy` also supported),
`--f-scale 1.0` in working pixels and `--max-nfev 300`. The first scene camera's
pose and the first-to-second baseline length stay fixed. Other poses and points
are refined; K and observations stay fixed.

The command reads a self-contained [M6 scene](sparse_reconstruction.md).
It requires at least two cameras and accepted points, a positive first baseline,
a connected accepted-observation camera graph and sufficient pixel equations.
Accepted input points must be forward of all observing cameras. Output folders
under repository `data/raw/` or the input scene directory are rejected, including
symlink aliases. The source file is read once; its SHA-256 identifies the exact
bytes used. No source/raw/M4/M5/M6 data is written.

## Output and statuses

| File | Contents |
| --- | --- |
| `scene.json` | Refined cameras and points, original IDs/pixels, recomputed accepted-observation errors and angles, rejected tracks, source metadata and optimization diagnostics |
| `bundle_adjustment.json` | Before/after/candidate metrics, loss/settings, gauge, solver status/message/evaluations/optimality, accepted/converged flags, source hash, SciPy version and input quality/scale diagnostics |

The output uses the existing scene schema and `load_scene` API. Its embedded
`metadata.bundle_adjustment` matches the separate report. Rejected M6 tracks keep
their reasons and observations, and accepted point/track/feature IDs are unchanged.
Every before/after metric uses the same retained observation set.

On candidate acceptance, current metadata uses
`scale_status="arbitrary_visual_scale_unverified"` and
`quality_status="bundle_adjusted_unverified"`. `baseline_convention` records
current adjacent lengths; the report's gauge identifies the one fixed baseline.
Original statuses, baseline convention and pose-loop diagnostics remain under
the optimization report's `input_*` fields. The historical loop record also
remains in `metadata.input_pose_closure`. Source lecture references and calibration
assumptions remain. Inherited warning text is prefixed `Input reconstruction:`.
This avoids labeling the old 17° diagnostic as a newly measured refined-pose loop.

The command prints observation reprojection RMSE and robust cost before/after,
as well as solver acceptance and convergence. Robust cost is the loss-weighted
objective; RMSE is the ordinary Euclidean pixel error and need not always decrease
under robust fitting when outliers are present.

Exit **0** means an acceptable candidate and solver convergence. Exit **1** means
an iteration-limited/unsuccessful solve or rejected candidate; the report and
current scene are still saved. A useful finite candidate can be accepted while
`converged=false`. A rejected candidate preserves original geometry. Exit **2**
means invalid input/options or I/O failure. Generated files with the same names
are replaced on rerun; use separate folders to compare configurations.

For equations and exact gauge choices see [math with lecture pages](../math/bundle_adjustment.md).
For synthetic and real-capture evidence see [M7 validation](../experiments/m7_bundle_adjustment_validation.md).
