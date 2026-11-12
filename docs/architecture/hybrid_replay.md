# Hybrid preview replay and still reconstruction (P9.3)

For the full path from a new recording to an exported and running browser UI,
follow [Run a recording and open the web UI](run_dataset.md). Reconstruction
alone creates the scene and reports; the HTML export is a separate step.

Run the existing incremental command on a P9 capture:

```bash
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 .venv/bin/python \
  python/scripts/reconstruct_incremental.py \
  data/raw/2026-10-27_153337_135_48fb0956
```

`--tracking-source auto` selects hybrid replay when `tracking.json` exists;
otherwise it uses the existing still-only P8 path. Explicit `hybrid` requires
saved frames; `stills` retains direct JPEG tracking. An unavailable or empty
hybrid sidecar produces an explained error rather than a silent fallback.
Default output is `data/processed/<session>/prototype_hybrid_p9/`. Existing
output directories are rejected. Selection and exit codes follow the
[P8 command](incremental_command.md): 0 for all selected JPEG cameras, 1 for
geometric failure or partial reconstruction, 2 for invalid input/stage errors.

## Image evidence and coordinate models

`vision/hybrid_tracking.py` runs the existing P2 `TemporalTracker` through every
saved grayscale preview. The recorded native crop/subsample intrinsics apply
to each PNG. Unsupported clocks/intrinsics, recorded resets, geometry changes,
complete loss of tracks and gaps over `--max-preview-gap-s` (default 0.25 s)
split segments. New segments reserve new track IDs. A short missing-frame gap
can continue only if ordinary image tracking survives it; larger gaps cannot
inherit identities. The replay exports its own continuity statistics separately
from the phone's recorded live estimates.

For each JPEG, both previews bracketing the **actual exposure** are required,
on the declared common camera/sensor clock and within one segment. Request or
callback time never substitutes for exposure. Tracks present in both previews
are independently matched into the actual JPEG using P2 forward/backward LK.
The gyro rotation over each preview-to-exposure interval and the two streams'
intrinsics provide an optional search warp. Reverse intervals use inverse
rotation. A plain LK attempt remains available; image checks decide support.
No missing gyro is replaced by identity except a measured zero-length interval.

The JPEG is resized and undistorted with its **own** camera model to the
preview analysis dimensions. This does not assume matching fields of view:
the search homography uses `K_jpeg_analysis R K_preview^-1`. The two measured
JPEG pixels must each pass forward/backward checks and agree within
`--cross-stream-agreement-px` (default 1.5 **working JPEG** pixels). Round-trip
errors are expressed in that same pixel basis. Their mean becomes the JPEG
observation. Pixel-center resize affines map these measured JPEG coordinates
to the JPEG working resolution (default long edge 1600). They never substitute
scaled preview coordinates for measurements. `--calibration` supplies the
JPEG model; preview calibration remains the separately recorded sidecar model.
Approximate intrinsics retain their original warnings.

At least eight dual-preview observations make a JPEG eligible for subsequent
geometry. Eligibility establishes 2D support, not a pose. P3 uses those JPEG
observations and actual exposure-to-exposure gyro intervals; P4 performs its
existing essential-matrix/cheirality/parallax bootstrap. P5 registers later
JPEGs with the existing robust 3D-to-2D PnP and guided matching, and P6 performs
the existing bounded local BA/confidence updates. Cameras require at least
eight PnP inliers. Only the bootstrap's preview segment can extend that map.
Unsupported JPEGs and other segments remain explicitly unregistered.

## Outputs and limitations

The P2-compatible `tracking/` directory contains actual JPEG observations,
pair masks and diagnostic overlays. `tracking.json` additionally records each
preview's segment/continuity, both cross-stream checks per JPEG, gyro search
priors, separate intrinsics, timings and reserved preview track IDs. The root
`REPORT.md` and `reconstruction.json` consolidate these with P3–P6 support,
quality and costs. SHA-256 provenance includes both sidecars and every preview
PNG as well as the original still/sensor inputs; inputs are verified again
before exporting the root scene. Raw recordings are unchanged.

Capture diagnostics distinguish request intervals, exposure intervals,
request-to-exposure and request-to-callback latency, skipped timer ticks,
preview gaps during and outside JPEG request windows, and per-JPEG overlapping
gaps. Window overlap is a timing association, not proof that JPEG capture
caused a pause. Unknown camera clocks are excluded from these comparisons.
Recorded live errors/timings and offline replay statistics are separate.

The scene contains reconstructed JPEG cameras and landmarks. Preview poses,
dense mapping, independent bootstraps for later segments and relocalization are
outside this step. Registration remains forward-only, so cameras preceding a
later bootstrap are reported as missing. Changing displays, rolling shutter,
motion blur and approximate calibration can still reject observations or poses.
Low residuals on accepted observations do not establish metric accuracy.

Synthetic tests cover nonplanar translation, rotation-only rejection, different
crops/resolutions, request/exposure offsets, inverse gyro intervals, missing
frames/capture pauses, geometry resets, unsupported clocks and absent JPEG
image support. Existing P8 tests exercise still-only compatibility. Physical
capture evidence and native instrumentation results remain distinct P9.4
acceptance items.

The first refinement pass prepares grayscale float32 images once per patch-search
batch instead of rescanning/converting full images for every landmark. It batches
the existing camera projections for residual/confidence measurements, reuses
landmark-quality calculations without constructing temporary scenes, assigns
the existing contiguous BA point-parameter block in one operation, and computes
the BA report's observed-point set once. A duplicate hybrid input-validation pass
is removed; validation still runs and errors still abort. Search radii, candidate
budgets, PnP gates, confidence thresholds, BA objective/solver/gauge and global
acceptance checks are unchanged. These optimizations reduce repeated work;
they do not add poses for the existing unsupported views.
