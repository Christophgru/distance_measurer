# Abort reconstruction when selected images do not connect

All selected images must belong to one connected graph of verified overlaps.
An edge connects two images with sufficient geometrically supported matches.
Every pair need not overlap: `0–1–2` is valid without an edge `0–2`. An isolated
image, or two disconnected groups of images, raises `DisconnectedOverlapError`
with the component image IDs. The pipeline never fills a missing visual pose
using compass or gyro orientation.

The gate runs at these stages:

- Correspondence audit: at least eight spatially distinct matches passing both
  pairwise geometry and patch correlation per edge. Failure returns a nonzero
  CLI status. JSON/HTML matching diagnostics remain available to inspect the
  failure; their quality status is `disconnected_overlap`.
- Audited-pair reconstruction: connected candidate pairs before pose estimation,
  then connected verified poses with at least eight pose inliers per edge.
  PnP still requires sufficient shared 3D points to initialize the remaining
  camera. Graph connectivity alone does not guarantee reconstructible geometry.
- Legacy M5 pose estimation: the verified pose graph must connect before camera
  export. A disconnected run removes a stale successful pose report and exits
  with an error.
- Heading preparation: at least eight reconstructed shared points per edge,
  using only observations whose reprojection error is at most three pixels.
  An isolated selected camera aborts before orientation fallback or new export.

Lecture 002 **p. 137** motivates corresponding physical points; graph validation
is an additional implementation requirement. Automatic correlation/geometry
checks can accept repeated patterns, so graph connectivity does not establish
semantic identity. The interactive point review remains useful.

Tests exercise a chain, an isolated image, two disconnected groups, missing pose
support, a textured matching pair with a blank third image, and heading/pose CLI
failures. Raw recordings and input scenes remain unchanged.
