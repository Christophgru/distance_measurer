package org.distancemeasuring.capture

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class CaptureStabilityPolicyTest {
    private fun frame() = JSONObject()
        .put("focus_distance_diopters", 2.0).put("focal_length_mm", 4.2)
        .put("physical_camera_id", "wide").put("crop_region", JSONArray(listOf(0, 0, 4000, 3000)))
        .put("af_mode", 4).put("af_state", 2).put("lens_state", 0)
        .put("ae_mode", 1).put("ae_state", 2).put("awb_state", 2)

    @Test fun waitsForThreeConvergedFramesAndAppliesOnlyOncePerTimeline() {
        val policy = CaptureStabilityPolicy(true, false, true)
        assertNull(policy.observe(frame()))
        policy.start()
        assertNull(policy.observe(frame()))
        assertNull(policy.observe(frame()))
        val plan = policy.observe(frame())!!
        assertEquals(2f, plan.focusDiopters)
        assertEquals(listOf(0, 0, 4000, 3000), plan.crop)
        assertTrue(plan.lockAwb)
        assertEquals("applying", policy.phase)
        policy.applied()
        assertEquals("applied", policy.phase)
        assertNull(policy.observe(frame().put("focus_distance_diopters", 3)))
        policy.stop()
        assertEquals("disabled", policy.phase)
        policy.start()
        assertEquals("waiting_for_convergence", policy.phase)
        assertNull(policy.plan)
    }

    @Test fun movingLensOrUnconvergedExposureResetsTheConsecutiveCount() {
        val policy = CaptureStabilityPolicy(true, false, true)
        policy.start()
        repeat(2) { assertNull(policy.observe(frame())) }
        assertNull(policy.observe(frame().put("lens_state", 1)))
        repeat(2) { assertNull(policy.observe(frame())) }
        assertNull(policy.observe(frame().put("ae_state", 1)))
        repeat(2) { assertNull(policy.observe(frame())) }
        assertNotNull(policy.observe(frame()))
    }

    @Test fun lensAndCropChangesRequireANewConvergedRun() {
        val policy = CaptureStabilityPolicy(true, false, true)
        policy.start()
        repeat(2) { policy.observe(frame()) }
        val tele = frame().put("physical_camera_id", "tele")
            .put("crop_region", JSONArray(listOf(10, 10, 3900, 2900)))
        assertNull(policy.observe(tele))
        assertNull(policy.observe(tele))
        assertNotNull(policy.observe(tele))
    }

    @Test fun unsupportedManualFocusAndAwbNeverGenerateThoseRequests() {
        val policy = CaptureStabilityPolicy(false, false, false)
        policy.start()
        repeat(2) { policy.observe(frame()) }
        val plan = policy.observe(frame())!!
        assertNull(plan.focusDiopters)
        assertFalse(plan.lockAwb)
    }

    @Test fun fixedFocusDoesNotRequireAnAutofocusStateOrAFocusRequest() {
        val policy = CaptureStabilityPolicy(false, true, true)
        policy.start()
        val fixed = frame().put("af_state", JSONObject.NULL).put("focus_distance_diopters", 0)
        repeat(2) { policy.observe(fixed) }
        assertNull(policy.observe(fixed)!!.focusDiopters)
    }

    @Test fun failureKeepsAutomaticFallbackUntilTheNextTimeline() {
        val policy = CaptureStabilityPolicy(true, false, true)
        policy.start()
        repeat(3) { policy.observe(frame()) }
        policy.failed("unsupported_control")
        assertEquals("automatic_fallback", policy.phase)
        assertEquals("unsupported_control", policy.reason)
        assertNull(policy.observe(frame()))
        policy.applied() // A late success must not reverse the fallback decision.
        assertEquals("automatic_fallback", policy.phase)
        policy.start()
        assertNull(policy.reason)
    }

    @Test fun missingMetadataNeverPretendsThatTheCameraConverged() {
        val policy = CaptureStabilityPolicy(true, false, true)
        policy.start()
        repeat(10) { assertNull(policy.observe(JSONObject())) }
        assertEquals("waiting_for_convergence", policy.phase)
    }

    @Test fun focusDriftResetsConvergenceButTinyRoundingNoiseDoesNot() {
        val policy = CaptureStabilityPolicy(true, false, true)
        policy.start()
        repeat(2) { policy.observe(frame()) }
        assertNull(policy.observe(frame().put("focus_distance_diopters", 3.0)))
        assertNull(policy.observe(frame().put("focus_distance_diopters", 3.00001)))
        assertNotNull(policy.observe(frame().put("focus_distance_diopters", 3.0)))
    }
}
