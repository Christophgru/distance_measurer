# Fast camera-pose prototype: implementation reuse map

P0.1 inventory, inspected 2026-10-25. Python paths below are relative to
`python/src/distance_measuring/`; dotted names are importable from that package.
`[R]` means reuse the existing implementation. This inventory changes no geometry
or capture implementations. The [P0.2 baseline](../experiments/prototype_baseline.md)
fixes a recorded dataset and comparison command.

## Existing building blocks

| Capability | Location and callable interface | Contract / reuse notes |
| --- | --- | --- |
| [R] Session loading | `dataset/loader.py`: `load_session(path, *, validate=True, max_sensor_gap_s=0.25) -> Session` | Reads `session.json`, `events.jsonl`, `sensors.csv`; validates by default and decodes images. `dataset/schema.py`: `Session.images`, `.sensor_streams`, `.image_path(event)`, `.start_ns`, `.duration_s`. Images follow event order, not filename sorting. |
| [R] Timestamp validation | `dataset/validation.py`: `validate_session(session, *, max_sensor_gap_s=0.25) -> ValidationReport`; `.raise_for_errors()` | Checks image ordering, sensor ordering, clock domains and per-stream bracketing coverage; warns about request-time photos and absent exposure times. `dataset/inspection.py`: `summarize_session(session, report)`; CLI `python/scripts/inspect_session.py SESSION --json`. |
| [R] Intrinsics / undistortion | `camera/intrinsics.py`: `resolve_intrinsics(metadata, *, calibration_path=..., ...)`, `load_intrinsics(path)`, `CameraIntrinsics` | Prefer calibration, then approximate metadata/heuristics. Methods: `undistort_image(image)`, `undistort_points(pixels, *, normalized=False, ...)`, `normalize_pixels(pixels)`, `pixels_from_normalized(points)`, `without_distortion()`, `project(P_w, extrinsics=None, *, distort=True)`. Normalization alone does not remove distortion. |
| [R] Calibration / resize | `camera/calibration.py`: `calibrate_3d(...)`, `calibrate_planar_views(...)`, `calibrate_checkerboard_folder(...)`; `vision/pipeline.py`: `working_camera(camera, size) -> (camera, affine)` | Reuse the pixel-center resize affine for both K and points. CLI `python/scripts/inspect_camera.py SESSION`; calibration tools in `tools/calibration/`. |
| [R] Feature extraction | `vision/features.py`: `SiftBackend(max_features=6000, contrast_threshold=0.04).extract(image) -> Features` | Aligned arrays: `points`, `descriptors`, `response`, `size`, `angle`, `octave`; deterministic ordering on the same OpenCV build. `FeatureBackend` is the existing interface. |
| [R] Descriptor matching | `vision/matching.py`: `match_descriptors(first, second, *, ratio=0.75, mutual=True) -> DescriptorMatches` | L2 nearest neighbors, Lowe ratio and optional reverse check; retains feature-row IDs/counts. `vision/consistency.py`: `rootsift`, `group_features`, `match_grouped`, `closed_three_view_tracks` support audited/grouped correspondences. |
| [R] Fundamental / RANSAC | `vision/epipolar.py`: `estimate_fundamental(first, second, *, threshold_px=1.5, confidence=0.999, max_iterations=10000, seed=0) -> GeometricMatches`; `sampson_errors(F, first, second)` | Ideal undistorted pixels; input-row-aligned inlier mask and errors. `rectify_matches(...)` is for diagnostics, not required for sparse guided search. |
| [R] Essential / RANSAC | `vision/pose.py`: `estimate_essential(first, second, *, threshold_normalized=0.001, seed=0) -> EssentialEstimate` | Normalized ideal rays `(x/z, y/z)`; five-point RANSAC and optional linear consensus refit. `essential_manifold`, `estimate_essential_linear` already exist. |
| [R] Relative pose | `vision/pose.py`: `recover_relative_pose(first, second, estimate, *, min_parallax_degrees=0.25, max_reprojection_error_normalized=0.003, min_positive_fraction=0.6) -> RelativePose`; `chain_camera(first, relative, *, baseline=1.0)` | Explicit E decomposition/cheirality; returns `R`, unit `t_direction`, mask and parallax. Chaining independent unit baselines leaves their relative scales unresolved. |
| [R] Triangulation | `vision/triangulation.py`: `triangulate_point(pixels, intrinsics, poses, *, max_reprojection_error_px=3.0, min_angle_degrees=1.0, solver="svd") -> TriangulationResult` | Two or more ideal-pixel views, distortion-free K; positive-depth, per-view reprojection and best acute intersection-angle gates. Returns position, errors, angle or rejection reason. |
| [R] Tracks | `vision/tracks.py`: `MatchEdge((image_id, feature_id), (image_id, feature_id), score=0.0)`; `build_tracks(edges) -> TrackBuildResult` | Deterministic conflict-aware merging; `FeatureTrack` has stable ID and image/feature keys. Does not yet retain optical-flow error or age. |
| [R] Scene / persistence | `scene/model.py`: `SceneCamera`, `Observation`, `SceneTrack`, `ScenePoint`, `Scene`; `save_scene(path, scene)`, `load_scene(path)` | Tracks retain rejected observations/reasons; accepted points retain triangulation angle and observations retain reprojection error. `scene/storage.py` supports JSON/BSON. `camera/geometry.py`: `CameraExtrinsics(R_cw, C_w)`, `.world_to_camera`, `.projection_matrix(K)`. |
| [R] Reprojection / BA | `optimization/bundle.py`: `reprojection_residuals(scene) -> ndarray`; `bundle_adjust(scene, *, loss="soft_l1", f_scale=1.0, max_nfev=300, variable_camera_ids=None, variable_point_ids=None, observation_point_ids=None) -> BundleResult` | Residuals are predicted minus observed `(u,v)`, accepted observations only. Existing SciPy sparse least squares retains fixed K, exact first camera and fixed first-baseline length. Defaults refine the whole scene; P6's `optimization/local_bundle.py:local_bundle_adjust` selects local variables/constraints and additionally guards selected/full-scene RMSE. |
| [R] Incremental PnP | `vision/pairwise_scene.py`: `rebuild_pairwise_scene(audit, *, refine_incremental=False, seed_strategy="support", allow_unregistered=False) -> PairwiseSceneResult`; shared `vision/pnp.py:estimate_pnp(world, pixels, camera, *, config=...)` | Seeds a pair, triangulates tracks, registers cameras with existing OpenCV RANSAC/LM and optionally runs BA. P5 extracted the shared calibrated PnP helper for temporal tracks. The audit path still accepts the dictionary from `tools/validation/audit_correspondences.py`; P6 can confidence-filter temporal PnP inputs. |
| [R] Projection / bounded patch search | `CameraIntrinsics.project`; `vision/correlation.py`: `correlate_patch(template, search, *, min_score=0.7, min_margin=0.05, peak_radius=2)`; `correlate_local_point(first, second, first_pixel, second_pixel, *, homography=None, patch_size=21, search_radius=5)` | Existing normalized correlation has texture/ambiguity rejection and bounded local search. Adapt it around projected landmarks; it currently starts from an approximate image pair match. |
| [R] Gyro / orientation | `inertial/experimental.py`: `gyro_orientation_interpolator(times, angular_velocity, initial_orientation)`; `orientation_interpolator(times, quaternions)` | Seconds on a common relative timeline; device-frame gyro rad/s, midpoint right-composed rotation increments and SciPy Slerp. No need to implement another integrator. `imu/` is only a placeholder; actual utilities live under `inertial/`. |
| [R] Device-to-camera convention | `inertial/experimental.py`: `back_camera_from_device(sensor_orientation_degrees)`; `inertial/heading.py`: `prepare_visual_heading(session, scene, *, stationary_window=(0.1, 1.6))` | Nominal unmirrored back-camera mounting for quarter-turn sensor orientation; not calibrated extrinsics. Heading workflow uses one world alignment, gyro propagation and visual heading correction; reuse conventions without coupling prototype relative rotation to scale estimation. |

Android files are under
`android/app/src/main/java/org/distancemeasuring/capture/`:

| Capability | Existing callable interface | Contract / remaining gap |
| --- | --- | --- |
| [R] Sensor acquisition | `CaptureRecorder.kt`: `start()`, `onSensorChanged(event)`, `takePhoto(imageCapture, cameraMetadata, targetRotation, geometry)`, `end(reason)` | Accelerometer, gyro, rotation vector, game rotation vector and magnetometer; serial recording while still images are captured. |
| [R] Session / timestamps | `RecordingSession.kt`: `recordSensor(sample)`, `beginPhoto(requestNs)`, `completePhoto(ticket, callbackNs, imageMetadata)`, `requestEnd(reason)`, `finish()` | Writes ordered sensor rows and lifecycle/image events. Selects frame time only for a compatible elapsed-realtime domain, otherwise records request time explicitly. |
| [R] JPEG metadata | `PhotoMetadata.kt`: `saveCapturedJpeg(file, image, camera, targetRotation, frameMetadata=null)`, `read(file, camera, targetRotation)` | Dimensions, EXIF orientation/flip, focal lengths, exposure, ISO, ImageProxy frame timestamp/source. P1 adds `CaptureGeometry` + `FrameMetadataStore` for exact timestamp association of focus/crop/3A/physical-lens results; `MainActivity.kt` supplies sensor characteristics. |

## Existing runnable paths and checks

The base OpenCV path is `vision.pipeline.match_session(session, output, ...)` →
`vision.pose_pipeline.estimate_poses(matches_directory, output, ...)` →
`scene.reconstruction.reconstruct_scene(matches_directory, poses_directory, output, ...)`
→ `optimization.pipeline.refine_scene(scene_path, output, **options)`. Corresponding
CLIs are `match_session.py`, `estimate_poses.py`, `reconstruct_scene.py` and
`refine_scene.py` in `python/scripts/`. This path selects exactly three images;
the audit/PnP path above can register more. The optional
`vision/visual_reference.py` / `reconstruct_visual_reference.py` backend uses
PyCOLMAP and is not needed for P1–P6 or the baseline.

Tests already cover dataset/schema validation, intrinsics, feature matching,
track merging, correlation, scene persistence and inertial conventions under
`python/tests/unit/`; geometry, relative pose, triangulation, pairwise PnP and BA
under `python/tests/synthetic/`; and stage CLIs under `python/tests/integration/`.
In particular inspect `test_inertial_experiment.py`, `test_pairwise_scene.py`,
`test_relative_pose.py`, `test_triangulation.py` and `test_bundle_adjustment.py`
before expanding those capabilities.

## Status and remaining adaptations for P1–P6

| Tasks | Work still needed; reuse boundary |
| --- | --- |
| P1.1–P1.2 | Implemented after this inventory: typed geometry fields, existing session inspector extended with change/stability diagnostics, exact-frame Camera2 metadata and `CaptureStability`/`CaptureStabilityPolicy` controls. No new dependency. See [P1 behavior and pending hardware validation](capture_stability.md). |
| P2.1–P2.4 | Implemented after this inventory in `vision/tracking.py`, `vision/tracking_pipeline.py` and `python/scripts/track_session.py`: Shi–Tomasi tile quotas, forward/reverse LK, age/error observation history and selective replenishment. Existing SIFT/matching is reused for opt-in large-motion initialization. See [interfaces, costs and limitations](temporal_tracking.md). |
| P3.1–P3.4 | Implemented after this inventory in `inertial/rotation.py`, `vision/parallax.py`, `vision/track_ranking.py` and `vision/parallax_pipeline.py`; CLI `python/scripts/analyze_track_parallax.py`. Reuses midpoint integration, nominal mounting, intrinsic projection and P2 tile mapping. Tests cover axis/pixel signs, pure rotation/translation and ranking. See [P3 conventions and real-data limitations](gyro_parallax.md). |
| P4.1–P4.3 | Implemented after this inventory in `vision/bootstrap.py`, `vision/bootstrap_pipeline.py` and `python/scripts/bootstrap_tracks.py`: earliest viable temporal pair, configurable coverage/parallax gates, independent visual rotation-only check, and existing E/cheirality/triangulation. Saves counts, timing, masks and a compatible two-camera scene. See [P4 thresholds, command and baseline](tracked_bootstrap.md). |
| P5.1–P5.2 | Implemented in `vision/pnp.py` (shared with the existing pairwise scene path), `vision/guided.py` and `vision/incremental.py`: temporal 3D–2D registration, verified OpenCV PnP/refinement, projection records, optional consistent gyro prior and image overlays. See [P5 command and acceptance evidence](tracked_incremental.md). |
| P5.3–P5.5 | Implemented in `vision/guided.py`, `vision/incremental.py` and `vision/incremental_pipeline.py`; CLI `python/scripts/extend_tracked_scene.py`. Reuses bounded NCC, SIFT/descriptor ratio, conflict-aware track merging and triangulation. Reports local/corridor candidate counts, failures, runtime and per-landmark quality. Recorded sparse stills grow landmarks but lack support for a later camera; synthetic sufficient-support sequences add cameras and points. See [P5 measured limitations](tracked_incremental.md). |
| P6.1–P6.3 | Implemented in `optimization/local_bundle.py`, selective variables in existing `optimization/bundle.py`, `vision/refinement.py` and `vision/landmark_confidence.py`. `extend_tracked_scene.py --refine` enables original-gauge local BA, at most two narrowing guided retries and confidence-gated PnP/BA with exclusion diagnostics. See [P6 configuration, acceptance and baseline](tracked_refinement.md). |

No dependency additions are required by this inventory. NumPy, SciPy and OpenCV
are already base dependencies in `python/pyproject.toml`. Keep metric-scale
experiments and dense reconstruction outside this pose prototype.
