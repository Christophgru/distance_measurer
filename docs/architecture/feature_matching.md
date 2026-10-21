# Run M4 on a recording

From the repository root with the Python environment active:

```bash
python python/scripts/match_session.py data/raw/measurement01 \
    --output data/processed/measurement01/m4
```

The default explicitly selects recorded image IDs **0, 1, 2**, even when a session
contains more images. It processes pairs 0–1, 1–2, 0–2. To choose another triple:

```bash
python python/scripts/match_session.py data/raw/measurement01 \
    --image-indices 1 3 4 --output data/processed/measurement01/m4_alternative
```

Options include `--calibration path/to/camera.yaml`, `--max-image-size 1600`,
`--max-features 6000`, `--ratio 0.75`, `--no-mutual`, `--threshold-px 1.5`, and
`--seed 0`. Calibration profiles must match the original stored image size and
camera ID under M3's rules; resizing K happens explicitly afterwards. The three
selected images must have equal working dimensions for this initial rectifier.
Raw captures are never written, and destinations inside `data/raw/`, inside the
session, or resolving there through a symlink are rejected.

## Outputs

| File | Contents |
| --- | --- |
| `matches.json` | Settings, software versions, image IDs/timestamps, original and working intrinsics, all pair counts/F matrices, failures, output names, rectified vertical error statistics |
| `image_NNN_features.npz` | `points` Nx2 ideal working coordinates; aligned `descriptors` Nx128 float32, `response`, `size`, `angle`, `octave` |
| `pair_NNN_NNN_matches.npz` | `indices` Mx2 feature IDs, L2 `distances`, `ratios`, final `inliers` boolean mask, `sampson_error_px`, verified `F` and successful `H_first,H_second` |
| `pair_NNN_NNN_matches.jpg` | Original ideal working views, with accepted/rejected match rows |
| `pair_NNN_NNN_rectified.jpg` | Projectively rectified views with accepted/rejected rows and horizontal guides |

For example, a pair's first coordinate array is
`features_first['points'][pair['indices'][:, 0]]`; the second uses column 1 and
the other image's features. Match indices identify **rows of the feature files**,
not raw JPEG pixels, track IDs, or indices into the RANSAC inlier subset. All NPZ
arrays load with `np.load(path, allow_pickle=False)`. A failed pair omits F/H
arrays when unavailable; failed geometry errors are infinity in NPZ. JSON uses
null matrices and explicit reasons rather than nonstandard NaN/infinity values.

Exit code **0** means all three pairs produced rectified diagnostics. **1** means
at least one pair lacked a verified F or rectification; the report and available
diagnostics are still written. **2** means invalid input/options or an I/O error.
Generated files with the same names are replaced on rerun; a previous rectified
preview for a failed current pair is removed. Use a separate output directory to
compare settings. `matches.json` defines the current run and selected images.

Green is epipolar-consistent and red is geometrically rejected **after** descriptor
filtering. A ratio-rejected descriptor has no pair row to plot. Diagnostics sample
up to 60 visible lines per class; NPZ retains every match. Both text and stdout
report counts, so a busy picture or a clipped rectification does not conceal the
number of accepted observations.

See [feature matching math](../math/feature_matching.md) for page-specific lecture
references, coordinate transforms, F/Sampson details and limits. This command
does not yet reconstruct 3D or recover physical distance.
