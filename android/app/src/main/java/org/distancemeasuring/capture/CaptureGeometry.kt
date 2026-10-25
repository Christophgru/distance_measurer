package org.distancemeasuring.capture

import android.hardware.camera2.CameraCaptureSession
import android.hardware.camera2.CaptureRequest
import android.hardware.camera2.CaptureResult
import android.hardware.camera2.TotalCaptureResult
import android.os.Build
import org.json.JSONArray
import org.json.JSONObject

/** Read-only Camera2 callback. It never submits requests or changes the session. */
class CaptureGeometry(
    private val physicalCameraId: String? = null, private val singleCameraId: String? = null,
) : CameraCaptureSession.CaptureCallback() {
    val frames = FrameMetadataStore()
    @Volatile var onFrame: ((JSONObject) -> Unit)? = null

    override fun onCaptureCompleted(
        session: CameraCaptureSession, request: CaptureRequest, result: TotalCaptureResult,
    ) {
        try {
            @Suppress("DEPRECATION") // Required on API 28–30.
            val physical: CaptureResult? = when {
                physicalCameraId == null -> null
                Build.VERSION.SDK_INT >= 31 -> result.physicalCameraTotalResults[physicalCameraId]
                Build.VERSION.SDK_INT >= 28 -> result.physicalCameraResults[physicalCameraId]
                else -> null
            }
            // A pinned physical output must use that sensor's result, not the
            // logical camera's potentially different active lens/crop metadata.
            if (physicalCameraId != null && physical == null) return
            val frame = physical ?: result
            val timestamp = frame.get(CaptureResult.SENSOR_TIMESTAMP) ?: return
            val crop = frame.get(CaptureResult.SCALER_CROP_REGION)
            val metadata = JSONObject()
                .put("focus_distance_diopters", frame.get(CaptureResult.LENS_FOCUS_DISTANCE) ?: JSONObject.NULL)
                .put("crop_region", crop?.let { JSONArray(listOf(it.left, it.top, it.right, it.bottom)) } ?: JSONObject.NULL)
                .put("zoom_ratio", if (Build.VERSION.SDK_INT >= 30)
                    frame.get(CaptureResult.CONTROL_ZOOM_RATIO) ?: JSONObject.NULL else JSONObject.NULL)
                .put("physical_camera_id", physicalCameraId ?: singleCameraId ?: (if (Build.VERSION.SDK_INT >= 29)
                    frame.get(CaptureResult.LOGICAL_MULTI_CAMERA_ACTIVE_PHYSICAL_ID) else null) ?: JSONObject.NULL)
                .put("ae_mode", frame.get(CaptureResult.CONTROL_AE_MODE) ?: JSONObject.NULL)
                .put("ae_state", frame.get(CaptureResult.CONTROL_AE_STATE) ?: JSONObject.NULL)
                .put("ae_lock", frame.get(CaptureResult.CONTROL_AE_LOCK) ?: JSONObject.NULL)
                .put("awb_mode", frame.get(CaptureResult.CONTROL_AWB_MODE) ?: JSONObject.NULL)
                .put("awb_state", frame.get(CaptureResult.CONTROL_AWB_STATE) ?: JSONObject.NULL)
                .put("awb_lock", frame.get(CaptureResult.CONTROL_AWB_LOCK) ?: JSONObject.NULL)
                .put("af_mode", frame.get(CaptureResult.CONTROL_AF_MODE) ?: JSONObject.NULL)
                .put("af_state", frame.get(CaptureResult.CONTROL_AF_STATE) ?: JSONObject.NULL)
                .put("lens_state", frame.get(CaptureResult.LENS_STATE) ?: JSONObject.NULL)
                .put("capture_result_source", "exact_sensor_timestamp")
            // Reuse the existing fields; prefer the actual frame result over EXIF.
            frame.get(CaptureResult.LENS_FOCAL_LENGTH)?.let { metadata.put("focal_length_mm", it) }
            frame.get(CaptureResult.SENSOR_EXPOSURE_TIME)?.let { metadata.put("exposure_time_s", it / 1e9) }
            frame.get(CaptureResult.SENSOR_SENSITIVITY)?.let { metadata.put("iso", it) }
            frames.put(timestamp, metadata, request.get(CaptureRequest.CONTROL_CAPTURE_INTENT) == CaptureRequest.CONTROL_CAPTURE_INTENT_STILL_CAPTURE)
            onFrame?.invoke(metadata)
        } catch (_: Exception) {
            // Optional vendor metadata must not interrupt CameraX capture or sensors.
        }
    }
}
