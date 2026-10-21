# Viewing calibrated camera geometry

These original notes preserve the [camera model](camera_model.md),
[triangulation](sparse_reconstruction.md) and [bundle adjustment](bundle_adjustment.md)
references. Lecture pages are **1-based PPTX slide order** in local
`002 - Image Formation and Acquisition.pptx`. The deck and extracted references
remain ignored under `local/lecture/`.

| Lecture page | Viewer connection |
| --- | --- |
| 65–68 | Homogeneous pixel coordinates and projective scale |
| 70–76 | K maps normalized camera rays to pixel samples |
| 81–83 | Camera center C, world-to-camera R and full 3×4 M=K[R|-R C] |
| 133–134 | Optical rays, camera centers and virtual camera views |

The viewer inverts the existing camera model to draw frustums. For each corner
of the ideal working image, append one and apply K inverse:

```text
m_corner = [u,v,1]^T
q_corner = K^-1 m_corner = [x,y,1]^T
P_corner_camera = drawing_depth * q_corner
P_corner_world = R_cw^T P_corner_camera + C_w
```

Corners lie at image **outer edges** `u=-0.5,width-0.5` and
`v=-0.5,height-0.5`, surrounding the zero-based pixel centers used by M3–M7.
This accounts for aspect ratio, unequal fx/fy, principal-point offset and skew.
It does not assume a centered symmetric field of view. Reprojecting the four
world corners through the camera's 3×4 M gives those original pixel edges.
No EXIF orientation, rectification homography or distortion is applied: the
stored scene describes ideal working pixels.

The frustum's vertex is the camera center C, not the origin translation column
O=-R C. The arrow follows world direction `R_cw^T [0,0,1]^T`, which is the forward
camera optical axis. Its arrowhead wings use world camera-right direction
`R_cw^T [1,0,0]^T`. Synthetic tests verify corner reprojection, positive camera
depth, and the forward-axis transform with rotated cameras.

Frustum depth is a **chosen drawing length**, not a scene measurement. By default
it is 12% of the largest coordinate span among points and camera centers, with a
small nonzero fallback for degenerate bounds. `--frustum-depth` overrides it.
All points remain in the display; there is no hidden downsampling, recentering,
reconstruction change or outlier removal. A camera-only preview panel keeps the
three frames distinguishable when the full point cloud spans a larger region.

## Stable IDs and renderer arrays

The Open3D point-cloud rows follow `scene.points` order. `SceneView.point_ids`
and the adapter's explicit returned tuple map those rows to persistent IDs.
IDs may have gaps and must not be treated as row offsets. Selection resolves IDs
through a dictionary and retains the user's A/B order even when the scene array
is reordered. Camera colors follow sorted recorded image IDs, independent of
camera array order.

Desktop rendering uses [Open3D's visualizer](https://www.open3d.org/docs/release/python_api/open3d.visualization.Visualizer.html)
and [geometry display API](https://www.open3d.org/docs/release/tutorial/visualization/visualization.html).
The camera geometry, colors, point highlighting, selection schema and headless
preview are project-specific extensions. M8 uses the explicitly permitted CLI
point-ID selection fallback; it does not implement mouse-based point picking.
The Open3D window supports navigation, and the PNG uses the same world geometry.

Selection artifacts include persistent point/track/feature IDs, coordinates and
the SHA-256 of the exact source scene. A later consumer should verify that hash
against the scene being measured. Coordinates remain reconstruction units;
viewing or highlighting does not supply metric calibration or establish geometry
accuracy. Source scale/quality statuses and warning text remain visible or saved.
