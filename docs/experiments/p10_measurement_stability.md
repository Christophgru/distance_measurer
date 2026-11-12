# P10.3 measurement stability experiment

The October 28 22:56 recording's free-3D ruler measurements are sensitive to
pixel placement. A conditional flat-surface variant is now implemented in
`distance_measuring.vision.planar_measurement`, with no new dependency and no
change to the default triangulation or measurement command.

The complete local result, scripts, plots and capture kit are under
`data/processed/2026-10-28_225648_021_a0d054e8/measurement_stability/`.
**The experimental mode is not promoted: physical accuracy remains unaccepted.**

## Model and safeguards

For two frozen visual cameras, source-frame rays satisfy `q.T X_source = 1`
on a common plane. The plane-induced transfer is
`H(q) = K_target (R + t q.T) inverse(K_source)`. Linear cross-product equations
initialize q; a robust symmetric pixel objective refines only its three
parameters. No known ruler lengths or annotated measurement endpoints fit the
plane or the cameras. Plane measurement is conditional on all endpoints lying
on that same surface.

The implementation requires at least 12 aligned texture matches, translation
between cameras and a full-rank system with condition number at most 10,000.
It rejects nonpositive depths. Query points intersect camera rays with the
plane, then fit all supplied views with two degrees of freedom on that plane.
Grazing rays below 5 degrees and point residuals above 3 pixels are rejected;
views are never silently dropped. These are experimental diagnostic gates,
not a product accuracy specification.

Held-out texture tests need at least eight correspondences and at least 80%
within 3 working pixels. Holding out a camera here means it is unused for the
plane fit; it was still part of the original visual reconstruction. These tests
are distinct from conditional ruler-length agreement on an imposed plane.

## Results and limits

After visually excluding the folded end, lower segments and bevel, 13 native
texture matches from JPEGs 0–1 fit the ruler face. The existing annotation
coordinates remain unchanged and unconfirmed. A single 10 cm reference in
group 0–1 sets the scale once, which group 0–14 also retains.

The 5 cm held-out interval changes from 5.28/4.34 cm under free triangulation to
**5.21/5.20 cm** under the shared-plane assumption. This is better conditional
repeatability, not independent verification of the plane. Only **14/23 (60.9%)**
unused JPEG-14 face matches meet the declared pixel gate. JPEG 4 has only four
face matches and missing tick annotations. The surface normal's 95th-percentile
bootstrap change is about **55.6 degrees**; independently fitting the later
face changes the reconstructed source reference length by **31.0%**.

An initial broad-ROI fit included different folded-ruler surfaces. Its numbers
are archived as `surface_measurements.mixed_surface.json`; they are not accepted
results. The final face mask was selected by inspecting the actual support
overlays, without tuning against physical length errors.

A separate background-only bundle-adjustment trial retains the eight cameras
outside the prior weak-PnP audit and 664 trusted background points. It excludes
39 points in an expanded ruler projection cone, using no ruler correspondences or
lengths. Refinement converges in 18 evaluations / 5.24 s, lowering the **same
subset's** reprojection RMSE from 0.7902 to 0.7305 px. Camera 0, the unit 0–1
baseline and all K matrices remain fixed. It does not solve measurement
geometry: the later surface check decreases to **13/23 (56.5%)**. This trial
stays a separate unverified scene and is not adopted.

The 200-trial sensitivity experiment resamples the frozen plane-fit consensus,
assumes ±0.5 native-pixel texture localization and ±2 native-pixel query-mark
localization, and refits the common plane and primary reference once per trial.
It shares query perturbations across overlapping groups. Its percentiles are
illustrative conditional sensitivity, not confidence/accuracy bounds; they
exclude camera/calibration error, wrong surfaces and systematic mark bias.

## Next physical evidence

The recording cannot isolate calibration error, pose error, correspondence bias
and weak surface support as one cause. The useful next test is a sharp, broad,
flat textured target with lateral motion and separately measured lengths.
The existing camera-calibration workflow can also replace metadata-derived K
and assumed zero distortion with a measured profile.

An A4 checkerboard PDF/SVG and a two-session protocol are prepared in
`measurement_stability/capture_kit/`. Its synthetic render detects all 54 inner
corners and the PDF has the intended physical page dimensions. This validates
the generated aid, not the physical print or phone calibration. Use the current
capture APK; no ARCore or replacement SLAM SDK is involved.

## Tests

All **69 focused tests pass**, covering the new plane geometry, exact world
coordinates with rotated/non-origin cameras, world similarities, weak-depth
noise, nonplanar endpoint rejection, unused-view failure, existing measurement
guards, triangulation, bundle adjustment and CLI input protection. The original
raw files, original scene and saved annotations remain unchanged.
