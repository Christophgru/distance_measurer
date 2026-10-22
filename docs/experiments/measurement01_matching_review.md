# measurement01 correspondence review

The poor overlays led to a correspondence-first audit. Camera poses, scale and
the M9 accuracy gate are unchanged. Point matching is evaluated in image pixels
without importing the old poses. **Two overlapping photos are sufficient.**

## Baseline problem

The original M4 pairwise F masks close **19** three-view feature-ID cycles.
Applying the new four-pixel physical-location deduplication leaves **14** tracks,
concentrated in one small monitor region. Multiple SIFT orientation variants had
inflated the independent point count. Such localized, largely planar support
cannot validate the desk/ruler geometry or explain camera motion reliably.

Increasing extraction to a 3200-pixel longest edge with ordinary RootSIFT and
bidirectional ratio 0.75 found only two closed desk-region tracks in the initial
probe. A limited affine-view simulation recovered ruler/keyboard observations
under the much stronger viewpoint change. Early exploratory counts included
diffuse and nearby duplicate detections; the final results below use the stricter
localization, correlation and spatial deduplication rules.

## Final pairwise results

Run the commands in the [matching guide](../../tools/validation/README.md).
The reviewed recording uses stored-orientation 1600×1200 working pixels,
metadata-derived approximate K and unknown distortion assumed zero. Display
rotation is applied only after all pixel geometry and matching computations.
Manual search regions exclude the upper monitor-dominated areas and retain the
desk, ruler and laptop. This is an explicit recording-specific mask, not an
automatic screen detector or a set of manually supplied point correspondences.

| Pair | Bidirectional descriptor matches | F inliers | Also pass local correlation | Spatially distinct passing matches |
| --- | ---: | ---: | ---: | ---: |
| 0–1 | 94 | 68 | 32 | **28** |
| 0–2 | 261 | 179 | 122 | **113** |
| 1–2 | 288 | 208 | 146 | **135** |

The final **276 pairwise candidates** pass both epipolar and local correlation
checks, with four-pixel separation within each pair. They are not 276 distinct
3D points: a physical location can occur in more than one image pair.
The genuinely two-image run on images 0 and 1 exports the same **28** passing
matches, an empty optional three-view track list and `third_view_required=false`.
This does not depend on the third photo being visible or matched.

All three views additionally close 13 localized, deduplicated cycles, of which
six pass all three independently fitted F masks. These are supplementary
diagnostics; their availability does not gate valid pairwise candidates.

RootSIFT ratios must pass in both directions. Affine/orientation duplicates are
grouped by position, diffuse groups are rejected, and no physical group receives
multiple partners within a pair. Final epipolar masks use the existing 1.5-pixel
Sampson threshold. Local correlation requires NCC ≥0.7 and adequate peak
separation. Each query is excluded from its local homography fit; shifts over
three pixels are rejected. See [math and lecture references](../math/correspondence_consistency.md).

## Review artifacts and verification

Local generated artifacts:

- `data/processed/measurement01/pairwise_matching/review.html`: only images 0/1.
- `data/processed/measurement01/matching_review/review.html`: independent pair
  selectors and optional three-view bonus tracks.
- `match_cards_*.jpg`, `tracks_overview.jpg` and `correspondences.json` in those
  directories: point crops, context, raw checks, scores and rejection reasons.

Ruler-number/logo and keyboard/cable candidate crops were visually inspected.
This is a spot check, not exhaustive manual ground truth. All records remain
`manual_review=unreviewed` until explicitly marked in the browser. The review
page offers positive/uncertain decisions and a source-bound JSON export.

The full existing/new Python suite passes **409 tests**. New tests cover known
RootSIFT values, duplicate grouping and diffuse chains, reverse ambiguity,
cycle contradictions and duplicate counts, known correlation peaks under gain
and offset, flat-patch rejection, repeated-pattern ambiguity, oblique patch
normalization, held-out queries, and a real two-photo synthetic CLI run. A blank
pair produces no fabricated accepted points. Source capture hashes remain
unchanged. Repository Ruff checks pass.

A headless Chromium smoke test opens the actual local reports and verifies
two/three-image layouts, pair/cycle selectors, accepted-candidate filters,
navigation, decision persistence, clear/reject controls and review download.
There are no JavaScript errors or external network requests. A screenshot stays
local as `matching_review/review_browser_smoke.png`.

Code comments retain lecture 002 references to **pp. 136–138** for scene-point
correspondence and epipolar geometry, and **pp. 141–144** for sliding-window
normalized correlation and textureless-region limitations. ASIFT, RootSIFT,
grouping, cycles, local image homographies and the review UI are identified as
extensions. No lecture material or captured imagery is committed.

The candidate set is now easier to inspect and supplies checked two-view
constraints beyond the monitor. It still does not establish correct global
camera poses, nonplanar support or physical scale. Pose estimation should be
revisited using reviewed correspondences before any further distance claim.
