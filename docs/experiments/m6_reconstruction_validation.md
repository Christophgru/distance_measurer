# M6 sparse reconstruction verification

M6 builds conflict-aware multiview tracks and triangulates them with the lecture's
K/R/C/3×4 model. Code comments reference local lecture 002 pages 65–76, 82–83,
98/101 and 132–138, using PPTX slide order. The normalized linear equations,
SVD/eigenvector choices and filtering limits are described in
[original reconstruction notes](../math/sparse_reconstruction.md). All earlier
lecture references remain; the deck and extracted material stay local/ignored.

## Automated checks

The full Python suite passes **330 tests**, including **54 new M6 tests**.
Repository Ruff lint and formatting checks pass. Fixtures are original generated
data and require neither the private capture nor the lecture deck.

- Tracks merge consistent triangles and preserve independent components.
  Duplicate edges are counted; same-image edges and contradictory unions are
  rejected before mutation. Shuffled/reversed edges produce identical tracks,
  conflict decisions and IDs, with lower-error edges winning conflicts.
- Known points with different K/skew/rotations reconstruct from two or three
  views within `1e-8` world units using either SVD or minimum eigenvectors.
  The two-view result agrees with an independent OpenCV full-M triangulation.
- Rotating/translating the world, using large coordinate offsets and changing
  length units preserves reconstructed geometry within `1e-6` absolute units.
  With 0.2-pixel synthetic noise, point error remains below 0.04 scene units and
  individual reprojection error below 0.9 pixels for the seeded example.
- Points behind a camera, a contradictory third view, nearly parallel and
  antiparallel rays, coincident cameras and points at infinity fail explicitly.
- Scene JSON round-trips camera parameters/M, points, observations, rejected
  tracks, nested metadata and large integer timestamps without meaningful loss.
  Broken references, duplicate observations/IDs, invalid frames and nonfinite
  coordinates/metadata are rejected.
- End-to-end M4/M5 fixtures with image IDs 4,7,9 retain full match-row identity,
  reconstruct 27 known points (24 three-view, 3 two-view), and preserve input
  hashes. Conflicts and rejected tracks are exported. Empty reruns replace old
  scenes, incompatible inputs/options fail, raw/source output aliases are blocked,
  and inherited pose warnings remain in stdout and the saved scene.

## measurement01 diagnostic reconstruction

```bash
.venv/bin/python python/scripts/reconstruct_scene.py \
    data/processed/measurement01/m4 data/processed/measurement01/m5 \
    --output data/processed/measurement01/m6
```

Default settings are SVD, maximum reprojection error 3 working pixels in every
observed image, and minimum best acute intersection angle 1°. The run uses M4's
1600×1200 ideal coordinates, M5 pose-inlier masks and its three camera frames.
Calibration remains **metadata-derived (approximate)**.

| Diagnostic | Result |
| --- | ---: |
| Accepted M5 pair edges | 283 |
| Tracks | 238 |
| Two-view / three-view tracks before triangulation | 212 / 26 |
| Consistent redundant edges | 19 |
| Rejected conflicting unions | 0 |
| Accepted 3D points | 206 |
| Accepted two-view / three-view points | 197 / 9 |
| Rejected tracks | 32, all above the reprojection threshold |
| Accepted observations | 421 |
| Euclidean observation reprojection RMSE | 0.86425 working pixels |

Both generated files remain ignored under `data/processed/measurement01/m6/`.
The final run reproduced these counts, loaded the saved scene successfully and
verified unchanged SHA-256 hashes for all 26 raw/M4/M5 input files.
The scene retains all 238 tracks; 32 have no point and keep their rejection reason
and observations. Accepted point IDs match their track IDs, with gaps.

**These points are diagnostic fits to M5's supplied cameras.** The scene retains
`quality_status="rotation_loop_inconsistent"`, the approximately 17° rotation
loop discrepancy and `scale_status="independent_unit_baselines_relative_scales_unresolved"`.
M6 has not resolved the relative baseline ratio or repaired the camera rotations.
The low error is conditional on retained tracks and supplied poses, and many
points have only two-view support. It does not verify a consistent three-view
scene or physical measurements. Rejected observations are not silently removed
to make three-view tracks appear valid. Subsequent refinement must retain these
limits and investigate calibration/correspondence/pose quality.
