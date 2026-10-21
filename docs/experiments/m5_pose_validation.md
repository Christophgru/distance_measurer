# M5 relative-pose verification

M5 implements calibrated E estimation, explicit four-way SVD pose decomposition,
cheirality/parallax/reprojection selection and three-camera chaining. Comments
retain lecture 002 references using 1-based PPTX slide/page order: K on 70–76,
external parameters and 3×4 M on 81–83, optical rays/stereo on 132–136 and epipolar
geometry on 138. The linear E fit reuses the minimum-eigenvector technique on
98/101 with a new nine-entry essential-matrix system. These algorithms are
identified as extensions in the [pose notes](../math/relative_pose.md); lecture
assets remain local and ignored.

## Synthetic and integration checks

The full Python suite passes **276 tests**, including **39 new M5 tests**.
Repository Ruff lint and formatting checks pass. Tests require neither the
private recording nor the lecture deck.

- Clean noncoplanar correspondences recover E up to scale/sign with Frobenius
  difference below `1e-9`; the minimal eight-observation case uses `1e-7`.
  Singular values satisfy `(1/sqrt(2),1/sqrt(2),0)` at norm one.
- All four SVD hypotheses have proper rotations and unit translation directions.
  Cheirality chooses the known R and translation sign with angular error below
  `1e-6` degrees, including negative and extreme essential-matrix scales.
  An independent OpenCV recovery call agrees on the clean example.
- With 240 observations, 60 injected outliers and `0.00015` normalized-coordinate
  noise, RANSAC retains at least 170/180 true correspondences and at most three
  false ones. Rotation error stays below `0.5°`, translation-direction error below
  `2°`, and repeated estimates/masks are identical on this build.
- Pure rotation and a `1e-7` baseline fail translation recovery instead of
  producing a successful camera step. Essential masks remain aligned through
  cheirality, and degenerate input/options are reported explicitly.
- Noncommuting chained rotations and nonzero initial camera centers obey
  `T_b_world = T_b_a T_a_world`; chosen baselines of 1 and 2.7 produce the expected
  camera-center displacement. O and C are tested as distinct quantities.
- End-to-end M4 exports with image IDs 4,7,9 and different per-image K recover all
  three frames, preserve the first frame as identity/origin, and close the clean
  rotation loop below `0.001°`. Full match-row masks, input hashes, JSON/NPZ and
  PNG outputs, stale-plot removal, malformed-coordinate/artifact rejection and
  raw/source-output protection are checked. A deliberately inconsistent direct
  edge triggers the visible rotation-loop warning.

## measurement01 diagnostic run

```bash
.venv/bin/python python/scripts/estimate_poses.py data/processed/measurement01/m4 \
    --output data/processed/measurement01/m5
```

This reuses M4's selected stored images 0,1,2, their ideal 1600×1200 working
coordinates, and metadata-derived **approximate** K. Unknown lens distortion is
still assumed zero. The default working-pixel tolerance is 1.5, minimum parallax
0.25°, seed 0, OpenCV 5.0.0 and NumPy 2.5.4. No rectified preview coordinates or
second undistortion are used.

| Pair | M4 F inliers | E inliers | Pose inliers | Median parallax |
| --- | ---: | ---: | ---: | ---: |
| 0–1 | 49 | 46 | 46 | 23.63° |
| 1–2 | 169 | 158 | 158 | 19.01° |
| 0–2 | 100 | 79 | 79 | 19.98° |

Both adjacent edges produce a chain, and all three camera frames can be rendered
in the first camera's world. The plot was inspected. Each adjacent baseline is
set independently to one for display; the resulting positions are illustrative.

**The direct and chained 0–2 rotations disagree by approximately 17.00°.** This
exceeds the application warning threshold of 5° and is reported as
`quality_status="rotation_loop_inconsistent"` in JSON, stdout and the figure.
The unit-chain/direct translation-direction discrepancy is about 34.41°, but
also depends on the unresolved baseline ratio. Repeated runs return the same
counts and diagnostics on this build.

This recording therefore does **not** establish a consistent three-view scene.
Possible contributors include approximate intrinsics, repeated patterns, planar
geometry and surviving mismatches; these diagnostics do not isolate a cause.
M6 tracks/structure and later refinement must retain these concerns rather than
treat this display chain as verified 3D or metric distances. A matching measured
camera calibration is still needed to replace the metadata assumptions.

The report and arrays are under ignored `data/processed/measurement01/m5/`:
`poses.json`, three pair pose NPZ files and `camera_frames.png`. Raw images,
M4 outputs and lecture files are untouched and uncommitted.
