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
| [R] Reprojection / BA | `optimization/bundle.py`: `reprojection_residuals(scene) -> ndarray`; `bundle_adjust(scene, *, loss="soft_l1", f_scale=1.0, max_nfev=300) -> BundleResult` | Residuals are predicted minus observed `(u,v)`, accepted observations only. SciPy sparse least squares with fixed K, exact first camera and fixed first-baseline length; report includes before/after Euclidean observation RMSE, convergence and acceptance. Whole-scene refinement, not a local window yet. |
| [R] Incremental PnP | `vision/pairwise_scene.py`: `rebuild_pairwise_scene(audit, *, refine_incremental=False, seed_strategy="support", allow_unregistered=False) -> PairwiseSceneResult` | Already seeds a pair, triangulates tracks, registers further cameras with `cv2.solvePnPRansac` + `solvePnPRefineLM`, and optionally runs BA. PnP is embedded in this function; adapt/extract it for P5.1 instead of adding a second solver. Input is an audit dictionary from `tools/validation/audit_correspondences.py`, not temporal tracks. |
| [R] Projection / bounded patch search | `CameraIntrinsics.project`; `vision/correlation.py`: `correlate_patch(template, search, *, min_score=0.7, min_margin=0.05, peak_radius=2)`; `correlate_local_point(first, second, first_pixel, second_pixel, *, homography=None, patch_size=21, search_radius=5)` | Existing normalized correlation has texture/ambiguity rejection and bounded local search. Adapt it around projected landmarks; it currently starts from an approximate image pair match. |
| [R] Gyro / orientation | `inertial/experimental.py`: `gyro_orientation_interpolator(times, angular_velocity, initial_orientation)`; `orientation_interpolator(times, quaternions)` | Seconds on a common relative timeline; device-frame gyro rad/s, midpoint right-composed rotation increments and SciPy Slerp. No need to implement another integrator. `imu/` is only a placeholder; actual utilities live under `inertial/`. |
| [R] Device-to-camera convention | `inertial/experimental.py`: `back_camera_from_device(sensor_orientation_degrees)`; `inertial/heading.py`: `prepare_visual_heading(session, scene, *, stationary_window=(0.1, 1.6))` | Nominal unmirrored back-camera mounting for quarter-turn sensor orientation; not calibrated extrinsics. Heading workflow uses one world alignment, gyro propagation and visual heading correction; reuse conventions without coupling prototype relative rotation to scale estimation. |

Android files are under
`android/app/src/main/java/org/distancemeasuring/capture/`:

| Capability | Existing callable interface | Contract / remaining gap |
| --- | --- | --- |
| [R] Sensor acquisition | `CaptureRecorder.kt`: `start()`, `onSensorChanged(event)`, `takePhoto(imageCapture, cameraMetadata, targetRotation)`, `end(reason)` | Accelerometer, gyro, rotation vector, game rotation vector and magnetometer; serial recording while still images are captured. |
| [R] Session / timestamps | `RecordingSession.kt`: `recordSensor(sample)`, `beginPhoto(requestNs)`, `completePhoto(ticket, callbackNs, imageMetadata)`, `requestEnd(reason)`, `finish()` | Writes ordered sensor rows and lifecycle/image events. Selects frame time only for a compatible elapsed-realtime domain, otherwise records request time explicitly. |
| [R] JPEG metadata | `PhotoMetadata.kt`: `saveCapturedJpeg(file, image, camera, targetRotation)`, `read(file, camera, targetRotation)` | Dimensions, EXIF orientation/flip, focal lengths, exposure, ISO, ImageProxy frame timestamp/source. `MainActivity.kt` private `bindCamera()` supplies Camera2 ID, lens facing, sensor orientation/size and timestamp domain. Focus/crop/3A state is not recorded yet. |

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

## Missing adaptations required by P1–P6

| Tasks | Work still needed; reuse boundary |
| --- | --- |
| P1.1–P1.2 | Record focus distance, zoom/crop, physical lens identity where available, 3A modes/locks and stability diagnostics; add capture stability control. Existing typed `ImageMetadata` retains unknown fields in `.raw` but does not expose these settings yet. CameraX/Camera2 and ExifInterface are already dependencies; no Android CV dependency is present or needed for P1. |
| P2.1–P2.4 | No grid-quota tracking seeds, pyramidal LK, forward/backward rejection, age/error observation fields or selective replenishment found. Reuse `Features.response`, image loading and track IDs; use existing OpenCV for detection/flow. Descriptor tracks are not persistent temporal tracking state. |
| P3.1–P3.4 | Expose per-image-pair gyro sample windows/counts and relative rotations using the existing integrator. Extend mounting tests to image-motion signs for yaw/pitch/roll; add rotation-only pixel prediction and residual parallax, spatial coverage and track ranking. Existing mounting math and tests are a starting point, not completion of P3. |
| P4.1–P4.3 | Add earliest temporally qualified bootstrap selection with configurable coverage/parallax/track thresholds. Existing pairwise seeds rank visual support/conditioning; they do not use gyro-compensated temporal tracks. Feed selected tracks to existing E/pose/triangulation and expose counts/timing/quality. |
| P5.1–P5.2 | Adapt existing PnP registration for temporal-track/landmark associations and report runtime/tolerances; wrap existing projection with bounds, observation counts and search radii/visualization. |
| P5.3–P5.5 | Adapt bounded correlation or descriptors around predicted landmarks; add candidate/runtime counts and optional mutual checks. No pose-derived epipolar corridor search exists. Merge newly confirmed observations and triangulate incrementally with existing helpers; the current audit pipeline computes pair matches first. |
| P6.1–P6.3 | Add a local BA camera/landmark window while preserving the existing gauge; current incremental refinement calls global BA. Add bounded repeated guided search after BA and a landmark-confidence selector for PnP. Angle/error/view-count components already exist; their combined confidence policy does not. |

No dependency additions are required by this inventory. NumPy, SciPy and OpenCV
are already base dependencies in `python/pyproject.toml`. Keep metric-scale
experiments and dense reconstruction outside this pose prototype.
