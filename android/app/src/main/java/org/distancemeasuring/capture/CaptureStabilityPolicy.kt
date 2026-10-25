package org.distancemeasuring.capture

import org.json.JSONObject
import kotlin.math.abs

data class FreezePlan(val crop: List<Int>, val focusDiopters: Float?, val lockAwb: Boolean)

/** Small, device-independent policy; result values use the Camera2 enum integers. */
class CaptureStabilityPolicy(
    private val manualFocus: Boolean, private val fixedFocus: Boolean,
    private val awbLockSupported: Boolean,
) {
    var phase = "disabled"
        private set
    var reason: String? = null
        private set
    var plan: FreezePlan? = null
        private set
    private var candidate: FreezePlan? = null
    private var candidateLens: String? = null
    private var candidateFocal = Double.NaN
    private var consecutive = 0

    fun start() {
        phase = "waiting_for_convergence"
        reason = null
        plan = null
        candidate = null
        consecutive = 0
    }

    fun stop() { phase = "disabled" }
    fun applied() { if (phase == "applying") phase = "applied" }
    fun failed(message: String) { phase = "automatic_fallback"; reason = message }

    fun observe(frame: JSONObject): FreezePlan? {
        if (phase != "waiting_for_convergence") return null
        val focused = fixedFocus || frame.optInt("af_state", -1) in listOf(2, 4) ||
            (frame.optInt("af_mode", -1) == 0 && frame.optInt("lens_state", -1) == 0)
        val exposed = frame.optInt("ae_mode", -1) == 0 || frame.optInt("ae_state", -1) in listOf(2, 3)
        val crop = frame.optJSONArray("crop_region")
        if (!focused || !exposed || frame.optInt("lens_state", -1) == 1 || crop?.length() != 4) {
            consecutive = 0
            return null
        }
        val distance = frame.optDouble("focus_distance_diopters", Double.NaN)
        val current = FreezePlan(
            (0..3).map { crop.getInt(it) },
            if (manualFocus && distance.isFinite() && distance >= 0) distance.toFloat() else null,
            awbLockSupported && frame.optInt("awb_state", -1) in listOf(2, 3),
        )
        val lens = frame.optString("physical_camera_id", "")
        val focal = frame.optDouble("focal_length_mm", Double.NaN)
        val previous = candidate
        val sameFocus = previous != null && (previous.focusDiopters == current.focusDiopters ||
            (previous.focusDiopters != null && current.focusDiopters != null &&
                abs(previous.focusDiopters - current.focusDiopters) <= 1e-4f))
        consecutive = if (previous?.crop == current.crop && sameFocus &&
            candidateLens == lens && (candidateFocal == focal || (candidateFocal.isNaN() && focal.isNaN())))
            consecutive + 1 else 1
        candidate = current
        candidateLens = lens
        candidateFocal = focal
        if (consecutive < 3) return null
        plan = current
        phase = "applying"
        return current
    }
}
