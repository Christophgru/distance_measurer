package org.distancemeasuring.capture

data class HybridCaptureConfig(
    val automaticStills: Boolean = true,
    val stillIntervalMs: Long = 1_000,
    val writerQueueCapacity: Int = 4,
    val diagnosticQueueCapacity: Int = 32,
) {
    init {
        require(stillIntervalMs in 250..60_000) { "Photo interval must be 0.25–60 seconds" }
        require(writerQueueCapacity in 1..16 && diagnosticQueueCapacity in 2..128)
    }
}

data class StillTick(val scheduledNs: Long, val missedCount: Long, val firstMissedNs: Long,
                     val lastMissedNs: Long)

/** An anchored monotonic schedule: return one latest due tick, never a catch-up burst. */
class PeriodicStillSchedule(startNs: Long, intervalMs: Long) {
    private val intervalNs = intervalMs * 1_000_000
    private var nextNs = startNs + intervalNs
    private var stopped = false

    init { require(startNs >= 0 && intervalMs in 250..60_000) }

    fun due(nowNs: Long): StillTick? {
        if (stopped || nowNs < nextNs) return null
        val first = nextNs
        val missed = (nowNs - nextNs) / intervalNs
        val latest = nextNs + missed * intervalNs
        nextNs = latest + intervalNs
        return StillTick(latest, missed, first, latest - intervalNs)
    }

    fun delayMs(nowNs: Long) = maxOf(1, (nextNs - nowNs + 999_999) / 1_000_000)
    fun stop() { stopped = true }
}

/** Worker-owned epoch gate: stale UI callbacks cannot request a photo in a new session. */
class AutomaticStillGate {
    var pending: Pair<String, Long>? = null
        private set
    fun reserve(path: String, scheduledNs: Long) {
        check(pending == null)
        pending = Pair(path, scheduledNs)
    }
    fun consume(path: String, scheduledNs: Long): Boolean {
        if (pending != Pair(path, scheduledNs)) return false
        pending = null
        return true
    }
    fun cancel(): Pair<String, Long>? = pending.also { pending = null }
}
