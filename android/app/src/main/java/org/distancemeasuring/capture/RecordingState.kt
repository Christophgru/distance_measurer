package org.distancemeasuring.capture

data class RecordingState(
    val active: Boolean = false,
    val ending: Boolean = false,
    val capturing: Boolean = false,
    val imageCount: Int = 0,
    val sampleCount: Int = 0,
    val path: String = "",
    val message: String = "idle",
) {
    val canStart: Boolean get() = !active
    val canTakePhoto: Boolean get() = active && !ending && !capturing
    val canEnd: Boolean get() = active && !ending
}
