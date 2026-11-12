package org.distancemeasuring.capture

import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.util.ArrayDeque
import java.util.Locale
import java.util.concurrent.CompletableFuture

data class PreviewFrameExport(
    val frame: PreviewLuma,
    val metadata: JSONObject,
    val intrinsics: JSONObject?,
    val clockDomain: String,
    val diagnostics: JSONObject,
    val geometrySignature: String,
    val resetReason: String? = null,
)

data class TrackingWriterResult(val state: String, val frames: Long, val dropped: Long,
                                val failureReason: String?)

/** One dedicated I/O worker, a bounded pixel queue, a bounded control queue and one drop range.
 * No filesystem calls or per-frame executor tasks run on producer/sensor threads.
 */
class TrackingFrameWriter(
    private val directory: File,
    private val sessionId: String,
    private val startNs: Long,
    private val config: HybridCaptureConfig,
    private val clock: () -> Long,
    private val encode: (PreviewLuma, File) -> Unit,
    unavailableReason: String? = null,
) {
    private sealed interface Work
    private data class FrameWork(val id: Long, val value: PreviewFrameExport,
                                 val reset: String?, val offeredNs: Long) : Work
    private data class EventWork(val row: JSONObject) : Work
    private data class DropWork(val first: Long, var count: Long,
                                val reasons: MutableSet<String>) : Work
    private val lock = Object()
    private val queue = ArrayDeque<Work>()
    private var queuedFrames = 0
    private var queuedEvents = 0
    private var pendingDrop: DropWork? = null
    private var pendingReset: String? = null
    private var nextFrame = 0L
    private var closing = false
    private var failed = false
    private var exportUnavailable = unavailableReason
    private var interruptedReason: String? = null
    private var finishReason: String? = null
    private var lastCameraNs = -1L
    private var lastCameraClock: String? = null
    val completion = CompletableFuture<TrackingWriterResult>()
    @Volatile var message = unavailableReason?.let { "Tracking unavailable: $it" } ?: "Saving tracking frames"
        private set
    // Worker-owned file state.
    private var sequence = 0L
    private var saved = 0L
    private var dropped = 0L
    private var resets = 0L
    private var captures = 0L
    private var previousGeometry: String? = null
    private var resetAfterFailure: String? = null
    private var lastAppendNs = startNs

    init { Thread(::run, "tracking-frame-writer").start() }

    fun offer(value: PreviewFrameExport): Boolean = synchronized(lock) {
        if (closing || failed || exportUnavailable != null) return false
        val frame = value.frame
        // The analyzer can receive a buffered frame from before Start was pressed.
        val id = nextFrame++
        if (value.resetReason != null) pendingReset = value.resetReason
        val invalidTime = frame.receivedTimestampNs <= startNs || frame.timestampNs <= 0 ||
            (value.clockDomain == "android_elapsed_realtime_ns" &&
                (frame.timestampNs <= startNs || frame.timestampNs > frame.receivedTimestampNs))
        val reversedTime = lastCameraClock == value.clockDomain && frame.timestampNs <= lastCameraNs
        if (invalidTime || reversedTime || queuedFrames >= config.writerQueueCapacity) {
            dropLocked(id, if (invalidTime) "frame_outside_timeline" else if (reversedTime)
                "camera_timestamp_not_increasing" else "writer_queue_full")
            return false
        }
        flushDropLocked()
        // JSONObject ownership crosses workers only as a private serialized snapshot.
        val owned = value.copy(metadata = JSONObject(value.metadata.toString()),
            intrinsics = value.intrinsics?.let { JSONObject(it.toString()) },
            diagnostics = JSONObject(value.diagnostics.toString()))
        queue.add(FrameWork(id, owned, pendingReset, clock())); pendingReset = null
        queuedFrames++
        lastCameraNs = frame.timestampNs; lastCameraClock = value.clockDomain
        lock.notifyAll()
        true
    }

    fun capture(scheduledNs: Long, ticket: PhotoTicket? = null, reason: String? = null,
                missedCount: Long = 0, lastSkippedNs: Long? = null): Boolean = synchronized(lock) {
        if (closing || failed) return false
        if (queuedEvents >= config.diagnosticQueueCapacity) {
            // Stop accepting work instead of hiding an unbounded diagnostic backlog.
            failed = true; interruptedReason = "diagnostic_queue_full"
            message = "Tracking export interrupted: diagnostic queue full"
            lock.notifyAll()
            return false
        }
        val row = JSONObject().put("type", "capture").put("scheduled_timestamp_ns", scheduledNs)
            .put("status", if (ticket == null) "skipped" else "requested")
            .put("image_index", ticket?.index ?: JSONObject.NULL)
            .put("request_timestamp_ns", ticket?.requestNs ?: JSONObject.NULL)
            .put("reason", if (ticket == null) checkNotNull(reason) else JSONObject.NULL)
        if (missedCount > 0) row.put("missed_tick_count", missedCount)
            .put("last_skipped_timestamp_ns", lastSkippedNs)
        queue.add(EventWork(row)); queuedEvents++
        lock.notifyAll()
        true
    }

    fun unavailable(reason: String) = synchronized(lock) {
        exportUnavailable = reason
        message = "Tracking stream unavailable: $reason"
    }

    /** Asynchronous drain; metadata/PNG writes never block the sensor worker at End. */
    fun finish(reason: String? = null): CompletableFuture<TrackingWriterResult> = synchronized(lock) {
        if (!closing) { closing = true; finishReason = reason; lock.notifyAll() }
        completion
    }

    internal fun queuedPixelCount() = synchronized(lock) { queuedFrames }

    private fun dropLocked(id: Long, reason: String) {
        val old = pendingDrop
        if (old == null) pendingDrop = DropWork(id, 1, mutableSetOf(reason))
        else { check(old.first + old.count == id); old.count++; old.reasons.add(reason) }
        lock.notifyAll()
    }

    private fun flushDropLocked() {
        pendingDrop?.let { queue.add(it); pendingDrop = null }
    }

    private fun next(): Work? = synchronized(lock) {
        while (queue.isEmpty() && pendingDrop == null && !closing) lock.wait()
        if (queue.isEmpty()) flushDropLocked()
        val item = queue.pollFirst()
        when (item) {
            is FrameWork -> queuedFrames--
            is EventWork -> queuedEvents--
            else -> Unit
        }
        item
    }

    private fun run() {
        var result: TrackingWriterResult
        try {
            val tracking = File(directory, "tracking")
            check(File(tracking, "frames").mkdirs()) { "Cannot create tracking frame directory" }
            File(tracking, "events.jsonl").bufferedWriter().use { output ->
                saveManifest("recording", null)
                fun append(row: JSONObject) {
                    val now = maxOf(clock(), startNs, lastAppendNs)
                    row.put("sequence", sequence).put("recorded_timestamp_ns", now)
                    output.write(row.toString()); output.newLine(); output.flush()
                    sequence++; lastAppendNs = now
                }
                while (true) {
                    when (val item = next() ?: break) {
                        is FrameWork -> writeFrame(item, ::append)
                        is DropWork -> {
                            append(dropRow(item)); dropped += item.count
                        }
                        is EventWork -> { append(item.row); captures++ }
                    }
                }
                val reason = synchronized(lock) { interruptedReason ?: exportUnavailable ?: finishReason }
                val state = if (reason == null) "complete" else if (saved == 0L &&
                    synchronized(lock) { exportUnavailable != null && interruptedReason == null })
                    "unavailable" else "interrupted"
                saveManifest(state, reason)
                result = TrackingWriterResult(state, saved, dropped, reason)
            }
        } catch (error: Exception) {
            val reason = "tracking_storage_error: ${error.message}"
            synchronized(lock) { failed = true; closing = true }
            try { saveManifest("interrupted", reason) } catch (_: Exception) { /* Storage may be full. */ }
            result = TrackingWriterResult("interrupted", saved, dropped, reason)
        }
        message = if (result.state == "complete") "Tracking saved: ${result.frames} frames, ${result.dropped} drops"
            else "Tracking ${result.state}: ${result.failureReason}"
        completion.complete(result)
    }

    private fun dropRow(drop: DropWork) = JSONObject().put("type", "drop")
        .put("first_frame_id", drop.first).put("count", drop.count)
        .put("reason", drop.reasons.sorted().joinToString(","))

    private fun writeFrame(item: FrameWork, append: (JSONObject) -> Unit) {
        val name = "tracking/frames/frame_%06d.png".format(Locale.ROOT, item.id)
        val target = File(directory, name)
        val temporary = File(target.parentFile, ".frame_${item.id}.pending.png")
        val exportStartNs = clock()
        try {
            encode(item.value.frame, temporary)
            check(temporary.isFile && temporary.length() > 0 && temporary.renameTo(target)) {
                "Cannot publish tracking PNG"
            }
        } catch (error: Exception) {
            temporary.delete(); target.delete()
            synchronized(lock) { interruptedReason = "frame_storage_error: ${error.message}" }
            message = "Tracking export interrupted: frame storage failed"
            resetAfterFailure = item.reset ?: "frame_storage_error"
            append(dropRow(DropWork(item.id, 1, mutableSetOf("frame_storage_error"))))
            dropped++
            return
        }
        val value = item.value; val frame = value.frame
        value.diagnostics.put("writer_queue_wait_ms", maxOf(0L, exportStartNs - item.offeredNs) / 1e6)
            .put("png_export_runtime_ms", maxOf(0L, clock() - exportStartNs) / 1e6)
        val reset = item.reset ?: resetAfterFailure ?: if (previousGeometry == null) "session_start"
            else if (previousGeometry != value.geometrySignature) "camera_geometry_changed" else null
        if (reset != null) {
            append(JSONObject().put("type", "reset").put("next_frame_id", item.id).put("reason", reset))
            resets++
        }
        resetAfterFailure = null; previousGeometry = value.geometrySignature
        val row = JSONObject().put("type", "frame").put("frame_id", item.id).put("filename", name)
            .put("timestamp_ns", frame.timestampNs).put("clock_domain", value.clockDomain)
            .put("received_timestamp_ns", frame.receivedTimestampNs)
            .put("buffer_size", JSONArray(listOf(frame.bufferWidth, frame.bufferHeight)))
            .put("buffer_crop_region", JSONArray(listOf(frame.cropLeft, frame.cropTop,
                frame.cropLeft + frame.cropWidth, frame.cropTop + frame.cropHeight)))
            .put("sample_step", frame.sampleStep).put("metadata", value.metadata)
            .put("intrinsics", value.intrinsics ?: JSONObject.NULL)
            .put("intrinsics_unavailable_reason", if (value.intrinsics == null) "preview_camera_metadata_missing" else JSONObject.NULL)
            .put("diagnostics", value.diagnostics)
        try { append(row) } catch (error: Exception) { target.delete(); throw error }
        saved++
    }

    private fun saveManifest(state: String, reason: String?) {
        val settings = JSONObject().put("requested_width", 640).put("requested_height", 480)
            .put("max_analysis_fps", 10).put("still_interval_s", config.stillIntervalMs / 1000.0)
            .put("writer_queue_capacity", config.writerQueueCapacity)
            .put("diagnostic_queue_capacity", config.diagnosticQueueCapacity)
            .put("automatic_stills", config.automaticStills)
        val manifest = JSONObject().put("schema_version", 1).put("session_id", sessionId)
            .put("state", state).put("failure_reason", reason ?: JSONObject.NULL)
            .put("records_file", "tracking/events.jsonl").put("pixel_format", "gray8_png")
            .put("settings", settings).put("counts", JSONObject().put("frames", saved)
                .put("dropped_frames", dropped).put("resets", resets).put("capture_events", captures))
        writeCaptureJson(File(directory, "tracking.json"), manifest)
    }
}
