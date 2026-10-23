# Browser reconstruction viewer

The Python exporter embeds this renderer, its dependencies and the scene into
one offline HTML file. Normal use needs a WebGL 2 browser, not Node, a Python
GUI backend or a running application server. See
[viewer usage](../../docs/architecture/scene_viewer.md).

Development dependencies are pinned in `package-lock.json`. After changing
`app.js`, rebuild the committed bundle:

```bash
cd tools/scene_viewer
npm ci
npm run build
```

The output is
`python/src/distance_measuring/visualization/web_assets/viewer.js`. The exporter
also includes the MIT license from `THREE-LICENSE.txt` in each HTML artifact.
The build also produces `dense_worker.js`, bundling the Apache-licensed BSON
decoder. Large reference exports copy that worker, its license and bounded BSON
sidecars beside the HTML; see [visual reference usage](../../docs/architecture/visual_reference.md).
Those viewers use a local HTTP server. The smoke test serves only local assets
for BSON exports and retains the standalone file test for legacy exports.

For browser regression checks, first export a scene with at least two cameras
and one point, then run:

```bash
npx playwright install chromium
npx playwright install-deps chromium
node smoke.mjs /absolute/path/to/exported/index.html
# Optional explicit 2M BSON capacity fixture (temporary, synthetic data only):
node bson_throughput.mjs /absolute/path/to/exported/reference/index.html
```

The smoke test runs offline in Chromium with SwiftShader on headless hosts.
It checks actual GPU ID picking, stable-ID search, orbit/pan/zoom, filter counts,
camera focus/view, source photos and mobile layout. It also creates and removes
a **synthetic** 100,000-point fixture to verify batched rendering. This fixture
is separate from the user's reconstructed cloud. `browser_smoke.png` is saved
beside the supplied HTML. No particular GPU frame rate is claimed by this test.

The cloud uses one `THREE.Points` buffer, binary position/color/ID/observation
data and shader-side filters. Mouse picking uses a separate ID-color render
pass and a one-pixel readback. Camera geometry is small and separate. Rendering
is scheduled only on interaction, filter changes, image loads and resizing.

Primary references: [Points](https://threejs.org/docs/pages/Points.html),
[BufferGeometry](https://threejs.org/docs/pages/BufferGeometry.html) and
[OrbitControls](https://threejs.org/docs/pages/OrbitControls.html).
