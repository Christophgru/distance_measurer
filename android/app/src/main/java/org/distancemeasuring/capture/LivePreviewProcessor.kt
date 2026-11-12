package org.distancemeasuring.capture

import android.util.Log
import org.json.JSONArray
import org.json.JSONObject
import java.util.Locale

data class PreviewCameraSettings(
    val realtimeClock: Boolean, val backCamera: Boolean, val sensorOrientation: Int?,
    val sensorWidthMm: Double?, val sensorHeightMm: Double?, val focalMm: Double?,
    val activeArray: PreviewCrop?, val pinnedCameraId: String?, val frames: FrameMetadataStore,
    val cameraId: String = "unknown",
)

private data class PreviewFrameModel(val frame: PreviewLuma, val camera: PreviewIntrinsics?,
                                     val physicalId: String?, val signature: String,
                                     val metadata: JSONObject)

/** One copied-frame delay lets already-recorded gyro samples bracket the frame. */
class LivePreviewProcessor(
    private val gyro: PreviewGyroHistory,
    private val config: PreviewTrackingConfig = PreviewTrackingConfig(),
    private val onExport: (String, PreviewFrameExport) -> Unit = { _, _ -> },
) : AutoCloseable {
    @Volatile private var settings: PreviewCameraSettings? = null
    private var activeSettings: PreviewCameraSettings? = null
    private var activeSession: String? = null
    private var pending: PreviewLuma? = null
    private var previous: PreviewFrameModel? = null
    private var tracker: PreviewFlowTracker? = null
    private var attemptedLoad = false
    private var lastLogNs = -1L

    fun configure(value: PreviewCameraSettings) { settings = value }

    fun process(frame: PreviewLuma, session: String): String {
        val setup = settings ?: return "Preview tracking: camera geometry unavailable"
        if (setup !== activeSettings || session != activeSession) {
            if (session == activeSession) flush(session)
            tracker?.reset(); pending = null; previous = null; lastLogNs = -1
            activeSettings = setup; activeSession = session
        }
        if (!attemptedLoad) {
            attemptedLoad = true
            if (PreviewNativeRuntime.available()) tracker = PreviewFlowTracker(config)
        }
        val flow = tracker ?: run {
            export(model(frame, setup), setup, JSONObject().put("unavailable_reason", "opencv_unavailable"), "tracking_unavailable")
            return "Preview tracking unavailable: OpenCV load failed; JPEG capture remains enabled"
        }
        val ready = pending
        pending = frame
        if (ready == null) return "Preview tracking: buffering one frame for gyro coverage"
        return try {
            processReady(ready, setup, flow)
        } catch (error: Exception) {
            flow.reset(); previous = null
            export(model(ready, setup), setup, JSONObject().put("unavailable_reason", "flow_error")
                .put("error", error.message), "flow_error")
            "Preview tracking reset: ${error.message}"
        }
    }

    /** Called on the analysis worker after its session gate closes, before writer drain. */
    fun flush(session: String) {
        if (activeSession != session) return
        val frame = pending ?: return
        pending = null
        val setup = activeSettings ?: return
        val flow = tracker ?: return
        try { processReady(frame, setup, flow) }
        catch (error: Exception) {
            flow.reset(); previous = null
            export(model(frame, setup), setup, JSONObject().put("unavailable_reason", "flow_error")
                .put("error", error.message), "flow_error")
        }
    }

    private fun model(frame: PreviewLuma, setup: PreviewCameraSettings): PreviewFrameModel {
        val metadata = setup.frames.peek(frame.timestampNs)
        val exactFocal = metadata?.optDouble("focal_length_mm", Double.NaN)
        val focal = exactFocal?.takeIf { it.isFinite() && it > 0 } ?: setup.focalMm
        val rawCrop = metadata?.optJSONArray("crop_region")
        val crop = if (rawCrop != null && rawCrop.length() == 4) PreviewCrop(rawCrop.getInt(0),
            rawCrop.getInt(1), rawCrop.getInt(2), rawCrop.getInt(3)) else setup.activeArray
        val camera = if (setup.sensorWidthMm != null && setup.sensorHeightMm != null && focal != null &&
            setup.activeArray != null && crop != null) try { approximatePreviewIntrinsics(frame,
                setup.sensorWidthMm, setup.sensorHeightMm, focal, setup.activeArray, crop) }
                catch (_: IllegalArgumentException) { null } else null
        val physical = metadata?.optString("physical_camera_id")?.takeIf { it.isNotEmpty() && it != "null" }
            ?: setup.pinnedCameraId
        val signature = "${frame.width},${frame.height},${frame.bufferWidth},${frame.bufferHeight}," +
            "${frame.cropLeft},${frame.cropTop},${frame.cropWidth},${frame.cropHeight}," +
            "${frame.sampleStep},${frame.rotationDegrees},$camera"
        val clock = if (setup.realtimeClock) "android_elapsed_realtime_ns" else "camera_unknown"
        val record = JSONObject().put("width", frame.width).put("height", frame.height)
            .put("camera_id", setup.cameraId).put("physical_camera_id", physical ?: JSONObject.NULL)
            .put("rotation_degrees", frame.rotationDegrees).put("target_rotation", 0)
            .put("is_flipped", false).put("lens_facing", if (setup.backCamera) 1 else 0)
            .put("sensor_orientation_degrees", setup.sensorOrientation ?: JSONObject.NULL)
            .put("sensor_timestamp_ns", frame.timestampNs).put("sensor_timestamp_clock_domain", clock)
            .put("focal_length_mm", focal ?: JSONObject.NULL)
            .put("metadata_source", if (metadata == null) "preview_camera_characteristics" else "exact_timestamp_camera2_preview")
        for (name in listOf("crop_region", "focus_distance_diopters", "zoom_ratio", "exposure_time_s",
            "iso", "ae_mode", "ae_state", "ae_lock", "awb_mode", "awb_state", "awb_lock", "af_mode", "af_state", "lens_state")) {
            if (metadata?.has(name) == true) record.put(name, metadata.get(name))
        }
        return PreviewFrameModel(frame, camera, physical, signature, record)
    }

    private fun export(model: PreviewFrameModel, setup: PreviewCameraSettings,
                       diagnostics: JSONObject, resetReason: String?) {
        val clock = if (setup.realtimeClock) "android_elapsed_realtime_ns" else "camera_unknown"
        val k = model.camera?.let { camera -> JSONObject().put("image_width", model.frame.width)
            .put("image_height", model.frame.height)
            .put("camera_matrix", JSONArray(listOf(listOf(camera.fx, camera.skew, camera.cx),
                listOf(0.0, camera.fy, camera.cy), listOf(0.0, 0.0, 1.0))))
            .put("distortion", JSONArray(listOf(0, 0, 0, 0, 0)))
            .put("source", "metadata-derived").put("approximate", true)
            .put("pixel_convention", "stored_pixels_ignore_exif").put("camera_id", setup.cameraId)
            .put("assumptions", JSONArray(listOf("Nominal focal/sensor/crop preview geometry",
                "Approximate principal point; distortion is uncalibrated and set to zero"))) }
        onExport(checkNotNull(activeSession), PreviewFrameExport(model.frame, model.metadata, k, clock,
            diagnostics, "${setup.cameraId},${model.physicalId},$clock,${model.signature}", resetReason))
    }

    private fun processReady(frame: PreviewLuma, setup: PreviewCameraSettings, flow: PreviewFlowTracker): String {
        val current = model(frame, setup)
        val old = previous
        val lensChanged = old?.physicalId != null && current.physicalId != null && old.physicalId != current.physicalId
        val reset = old != null && (old.signature != current.signature || lensChanged || frame.timestampNs <= old.frame.timestampNs)
        if (reset) { flow.reset(); previous = null }
        val source = previous
        val step = flow.step(frame)
        val estimate = when {
            source == null -> PreviewGyroRotation(null, 0, 0, if (reset) "geometry_or_timestamp_changed" else "initial_frame")
            !setup.realtimeClock -> PreviewGyroRotation(null, 0, 0, "camera_clock_unknown")
            !setup.backCamera || setup.sensorOrientation !in listOf(0, 90, 180, 270) -> PreviewGyroRotation(null, 0, 0, "unsupported_camera_mounting")
            source.camera == null || current.camera == null -> PreviewGyroRotation(null, 0, 0, "intrinsics_unavailable")
            else -> gyro.rotation(source.frame.timestampNs, frame.timestampNs)
        }
        val rotation = estimate.matrix?.let { PreviewRotation.cameraRayRotation(it, setup.sensorOrientation!!) }
        val valid = step.matches.mapNotNull { match ->
            val prediction = if (rotation != null) source?.camera?.rotate(match.source.point, rotation, current.camera!!) else null
            prediction?.let { Triple(match, it.distance(match.target.point), it) }
        }
        val parallax = previewMedian(valid.map { it.second })
        val rawMotion = previewMedian(valid.map { it.first.source.point.distance(it.first.target.point) })
        val sourceCells = valid.map { previewCell(it.first.source.point, frame.width, frame.height,
            config.keyframes.gridRows, config.keyframes.gridCols) }.toSet().size
        val targetCells = valid.map { previewCell(it.first.target.point, frame.width, frame.height,
            config.keyframes.gridRows, config.keyframes.gridCols) }.toSet().size
        val decision = KeyframePolicy.score(valid.size, sourceCells, targetCells, parallax,
            frame.width, frame.height, rotation != null, config.keyframes)
        val diagnostics = JSONObject().put("frame_timestamp_ns", frame.timestampNs)
            .put("timestamp_clock_domain", if (setup.realtimeClock) "android_elapsed_realtime_ns" else "camera_unknown")
            .put("width", frame.width).put("height", frame.height).put("sample_step", frame.sampleStep)
            .put("rotation_degrees_metadata", frame.rotationDegrees).put("analysis_delay_frames", 1)
            .put("active_tracks", step.activeCount).put("surviving_tracks", step.matches.size)
            .put("track_budget", config.maxTracks).put("fb_threshold_px", config.fbThresholdPx)
            .put("keyframe_settings", config.keyframes.json())
            .put("valid_parallax_tracks", valid.size).put("occupied_cells", JSONArray(listOf(sourceCells, targetCells)))
            .put("median_fb_error_px", step.medianFbError ?: JSONObject.NULL)
            .put("median_residual_parallax_px", parallax ?: JSONObject.NULL)
            .put("compensation_increases_median_motion", if (parallax != null && rawMotion != null) parallax > rawMotion else JSONObject.NULL)
            .put("predictions_in_bounds", valid.count { it.third.inBounds(frame.width, frame.height) })
            .put("seeded_count", step.seededCount).put("detected_cells", JSONArray(step.detectedCells))
            .put("flow_runtime_ms", step.flowMs).put("selection_runtime_ms", step.selectionMs)
            .put("gyro_sample_count", estimate.sampleCount).put("gyro_max_gap_ns", estimate.maxGapNs)
            .put("gyro_reason", estimate.reason ?: JSONObject.NULL)
            .put("intrinsics_source", "approximate_focal_crop_geometry")
            .put("quality_status", "nominal_preview_geometry_unverified").put("keyframe", decision.json())
        if (lastLogNs < 0 || frame.timestampNs - lastLogNs >= 500_000_000) {
            Log.i("PreviewTracking", diagnostics.toString()); lastLogNs = frame.timestampNs
        }
        previous = current
        export(current, setup, diagnostics, if (reset) "camera_geometry_or_timestamp_changed"
            else if (source == null) "tracking_reference_reset" else null)
        fun formatted(value: Double?) = value?.let { String.format(Locale.US, "%.2f", it) } ?: "unavailable"
        return "tracks: ${step.activeCount} (${step.matches.size} survived); median FB: ${formatted(step.medianFbError)} px\n" +
            "rotation-compensated parallax: ${formatted(parallax)} px; keyframe: ${decision.label}\n" +
            "${estimate.reason ?: decision.reasons.joinToString()}; approximate K / nominal IMU mount"
    }

    override fun close() { tracker?.close(); tracker = null; previous = null; pending = null }
}
