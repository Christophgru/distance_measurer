package org.distancemeasuring.capture

import org.opencv.core.CvType
import org.opencv.core.Mat
import org.opencv.core.MatOfByte
import org.opencv.core.MatOfFloat
import org.opencv.core.MatOfPoint
import org.opencv.core.MatOfPoint2f
import org.opencv.core.Point
import org.opencv.core.Rect
import org.opencv.core.Scalar
import org.opencv.core.Size
import org.opencv.core.TermCriteria
import org.opencv.imgproc.Imgproc
import org.opencv.video.Video

data class PreviewTrackingConfig(
    val maxTracks: Int = 240, val minDistancePx: Int = 8, val qualityLevel: Double = .01,
    val fbThresholdPx: Double = 1.5, val replenishRatio: Double = .75,
    val keyframes: KeyframeConfig = KeyframeConfig(),
) {
    init {
        require(maxTracks > 0 && minDistancePx > 0)
        require(qualityLevel.isFinite() && qualityLevel > 0 && qualityLevel <= 1)
        require(fbThresholdPx.isFinite() && fbThresholdPx > 0)
        require(replenishRatio.isFinite() && replenishRatio > 0 && replenishRatio <= 1)
    }
}

data class PreviewTrackedPoint(val id: Long, val point: PreviewPoint, val age: Int)
data class PreviewFlowMatch(val source: PreviewTrackedPoint, val target: PreviewTrackedPoint, val fbError: Double)
data class PreviewFlowStep(val matches: List<PreviewFlowMatch>, val activeCount: Int,
                           val seededCount: Int, val detectedCells: List<Int>, val inputCount: Int,
                           val medianFbError: Double?, val flowMs: Double, val selectionMs: Double)

object PreviewFlowChecks {
    fun forwardUsable(point: PreviewPoint, status: Boolean, error: Double, width: Int, height: Int) =
        status && error.isFinite() && error >= 0 && point.inBounds(width, height)
    fun backwardError(source: PreviewPoint, returned: PreviewPoint, status: Boolean,
                      error: Double, width: Int, height: Int, threshold: Double): Double? {
        if (!forwardUsable(returned, status, error, width, height)) return null
        val distance = source.distance(returned)
        return distance.takeIf { it <= threshold }
    }
}

/** Only native Shi–Tomasi and pyramidal LK; all Mat ownership stays on one worker. */
class PreviewFlowTracker(private val config: PreviewTrackingConfig = PreviewTrackingConfig()) : AutoCloseable {
    private var previous: Mat? = null
    private var active = listOf<PreviewTrackedPoint>()
    private var nextId = 0L

    fun reset() { previous?.release(); previous = null; active = emptyList(); nextId = 0 }
    override fun close() = reset()

    fun step(frame: PreviewLuma): PreviewFlowStep {
        require(frame.pixels.size == frame.width * frame.height)
        require(frame.width >= config.keyframes.gridCols && frame.height >= config.keyframes.gridRows)
        if (previous != null && (previous!!.cols() != frame.width || previous!!.rows() != frame.height)) reset()
        val current = Mat(frame.height, frame.width, CvType.CV_8UC1)
        current.put(0, 0, frame.pixels)
        try {
            val inputCount = active.size
            val flowStart = System.nanoTime()
            val matches = previous?.let { if (active.isEmpty()) emptyList() else flow(it, current) } ?: emptyList()
            val flowMs = (System.nanoTime() - flowStart) / 1e6
            active = matches.map { it.target }
            val selectionStart = System.nanoTime()
            val cells = mutableListOf<Int>()
            val seeded = if (active.size < config.maxTracks * config.replenishRatio) replenish(current, cells) else 0
            val selectionMs = (System.nanoTime() - selectionStart) / 1e6
            previous?.release()
            previous = current
            return PreviewFlowStep(matches, active.size, seeded, cells, inputCount,
                previewMedian(matches.map { it.fbError }), flowMs, selectionMs)
        } catch (error: Exception) {
            current.release()
            reset()
            throw error
        }
    }

    private fun flow(first: Mat, second: Mat): List<PreviewFlowMatch> {
        val source = MatOfPoint2f(*active.map { Point(it.point.x, it.point.y) }.toTypedArray())
        val target = MatOfPoint2f(); val forwardStatus = MatOfByte(); val forwardError = MatOfFloat()
        val reverseSource = MatOfPoint2f(); val returned = MatOfPoint2f()
        val reverseStatus = MatOfByte(); val reverseError = MatOfFloat()
        try {
            val criteria = TermCriteria(TermCriteria.COUNT + TermCriteria.EPS, 30, .01)
            Video.calcOpticalFlowPyrLK(first, second, source, target, forwardStatus, forwardError,
                Size(31.0, 31.0), 4, criteria, 0, 1e-4)
            val pixels = target.toArray(); val status = forwardStatus.toArray(); val errors = forwardError.toArray()
            val rows = active.indices.filter { i -> pixels.getOrNull(i)?.let { p ->
                PreviewFlowChecks.forwardUsable(PreviewPoint(p.x, p.y), status.getOrNull(i)?.toInt() == 1,
                    errors.getOrNull(i)?.toDouble() ?: Double.NaN, second.cols(), second.rows())
            } == true }
            if (rows.isEmpty()) return emptyList()
            reverseSource.fromArray(*rows.map { pixels[it] }.toTypedArray())
            Video.calcOpticalFlowPyrLK(second, first, reverseSource, returned, reverseStatus, reverseError,
                Size(31.0, 31.0), 4, criteria, 0, 1e-4)
            val back = returned.toArray(); val ok = reverseStatus.toArray(); val err = reverseError.toArray()
            return rows.mapIndexedNotNull { k, i ->
                val p = back.getOrNull(k) ?: return@mapIndexedNotNull null
                val error = PreviewFlowChecks.backwardError(active[i].point, PreviewPoint(p.x, p.y),
                    ok.getOrNull(k)?.toInt() == 1, err.getOrNull(k)?.toDouble() ?: Double.NaN,
                    first.cols(), first.rows(), config.fbThresholdPx) ?: return@mapIndexedNotNull null
                PreviewFlowMatch(active[i], active[i].copy(point = PreviewPoint(pixels[i].x, pixels[i].y),
                    age = active[i].age + 1), error)
            }
        } finally {
            listOf(source, target, forwardStatus, forwardError, reverseSource, returned, reverseStatus, reverseError).forEach { it.release() }
        }
    }

    private fun replenish(gray: Mat, visited: MutableList<Int>): Int {
        val width = gray.cols(); val height = gray.rows()
        val rows = config.keyframes.gridRows; val cols = config.keyframes.gridCols
        val counts = IntArray(rows * cols)
        active.forEach { counts[previewCell(it.point, width, height, rows, cols)]++ }
        val mask = Mat(height, width, CvType.CV_8UC1, Scalar(255.0))
        val points = active.toMutableList()
        try {
            points.forEach { Imgproc.circle(mask, Point(it.point.x, it.point.y), config.minDistancePx, Scalar(0.0), -1) }
            for (cell in counts.indices) {
                val quota = config.maxTracks / counts.size + if (cell < config.maxTracks % counts.size) 1 else 0
                if (counts[cell] >= quota || points.size >= config.maxTracks) continue
                val row = cell / cols; val col = cell % cols
                val left = col * width / cols; val top = row * height / rows
                val right = (col + 1) * width / cols; val bottom = (row + 1) * height / rows
                if (right - left < 3 || bottom - top < 3) continue
                val roi = gray.submat(Rect(left, top, right - left, bottom - top))
                val roiMask = mask.submat(Rect(left, top, right - left, bottom - top))
                val corners = MatOfPoint()
                try {
                    visited += cell
                    Imgproc.goodFeaturesToTrack(roi, corners, quota - counts[cell], config.qualityLevel,
                        config.minDistancePx.toDouble(), roiMask, 3, false, .04)
                    for (corner in corners.toArray()) {
                        val p = PreviewPoint(corner.x + left, corner.y + top)
                        if (points.any { it.point.distance(p) < config.minDistancePx }) continue
                        if (points.size >= config.maxTracks) break
                        points += PreviewTrackedPoint(nextId++, p, 1)
                        Imgproc.circle(mask, Point(p.x, p.y), config.minDistancePx, Scalar(0.0), -1)
                    }
                } finally { corners.release(); roiMask.release(); roi.release() }
            }
        } finally { mask.release() }
        val added = points.size - active.size
        active = points
        return added
    }
}
