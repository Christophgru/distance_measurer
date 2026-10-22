# Matching before camera alignment

Lecture references use one-based PPTX slide order in local lecture 002. These
notes extend [the original matching notes](feature_matching.md) and preserve
their K/pixel conventions. **Two overlapping images are sufficient** for a
correspondence; a third view supplies an optional additional consistency check.
Neither a camera pose nor a physical scale is an input to patch correlation.

## Descriptor candidates and physical locations

The original matcher tests descriptor ambiguity in the forward direction and
mutual nearest identity in reverse. The new audit tests the ratio in **both**
directions. It additionally uses synthetic affine views to handle foreshortening
and square-root histogram normalization. These are extensions beyond the
lecture, based on [Yu and Morel's ASIFT description](https://www.ipol.im/pub/art/2011/my-asift/)
and [Arandjelovic and Zisserman's RootSIFT, section 3](https://www.robots.ox.ac.uk/~vgg/publications/2012/Arandjelovic12/arandjelovic12.pdf).
The implementation uses OpenCV's affine-feature wrapper around SIFT.

Multiple orientations or simulated views can detect the same image location.
Treating each as an independent physical point inflates geometric support.
Detections within 1.5 working pixels form connected groups; a group is discarded
if any member is more than 2 pixels from its median. This prevents a chain of
nearby detections from becoming one diffuse point. Each group's descriptor
variants remain available. The ratio test's second neighbor must belong to a
**different location group**; missing a distinct second neighbor rejects that
descriptor conservatively. Bidirectional proposals are assigned one-to-one by
quality, not claimed to solve an optimal assignment problem.

## Pairwise photometric checks from the lecture

**Pages 141 and 143** describe sliding-window matching and normalized correlation.
For vectorized patches A and B, the implemented zero-mean normalized correlation
is:

```text
             sum((A - mean(A)) * (B - mean(B)))
NCC = --------------------------------------------------
      sqrt(sum((A - mean(A))²) * sum((B - mean(B))²))
```

This is invariant to a positive brightness gain and additive offset in the ideal
unclipped case. **Page 144** motivates explicitly rejecting textureless patches:
constant data cannot establish a unique correspondence. The implementation also
rejects competing correlation peaks with similar scores. Defaults are minimum
NCC **0.7**, peak margin **0.05**, and a two-pixel suppression radius around the
best peak. Search/template shapes and finite values are validated.

The independent APIs are:

```python
from distance_measuring.vision.correlation import correlate_patch, correlate_local_point

# Only two grayscale regions are required. Peak top_left is in search-region pixels.
peak = correlate_patch(template_region, search_region)

# Refine/check an approximate point pair, assuming local translation by default.
result = correlate_local_point(image_a, image_b, pixel_a, approximate_pixel_b)
```

Strong perspective changes require local appearance normalization. In the audit,
each query uses up to 30 nearby support matches from **the same image pair**,
excluding the query and locations within 4 pixels. At least six RANSAC-consistent
neighbors support a local homography. The second image is sampled through this
homography around the candidate; a 21×21 patch searches within ±5 first-image
pixels. This local normalization is our extension, not prescribed on the slides.
The homography is an image mapping, not the lecture's 3×4 camera matrix.

The query is held out of its own homography fit. Correlation proposes a refined
second-image location; a shift over 3 working pixels rejects the candidate.
Refined positions are exported as diagnostics and do not silently replace the
original feature coordinates. Missing support or a nonplanar neighborhood leaves
the correlation **unverified**. It is not proof that the correspondence is false.
The direct patch/local-point APIs remain usable without a neighborhood model.

## Geometric checks and optional cycles

**Page 138** supplies the epipolar constraint. The existing F/RANSAC/Sampson
estimator checks each image pair independently, at 1.5 working pixels. A usable
pairwise candidate passes both epipolar and correlation checks; nearby candidates
within 4 pixels in either image do not count as independent support. Full masks,
ratios, correlation results and duplicate associations remain inspectable.

For three selected images, an optional track must satisfy exact group-ID equality
between A→B→C and direct A→C. Open paths and contradictory cycles are counted.
Nearby closed tracks are deduplicated. This extends **pp. 136–138**, which require
observations of the same physical scene point. A match seen in only two images
remains usable; it does not need to close a three-view cycle.

All of these are consistency tests. Repeated texture can still produce a wrong
match that passes them. Small residuals on one planar monitor region do not prove
correct camera motion, nonplanar scene support, or metric accuracy. The local
review page therefore shows unwarped image context and corresponding crops,
supports explicit review decisions, and binds exports to the input hashes and
matching report. Camera alignment and scale remain separate steps.
