package org.distancemeasuring.capture

import android.graphics.Rect
import android.hardware.camera2.CameraCharacteristics
import android.hardware.camera2.CaptureRequest
import android.os.Handler
import android.os.Looper
import androidx.camera.camera2.interop.Camera2CameraControl
import androidx.camera.camera2.interop.CaptureRequestOptions
import androidx.camera.camera2.interop.ExperimentalCamera2Interop
import androidx.camera.core.Camera
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.Executor

/** Prototype default during each timeline; automatic exposure remains enabled. */
@androidx.annotation.OptIn(ExperimentalCamera2Interop::class)
class CaptureStability(
    camera: Camera, characteristics: CameraCharacteristics,
    private val executor: Executor, private val physicalPin: String,
) {
    private val control = Camera2CameraControl.from(camera.cameraControl)
    private val minimumFocus = characteristics.get(CameraCharacteristics.LENS_INFO_MINIMUM_FOCUS_DISTANCE)
    private val policy = CaptureStabilityPolicy(
        manualFocus = minimumFocus != null && minimumFocus > 0 &&
            characteristics.get(CameraCharacteristics.CONTROL_AF_AVAILABLE_MODES)
                ?.contains(CaptureRequest.CONTROL_AF_MODE_OFF) == true,
        fixedFocus = minimumFocus == 0f,
        awbLockSupported = characteristics.get(CameraCharacteristics.CONTROL_AWB_LOCK_AVAILABLE) == true,
    )
    private val handler = Handler(Looper.getMainLooper())
    private var generation = 0
    private var zoomStatus = "requested"

    init {
        try {
            val zoom = camera.cameraControl.setZoomRatio(1f)
            zoom.addListener({
                zoomStatus = try { zoom.get(); "applied" } catch (_: Exception) { "unavailable" }
            }, executor)
        } catch (_: Exception) { zoomStatus = "unavailable" }
    }

    fun start() { generation++; policy.start() }

    fun stop() {
        if (policy.phase == "disabled") return
        generation++
        policy.stop()
        try { control.clearCaptureRequestOptions() } catch (_: Exception) { /* Camera already closed. */ }
    }

    fun observe(frame: JSONObject) {
        if (zoomStatus == "requested") return
        val plan = policy.observe(frame) ?: return
        val token = generation
        try {
            val crop = plan.crop
            val options = CaptureRequestOptions.Builder()
                .setCaptureRequestOption(CaptureRequest.SCALER_CROP_REGION, Rect(crop[0], crop[1], crop[2], crop[3]))
            plan.focusDiopters?.let {
                options.setCaptureRequestOption(CaptureRequest.CONTROL_AF_MODE, CaptureRequest.CONTROL_AF_MODE_OFF)
                options.setCaptureRequestOption(CaptureRequest.LENS_FOCUS_DISTANCE, it)
            }
            if (plan.lockAwb) options.setCaptureRequestOption(CaptureRequest.CONTROL_AWB_LOCK, true)
            val pending = control.setCaptureRequestOptions(options.build())
            pending.addListener({
                if (token == generation && policy.phase == "applying") {
                    try { pending.get(); policy.applied() }
                    catch (_: Exception) { fallback("control_request_failed") }
                }
            }, executor)
            handler.postDelayed({
                if (token == generation && policy.phase == "applying") fallback("control_request_timed_out")
            }, 2000)
        } catch (_: Exception) { fallback("control_unavailable") }
    }

    private fun fallback(reason: String) {
        policy.failed(reason)
        try { control.clearCaptureRequestOptions() } catch (_: Exception) { /* Best effort. */ }
    }

    fun snapshot(): JSONObject = JSONObject()
        .put("mode", "prototype_default")
        .put("phase_at_request", policy.phase)
        .put("reason", policy.reason ?: JSONObject.NULL)
        .put("physical_camera_pin", physicalPin)
        .put("zoom_1x", zoomStatus)
        .put("requested_focus_distance_diopters", policy.plan?.focusDiopters ?: JSONObject.NULL)
        .put("requested_crop_region", policy.plan?.crop?.let { JSONArray(it) } ?: JSONObject.NULL)
        .put("requested_awb_lock", policy.plan?.lockAwb ?: false)
        .put("ae_lock_requested", false)
}
