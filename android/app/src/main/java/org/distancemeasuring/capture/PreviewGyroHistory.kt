package org.distancemeasuring.capture

data class PreviewGyroSample(val timestampNs: Long, val x: Double, val y: Double, val z: Double)
data class PreviewGyroRotation(val matrix: DoubleArray?, val sampleCount: Int,
                               val maxGapNs: Long, val reason: String?)

/** Bounded sensor handoff; no gyro extrapolation, no bias/translation/scale estimate. */
class PreviewGyroHistory(private val capacity: Int = 2048) {
    private val samples = ArrayDeque<PreviewGyroSample>()
    private var startNs = 0L
    init { require(capacity >= 2) }
    @Synchronized fun begin(timestampNs: Long) { samples.clear(); startNs = timestampNs }
    @Synchronized fun add(sample: PreviewGyroSample): Boolean {
        if (sample.timestampNs <= startNs || (samples.lastOrNull()?.timestampNs ?: startNs) >= sample.timestampNs ||
            !listOf(sample.x, sample.y, sample.z).all { it.isFinite() }) return false
        samples.addLast(sample)
        while (samples.size > capacity) samples.removeFirst()
        return true
    }

    fun rotation(start: Long, end: Long, maxGapNs: Long = 250_000_000): PreviewGyroRotation {
        val snapshot = synchronized(this) { samples.toList() }
        if (start < 0 || end <= start) return PreviewGyroRotation(null, 0, 0, "invalid_frame_interval")
        if (snapshot.size < 2 || start < snapshot.first().timestampNs || end > snapshot.last().timestampNs)
            return PreviewGyroRotation(null, 0, 0, "gyro_not_bracketed")
        val left = snapshot.indexOfLast { it.timestampNs <= start }
        val right = snapshot.indexOfFirst { it.timestampNs >= end }
        val window = snapshot.subList(left, right + 1)
        val gap = window.zipWithNext().maxOf { (a, b) -> b.timestampNs - a.timestampNs }
        if (gap > maxGapNs) return PreviewGyroRotation(null, window.size, gap, "gyro_sample_gap")
        fun rate(t: Long): DoubleArray {
            val a = snapshot.indexOfLast { it.timestampNs <= t }
            if (snapshot[a].timestampNs == t) return doubleArrayOf(snapshot[a].x, snapshot[a].y, snapshot[a].z)
            val before = snapshot[a]; val after = snapshot[a + 1]
            val fraction = (t - before.timestampNs).toDouble() / (after.timestampNs - before.timestampNs)
            return doubleArrayOf(before.x + fraction * (after.x - before.x),
                before.y + fraction * (after.y - before.y), before.z + fraction * (after.z - before.z))
        }
        val times = listOf(start) + window.map { it.timestampNs }.filter { it > start && it < end } + end
        var active = PreviewRotation.identity()
        for ((a, b) in times.zipWithNext()) {
            val first = rate(a); val second = rate(b)
            val h = (b - a) / 1e9
            active = PreviewRotation.multiply(active, PreviewRotation.exponential(
                .5 * (first[0] + second[0]) * h, .5 * (first[1] + second[1]) * h, .5 * (first[2] + second[2]) * h))
        }
        if (!active.all { it.isFinite() }) return PreviewGyroRotation(null, window.size, gap, "nonfinite_gyro_rotation")
        return PreviewGyroRotation(PreviewRotation.transpose(active), window.size, gap, null)
    }
}
