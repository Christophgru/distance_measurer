# M9 stopped at the ruler accuracy gate

This records the original failed M7-based check. A later
[pairwise/acceleration retry](ruler_pairwise_acceleration_retry.md) passes the
ruler-reference geometry checks; independent inertial metric scale still fails.

M9 is **incomplete**. The added T9.1 requirement says to check the ruler's
centimetre scale and stop if deviation exceeds 10%. The measurement01 check
failed, so no measurement engine, metric scene scale or measurement CLI was
added. T9.1–T9.4 remain unchecked. Existing lecture references are preserved.

## Reproduction

With the existing editable Python installation and local M7 scene:

```bash
.venv/bin/python tools/validation/measurement01_ruler.py \
    data/processed/measurement01/m7/scene.json \
    > data/processed/measurement01/m9_validation/ruler_report.json
```

The output directory must already exist. **Exit status 1 is the expected failed
gate**, not a script crash. The script only reads the scene and prints JSON; it
does not change cameras, points, scale or raw files. The checked scene SHA-256 is
`79da76317d7e650fad248b813435dce5eb1bd456dd00ed583b96002ce2cf2cc2`.
The experiment is specific to this recording and its original working frame.

## Observations and method

Manually inspect the same far-edge intersections of the **5, 10, 15 and 20 cm**
ticks on the ruler's top face in recorded images **0, 1 and 2**. Do not mix
the folded lower sections or use the viewer's selected monitor features as ruler
endpoints. Local diagnostic crops and annotated images are ignored under
`data/processed/measurement01/m9_validation/`.

Inspection uses EXIF-upright images; triangulation uses the existing
stored-orientation, 1600×1200 working frame. An upright 1200×1600 pixel `(x,y)`
maps to working `(u,v)=(y,1199-x)`. The checked-in script records the converted
manual observations. Conversion precision does not imply subpixel tick
accuracy. The current approximate metadata-derived K and zero-distortion
assumption are retained; these observations receive no extra lens correction.

Test all three camera pairs and the complete three-view fit with the existing
triangulator. Its ordinary acceptance threshold is **3 working pixels in every
view**. All four ticks fail this threshold in every group.

For diagnosis only, repeat with a relaxed reprojection threshold to obtain the
rejected candidate coordinates. Use the 5–10 cm interval to define a temporary
centimetres-per-reconstruction-unit factor, then check the independent 10–15 and
15–20 cm intervals. This avoids checking the scale reference against itself.
No such temporary factor is applied to the scene or exported as a valid scale.

## Results

Every held-out interval should be **5 cm**. These lengths are exploratory fits
of rejected points, **not accepted measurements**:

| Image IDs | 10–15 cm fitted length | Deviation | 15–20 cm fitted length | Deviation | Reprojection error range |
| --- | --- | --- | --- | --- | --- |
| 0, 1 | 5.048 cm | 0.96% | 5.292 cm | 5.84% | 46.3–122.9 px |
| 0, 2 | 5.545 cm | **10.89%** | 5.965 cm | **19.31%** | 18.7–55.9 px |
| 1, 2 | 4.926 cm | 1.49% | 4.791 cm | 4.19% | 12.9–16.3 px |
| 0, 1, 2 | 5.105 cm | 2.11% | 5.288 cm | 5.76% | 14.8–151.5 px |

Values are approximate because tick placement is manual. The discrepancy is
also visible in the much larger-than-3-pixel reprojection errors. The favorable
length ratios of other groups cannot establish a pass: none supplies accepted
ruler points, and pair 0–2 exceeds the requested length limit. Averaging views
or selecting a favorable pair would hide this failure.

The ordinary SVD triangulation and minimum-eigenvector solve agree to less than
`1e-13` reconstruction units for these diagnostic candidates. Thus changing
between these two linear solvers does not resolve the observed discrepancy.

## Lecture connection and consequence

The diagnostic's code comments cite lecture 002, **1-based PPTX slide order**:

- **p. 36:** orthonormal Euclidean coordinates, used for 3D interval lengths.
- **pp. 81–83:** external R/C and full homogeneous 3×4 camera matrix
  `M=K[R|-RC]`, supplied by the existing camera/triangulation code.
- **p. 93:** known spacing as a calibration reference. Reserving separate
  intervals for validation is our experiment design, not a slide prescription.
- **pp. 98/101:** minimum eigenvector of a normal matrix, reused for the
  four-entry homogeneous triangulation problem rather than twelve camera terms.
- **pp. 133–136:** calibrated rays and approximate least-squares intersection.

M7's low residual on retained feature tracks did not validate this independent
ruler geometry. The check identifies inconsistent ruler observations and the
current camera model; it does not isolate the cause among calibration, poses,
correspondences or capture conditions. Reliable calibration/pose validation on
the ruler is needed before resuming M9. The existing reconstruction remains in
arbitrary units with unverified physical accuracy. No lecture material or image
data is committed.

Verification: the diagnostic completes with expected status 1, all sixteen
ordinary triangulations reject for high reprojection error, the SVD/eigenvector
comparison above passes, all **375 existing Python tests** pass, and repository
Ruff lint/format checks pass. The input scene's SHA-256 remains unchanged. No
measurement-engine behavior is claimed or tested as complete.
