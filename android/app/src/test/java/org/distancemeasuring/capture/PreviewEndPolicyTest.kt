package org.distancemeasuring.capture

import org.junit.Assert.*
import org.junit.Test

class PreviewEndPolicyTest {
    @Test fun userEndKeepsPreviewThroughPendingJpegAndRequiresAFollowingFrame() {
        val policy = PreviewEndPolicy(200)
        policy.begin("user")
        assertNull(policy.decision(1_000, true, null, 900, true))
        // The JPEG may finish long after End; its existing watchdog owns that wait.
        assertNull(policy.decision(10_000, true, null, 9_900, true))
        assertNull(policy.decision(10_001, false, 9_950, 9_900, true))
        assertNull(policy.decision(10_002, false, 9_950, 9_950, true))
        assertEquals("following_preview_queued", policy.decision(10_003, false, 9_950, 9_951, true))
    }

    @Test fun recordedFinalJpegCannotCloseOnTheLastEarlierPreview() {
        val policy = PreviewEndPolicy()
        policy.begin("user")
        val exposure = 2_494_809_371_013_756L
        val lastPreview = 2_494_809_271_032_506L
        assertNull(policy.decision(exposure + 400_000_000, false, exposure, lastPreview, true))
        assertEquals("following_preview_queued",
            policy.decision(exposure + 500_000_000, false, exposure, exposure + 33_000_000, true))
    }

    @Test fun missingPreviewTimesOutAndRepeatedEndCannotExtendTheDeadline() {
        val policy = PreviewEndPolicy(200)
        policy.begin("user")
        assertNull(policy.decision(1_000, false, 950, 900, true))
        policy.begin("user")
        assertNull(policy.decision(1_199, false, 950, 900, true))
        assertEquals("preview_tail_timeout", policy.decision(1_200, false, 950, 900, true))
    }

    @Test fun backgroundAndDestroyInterruptATailWithoutWaitingForTheCamera() {
        for (reason in listOf("activity_paused", "activity_destroyed")) {
            val policy = PreviewEndPolicy()
            policy.begin("user")
            assertNull(policy.decision(1_000, true, null, null, true))
            policy.begin(reason)
            assertEquals("interrupted_end", policy.decision(1_001, true, null, null, true))
        }
    }

    @Test fun noPhotoUnavailablePreviewAndNewSessionsDoNotWaitOrInheritDeadlines() {
        val policy = PreviewEndPolicy(200)
        policy.begin("user")
        assertEquals("no_saved_photo", policy.decision(1_000, false, null, 900, true))
        assertEquals("preview_unavailable", policy.decision(1_000, true, null, null, false))
        val next = PreviewEndPolicy(200)
        assertNull(next.decision(10_000, false, 9_900, 9_950, true))
        next.begin("user")
        assertNull(next.decision(10_000, false, 9_900, 9_800, true))
        assertEquals("following_preview_queued", next.decision(10_001, false, 9_900, 9_950, true))
    }

    @Test fun finishingPreviewNeverReenablesRecordingCommands() {
        val state = RecordingState(active = true, ending = true, previewFinishing = true)
        assertTrue(state.acceptsPreview)
        assertFalse(state.canStart)
        assertFalse(state.canTakePhoto)
        assertFalse(state.canEnd)
        assertFalse(state.copy(previewFinishing = false).acceptsPreview)
        assertFalse(state.copy(active = false).acceptsPreview)
    }
}
