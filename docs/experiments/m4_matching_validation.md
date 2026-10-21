# M4 matching verification

M4 adds a SIFT backend interface, L2/ratio/mutual descriptor filtering, RANSAC F
verification, and rectified diagnostics. The code comments and
[matching notes](../math/feature_matching.md) reference the local lecture by
PPTX slide/page order: correspondence on 137, epipolar lines on 138, rectification
on 139–140, and the existing K/homogeneous camera model. SIFT/RANSAC/Sampson are
explicit extensions beyond the deck's block-matching examples.

## Automated checks

The full Python suite passes **237 tests**, including 48 new M4 tests. Repository
Ruff lint and formatting checks pass. The new checks cover:

- Repeatable SIFT coordinates/descriptors, row ordering/alignment, grayscale/BGR
  agreement, feature limits, and shaped empty results for blank images.
- Hand-computable L2 ratio/mutual examples, ambiguous equal-distance descriptors,
  missing neighbors, empty inputs, and malformed configuration.
- Known 3D points projected through M3's K/external/3×4 camera model: the derived
  F satisfies the epipolar equation, including projective scale/sign changes.
- RANSAC on 240 noncoplanar observations with 60 injected wrong correspondences
  and 0.15-pixel Gaussian noise: at least 172 of 180 true matches retained, at most
  three injected mismatches accepted, repeated F/mask identical on the same build.
- Noiseless rectification aligns vertical coordinates to within 0.001 pixels,
  verifies the transformed F relation, and keeps at least 95% of support visible.
- End-to-end generated textured views at varying depths, all three output pairs,
  exported feature IDs/masks, explicit selection from five images, calibrated
  distortion correction and resized K, stored JPEG orientation, featureless
  failure reports, removal of stale successful previews, and rejection of raw
  output destinations including symlink aliases. Capture hashes are unchanged.

Tests use original generated fixtures and do not require private photos or the
lecture deck. The configured random seed ensures serial repeatability on this
OpenCV build, not identical results across every library release.

## measurement01

Command from the repository root:

```bash
.venv/bin/python python/scripts/match_session.py data/raw/measurement01 \
    --output data/processed/measurement01/m4
```

The run explicitly selects recorded images **0, 1, 2** from the five-image
session. Stored images remain 4096×3072 with EXIF rotation ignored; SIFT and F
operate at **1600×1200**. Source intrinsics remain **metadata-derived
(approximate)**, with unknown distortion assumed zero. The working K is:

```text
[[1056.78013,    0,        799.5],
 [   0,      1056.78013,   599.5],
 [   0,         0,           1  ]]
```

Feature counts are 2437, 3252, and 3388. Default settings are max 6000 features,
ratio 0.75, mutual nearest enabled, RANSAC/Sampson threshold 1.5 working pixels,
and seed 0, using OpenCV 5.0.0 and NumPy 2.5.4.

| Pair | Raw nearest | Ratio retained | Mutual retained | Geometric inliers | Geometric outliers | Rectified vertical median / p95 (pixels) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 0–1 | 2437 | 121 | 88 | 49 | 39 | 0.528 / 2.100 |
| 1–2 | 3252 | 312 | 254 | 169 | 85 | 0.466 / 1.405 |
| 0–2 | 2437 | 195 | 159 | 100 | 59 | 0.528 / 1.602 |

Repeated runs produced the same feature and match counts. All three pairs
produced rectified previews, and all three previews were visually inspected.
The 0–1 initial unadjusted warp clipped most inliers; the viewport correction
preserves horizontal alignment and makes 48 of its 49 accepted matches visible.
The other views draw deterministic samples of up to 60 lines per class.

Generated JSON, NPZ, and six JPEG diagnostics remain under ignored
`data/processed/measurement01/m4/`. Neither raw captures nor lecture assets are
committed. Output pixel errors are affected by rectification's projective scale;
they are not calibration errors or distance accuracy. Repeated scene patterns
can still yield incorrect matches along valid epipolar lines. Relative pose,
cheirality, and 3D reconstruction have not yet been established by M4.
