# Run M6 and load a persistent scene

From the repository root with the Python environment active:

```bash
python python/scripts/reconstruct_scene.py \
    data/processed/measurement01/m4 data/processed/measurement01/m5 \
    --output data/processed/measurement01/m6
```

Generate [M4](feature_matching.md) and [M5](relative_pose.md) first. Options are
`--max-reprojection-error-px 3`, `--min-angle-degrees 1`, and `--solver svd`
or `--solver eigh`. Thresholds use M4's ideal working pixels and ray angles.

The command requires a complete three-camera M5 chain. It checks session/image
and calibration agreement, derived M/O/T_scene matrices, original match indices,
M4 masks, M5 mask alignment/subsets, pair counts and artifact paths. This detects
incompatible exports; the older M5 format does not contain feature-file content
checksums, so it cannot prove that coordinate files were never edited. Regenerate
M4/M5 after changing capture/calibration/features. M6 reads inputs without
modifying them and rejects output destinations under repository `data/raw/`,
M4 or M5 directories, including symlink aliases.

## Outputs and persistence

| File | Contents |
| --- | --- |
| `scene.json` | Versioned camera/point/track/observation objects, settings, lecture pages, calibration records, baseline conventions and inherited geometry warnings |
| `triangulation.json` | Summary counts, rejection reasons, reprojection RMSE, per-track error/angle diagnostics and all rejected correspondence unions |

The scene schema has `schema_version=1` and
`coordinate_frame="undistorted_working_pixels_ignore_exif"`.

| Scene object | Fields |
| --- | --- |
| Camera | Recorded `image_id`, working intrinsics including K/source/assumptions, world-to-camera `R_cw`, world center `C_w` |
| Track | `track_id`, ordered observations, accepted `point_id` or null, `rejection_reason` or null |
| Observation | `image_id`, original `feature_id`, ideal working `pixel=(u,v)`, available `reprojection_error_px` |
| Point | `point_id`, reciprocal `track_id`, world `position=(X,Y,Z)`, best acute triangulation angle |
| Metadata | Selected images, world-to-camera convention, scale/quality status, pose closure, warnings, original image/calibration records, settings and lecture references |

Accepted points use their track ID as point ID, so rejected tracks leave gaps.
IDs are identifiers, not indices into a dense array. Each track has at least two
distinct images, and no `(image_id,feature_id)` can belong to multiple tracks.
Point/track references must be reciprocal. Rejected tracks retain observations
and have no 3D point. Missing reprojection errors use null; JSON disallows
nonfinite values.

```python
from distance_measuring.scene import load_scene, save_scene

scene = load_scene("data/processed/measurement01/m6/scene.json")
print(len(scene.cameras), len(scene.points), scene.metadata["scale_status"])
print(scene.cameras[0].M)  # Full 3x4 K[R|-R*C], derived without duplicated JSON.
save_scene("data/processed/measurement01/m6/scene_copy.json", scene)
```

Loading validates finite coordinates, proper camera parameters, unique IDs,
one-feature-per-image tracks, camera references and reciprocal point references.
Serialization preserves floating-point values and exact integer IDs/timestamps.
The generic `save_scene` writes the caller's chosen path; the reconstruction
command enforces the raw/input-directory protections above.

Exit **0** means at least one point was accepted and files were produced; inspect
scale/quality warnings before interpreting it. Exit **1** means no points were
accepted; an empty current scene and diagnostics are still saved. Exit **2**
means invalid input/options or an I/O error. Files with the same names are replaced
on rerun, including replacement of an older nonempty scene by an empty result.

See [triangulation math and lecture pages](../math/sparse_reconstruction.md) and
[validation results](../experiments/m6_reconstruction_validation.md). M6 does not
resolve relative baseline scales, optimize poses, or establish physical distance.
