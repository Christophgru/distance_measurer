package org.distancemeasuring.capture

/** Worker-owned bounded tail. Never compare an unknown camera clock to elapsed realtime. */
class PreviewEndPolicy(private val tailTimeoutNs: Long = 2_000_000_000L) {
    private var reason: String? = null
    private var deadlineNs: Long? = null

    init { require(tailTimeoutNs > 0) }

    fun begin(value: String) {
        if (reason == null || (reason == "user" && value != "user")) reason = value
    }

    /** Null means keep recording; the existing photo watchdog bounds pending captures. */
    fun decision(nowNs: Long, photoPending: Boolean, photoBoundaryNs: Long?,
                 lastQueuedPreviewNs: Long?, previewAvailable: Boolean): String? {
        val end = reason ?: return null
        if (end != "user") return "interrupted_end"
        if (!previewAvailable) return "preview_unavailable"
        if (photoPending) return null
        if (photoBoundaryNs == null) return "no_saved_photo"
        if (lastQueuedPreviewNs != null && lastQueuedPreviewNs > photoBoundaryNs)
            return "following_preview_queued"
        val deadline = deadlineNs ?: (nowNs + tailTimeoutNs).also { deadlineNs = it }
        return if (nowNs >= deadline) "preview_tail_timeout" else null
    }
}
