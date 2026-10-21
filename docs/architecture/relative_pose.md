# Run M5 from M4 exports

From the repository root with the Python environment active:

```bash
python python/scripts/estimate_poses.py data/processed/measurement01/m4 \
    --output data/processed/measurement01/m5
```

Run [M4 matching](feature_matching.md) first. M5 reuses its original ideal working
feature coordinates and per-image K, not its rectified preview images. It reads
all three pair files, selects M4's F inliers, estimates calibrated E and chooses
R/translation direction through explicit four-candidate cheirality tests.

Options are `--threshold-px 1.5` in M4 working pixels,
`--min-parallax-degrees 0.25`, and `--seed 0`. Calibration and image selection are
inherited from M4; regenerate M4 with `--calibration` or `--image-indices` to change
them. Coordinate-frame/schema mismatches, double-undistortion, malformed arrays,
out-of-range feature IDs, count disagreements and escaping artifact paths fail
with an actionable error. Raw data and input M4 files remain unchanged. Output
destinations under repository `data/raw/` or the M4 directory are rejected,
including symlink aliases.

## Outputs

| File | Contents |
| --- | --- |
| `poses.json` | Original image/calibration records; settings; per-pair E, R and translation direction; cheirality counts/parallax/failures; chained R/C/O/T_scene/M; loop diagnostics; scale and quality status |
| `pair_NNN_NNN_pose.npz` | Original M4 `indices`, `m4_inliers`, `essential_inliers`, `pose_inliers`, normalized Sampson errors, E when available, successful `R_second_first` and `t_second_first_direction` |
| `camera_frames.png` | Three frames with colored local X/Y/Z axes in the first selected camera's world, independent unit-baseline warning and any rotation-loop warning |

All pair masks have the **full original M4 match-row length**, including rows
excluded before E estimation. `pose_inliers` is a subset of `essential_inliers`,
itself a subset of `m4_inliers`. Match row indices refer to feature rows as in M4.
NPZ errors are infinity for excluded/failed rows; JSON uses null for unavailable
matrices and explicit failure reasons. NPZ files load with `allow_pickle=False`.

Chaining follows the first–second and second–third selected edges. It does not
silently substitute another edge when an adjacent pose fails. The first-to-third
pose is an independent diagnostic, not a constraint that forces the chain to
close. Its failure leaves the chain available but marks the loop unchecked.

Exit **0** / `success=true` means all three frames were produced. This describes
output completeness; inspect `quality_status`, `warnings` and `closure` for
geometric quality. A rotation discrepancy above 5° gets
`quality_status="rotation_loop_inconsistent"` and visible text warnings. Exit
**1** means an adjacent pose failed and the chain is incomplete; available arrays
and the report are still written. Exit **2** means invalid input/options or an
I/O error. A failed rerun removes an old camera-frame plot. Generated files with
the same names are replaced; use different output folders to compare settings.

Even a complete chain has `scale_status="independent_unit_baselines_relative_scales_unresolved"`.
The plotted positions are illustrative, and neither their relative baseline
lengths nor their physical units are recovered by M5. The camera extrinsics retain
the lecture's `O=-R C` distinction. See the [equations and slide references](../math/relative_pose.md).
