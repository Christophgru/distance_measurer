package org.distancemeasuring.capture

import org.json.JSONObject

/** Bounded cross-thread join by exact sensor timestamp; never nearest preview frame. */
class FrameMetadataStore(private val capacity: Int = 120) {
    private val frames = linkedMapOf<Long, JSONObject>()
    private val stillFrames = linkedMapOf<Long, JSONObject>()

    init { require(capacity > 0) }

    @Synchronized fun put(timestampNs: Long, metadata: JSONObject, isStill: Boolean = false) {
        if (timestampNs <= 0) return
        frames[timestampNs] = JSONObject(metadata.toString())
        if (isStill) {
            stillFrames[timestampNs] = JSONObject(metadata.toString())
            while (stillFrames.size > 8) stillFrames.remove(stillFrames.keys.first())
        }
        while (frames.size > capacity) frames.remove(frames.keys.first())
    }

    @Synchronized fun contains(timestampNs: Long) = frames.containsKey(timestampNs) || stillFrames.containsKey(timestampNs)

    /** Analysis must never consume metadata needed by an overlapping JPEG. */
    @Synchronized fun peek(timestampNs: Long): JSONObject? =
        (frames[timestampNs] ?: stillFrames[timestampNs])?.let { JSONObject(it.toString()) }

    @Synchronized fun take(timestampNs: Long): JSONObject? {
        val still = stillFrames.remove(timestampNs)
        return frames.remove(timestampNs) ?: still
    }
}
