package org.distancemeasuring.capture

import org.json.JSONArray
import org.json.JSONObject

data class KeyframeConfig(
    val minTracks: Int = 24, val gridRows: Int = 6, val gridCols: Int = 8,
    val minOccupiedCells: Int = 6, val minParallaxFraction: Double = 3.0 / 1600,
) {
    init {
        require(minTracks > 0 && gridRows > 0 && gridCols > 0)
        require(minOccupiedCells in 1..gridRows * gridCols)
        require(minParallaxFraction.isFinite() && minParallaxFraction > 0)
    }

    fun json(): JSONObject = JSONObject().put("min_tracks", minTracks)
        .put("grid_rows", gridRows).put("grid_cols", gridCols)
        .put("min_occupied_cells", minOccupiedCells).put("min_parallax_fraction", minParallaxFraction)
}

data class KeyframeDecision(val candidate: Boolean, val score: Double,
                            val reasons: List<String>, val thresholdPx: Double) {
    val label: String get() = if (candidate) "candidate keyframe" else "skip"
    fun json(): JSONObject = JSONObject().put("candidate", candidate).put("label", label)
        .put("score", score).put("reasons", JSONArray(reasons)).put("parallax_threshold_px", thresholdPx)
}

/** Same pair-wise pre-geometry policy as Python; candidates do not certify poses. */
object KeyframePolicy {
    fun score(tracks: Int, sourceCells: Int, targetCells: Int, medianParallaxPx: Double?,
              width: Int, height: Int, rotationAvailable: Boolean,
              config: KeyframeConfig = KeyframeConfig()): KeyframeDecision {
        require(tracks >= 0 && sourceCells >= 0 && targetCells >= 0 && width > 0 && height > 0)
        require(maxOf(sourceCells, targetCells) <= minOf(tracks, config.gridRows * config.gridCols))
        require(medianParallaxPx == null || medianParallaxPx.isFinite() && medianParallaxPx >= 0)
        val threshold = config.minParallaxFraction * maxOf(width, height)
        val reasons = mutableListOf<String>()
        if (tracks < config.minTracks) reasons += "insufficient_tracks"
        if (minOf(sourceCells, targetCells) < config.minOccupiedCells) reasons += "insufficient_coverage"
        if (!rotationAvailable) reasons += "rotation_unavailable"
        if (medianParallaxPx == null) reasons += "parallax_unavailable"
        else if (medianParallaxPx < threshold) reasons += "insufficient_parallax"
        val score = if (rotationAvailable && medianParallaxPx != null) minOf(1.0,
            tracks.toDouble() / config.minTracks,
            minOf(sourceCells, targetCells).toDouble() / config.minOccupiedCells,
            medianParallaxPx / threshold) else 0.0
        return KeyframeDecision(reasons.isEmpty(), score,
            reasons.ifEmpty { listOf("supported_motion_candidate") }, threshold)
    }
}
