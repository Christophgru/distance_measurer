package org.distancemeasuring.capture

import org.junit.Assert.*
import org.junit.Test

class RecordingStateTest {
    @Test fun idleOnlyAllowsStarting() {
        val state = RecordingState()
        assertTrue(state.canStart)
        assertFalse(state.canTakePhoto)
        assertFalse(state.canEnd)
    }

    @Test fun recordingCannotStartAnotherTimeline() {
        val state = RecordingState(active = true)
        assertFalse(state.canStart)
        assertTrue(state.canTakePhoto)
        assertTrue(state.canEnd)
    }

    @Test fun pendingPhotoCanEndButCannotStartAnotherCapture() {
        val state = RecordingState(active = true, capturing = true)
        assertFalse(state.canStart)
        assertFalse(state.canTakePhoto)
        assertTrue(state.canEnd)
    }

    @Test fun finishingBlocksAllCommands() {
        val state = RecordingState(active = true, ending = true, capturing = true)
        assertFalse(state.canStart)
        assertFalse(state.canTakePhoto)
        assertFalse(state.canEnd)
    }
}
