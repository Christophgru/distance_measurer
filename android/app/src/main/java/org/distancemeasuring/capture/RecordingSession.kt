package org.distancemeasuring.capture

import org.json.JSONObject
import java.io.File
import java.io.FileOutputStream
import java.nio.file.AtomicMoveNotSupportedException
import java.nio.file.Files
import java.nio.file.StandardCopyOption
import java.time.Instant
import java.time.ZoneOffset
import java.time.format.DateTimeFormatter
import java.util.PriorityQueue
import java.util.UUID

data class SensorSample(
    val timestampNs: Long,
    val type: String,
    val x: Float,
    val y: Float,
    val z: Float,
    val w: Float?,
    val accuracy: Int,
)

data class PhotoTicket(val index: Int, val requestNs: Long, val directory: File) {
    val filename = "images/image_%03d.jpg".format(java.util.Locale.ROOT, index)
    val file = File(directory, filename)
    // CameraX writes here first; only successful, registered images are published.
    val temporaryFile = File(directory, "images/.image_$index.pending.jpg")
}

/** Session file/state contract. All calls are confined to the recorder's worker thread. */
class RecordingSession(
    root: File,
    device: JSONObject,
    availability: Map<String, Boolean>,
    private val clock: () -> Long,
    sessionId: String = newSessionId(),
) {
    val directory = File(root, sessionId)
    val startNs = clock()
    var imageCount = 0
        private set
    var ending = false
        private set
    var closed = false
        private set
    val sampleCount: Int get() = sensorCounts.values.sum()
    val hasPendingPhoto: Boolean get() = pending.isNotEmpty()

    private val sensorCounts = availability.keys.associateWith { 0 }.toMutableMap()
    private val sensorAvailability = availability.toMutableMap()
    private val lastSeen = mutableMapOf<String, Long>()
    private val pending = mutableMapOf<Int, PhotoTicket>()
    private val samples = PriorityQueue(compareBy<SensorSample> { it.timestampNs }.thenBy { it.type })
    private var lastWrittenNs = startNs
    private var nextImage = 0
    private var droppedSamples = 0
    private var lastFlushNs = startNs
    private var endReason = "user"
    private val sensorWriter: java.io.BufferedWriter
    private val eventWriter: java.io.BufferedWriter
    private val metadata = JSONObject()
        .put("schema_version", 1)
        .put("session_id", sessionId)
        .put("device", device)
        .put("created_at_utc", Instant.now().toString())
        .put("timeline_start_ns", startNs)
        .put("clock_domain", "android_elapsed_realtime_ns")
        .put("image_timestamp_source", "button_request")
        .put("sensor_order", "global_nondecreasing_per_stream_strictly_increasing")
        .put("sensor_reorder_window_ns", REORDER_WINDOW_NS)
        .put("notes", "")

    init {
        require(sessionId.matches(Regex("[A-Za-z0-9_-]+"))) { "Invalid session ID" }
        check(root.isDirectory || root.mkdirs()) { "Cannot create session root: $root" }
        check(directory.mkdir()) { "Session already exists or cannot be created: $directory" }
        check(File(directory, "images").mkdir()) { "Cannot create images directory" }
        sensorWriter = File(directory, "sensors.csv").bufferedWriter()
        try {
            eventWriter = File(directory, "events.jsonl").bufferedWriter()
        } catch (error: Exception) {
            sensorWriter.close()
            throw error
        }
        try {
            sensorWriter.write("timestamp_ns,sensor_type,x,y,z,w,accuracy\n")
            sensorWriter.flush()
            event(JSONObject().put("type", "timeline_start").put("timestamp_ns", startNs))
            saveMetadata("recording")
        } catch (error: Exception) {
            closeStreams()
            throw error
        }
    }

    fun setSensorAvailable(type: String, available: Boolean) {
        check(!closed)
        sensorAvailability[type] = available
        saveMetadata(if (ending) "ending" else "recording")
    }

    fun recordSensor(sample: SensorSample) {
        if (closed) return
        val now = clock()
        if (sensorAvailability[sample.type] != true || sample.timestampNs <= startNs ||
            sample.timestampNs > now || sample.timestampNs <= (lastSeen[sample.type] ?: startNs) ||
            !sample.x.isFinite() || !sample.y.isFinite() || !sample.z.isFinite() ||
            (sample.w != null && !sample.w.isFinite())
        ) {
            droppedSamples++
            return
        }
        lastSeen[sample.type] = sample.timestampNs
        samples.add(sample)
        flushSamples(now - REORDER_WINDOW_NS)
    }

    fun beginPhoto(requestNs: Long): PhotoTicket? {
        if (closed || ending || hasPendingPhoto) return null
        require(requestNs > startNs && requestNs <= clock()) { "Image request outside timeline" }
        val ticket = PhotoTicket(nextImage++, requestNs, directory)
        pending[ticket.index] = ticket
        event(JSONObject().put("type", "image_request").put("index", ticket.index)
            .put("timestamp_ns", requestNs))
        return ticket
    }

    fun completePhoto(ticket: PhotoTicket, callbackNs: Long, imageMetadata: JSONObject): Boolean {
        if (closed || pending[ticket.index] != ticket) {
            ticket.temporaryFile.delete()
            return false
        }
        require(callbackNs >= ticket.requestNs && callbackNs <= clock()) { "Invalid callback time" }
        check(ticket.temporaryFile.isFile && ticket.temporaryFile.length() > 0) { "Missing JPEG" }
        check(ticket.temporaryFile.renameTo(ticket.file)) { "Cannot publish JPEG" }
        try {
            event(JSONObject().put("type", "image").put("index", ticket.index)
                .put("filename", ticket.filename)
                // This is button-request time, not sensor exposure time. Both clocks below
                // use elapsedRealtimeNanos, as do Android SensorEvent timestamps.
                .put("timestamp_ns", ticket.requestNs)
                .put("request_timestamp_ns", ticket.requestNs)
                .put("callback_timestamp_ns", callbackNs)
                .put("clock_domain", "android_elapsed_realtime_ns")
                .put("timestamp_source", "button_request")
                .put("metadata", imageMetadata))
        } catch (error: Exception) {
            ticket.file.delete()
            throw error
        }
        pending.remove(ticket.index)
        imageCount++
        return true
    }

    fun failPhoto(ticket: PhotoTicket, message: String) {
        ticket.temporaryFile.delete()
        if (closed || pending.remove(ticket.index) == null) return
        event(JSONObject().put("type", "image_error").put("index", ticket.index)
            .put("timestamp_ns", clock()).put("message", message))
    }

    fun requestEnd(reason: String) {
        if (closed || ending) return
        ending = true
        endReason = reason
        saveMetadata("ending")
    }

    fun finish() {
        check(ending && !hasPendingPhoto && !closed) { "Session cannot finish yet" }
        val endNs = clock()
        check(endNs > startNs) { "End must be after start" }
        try {
            flushSamples(endNs - 1)
            samples.clear()
            sensorWriter.flush()
            event(JSONObject().put("type", "timeline_end").put("timestamp_ns", endNs)
                .put("reason", endReason))
            metadata.put("timeline_end_ns", endNs).put("end_reason", endReason)
            saveMetadata(if (endReason == "user") "complete" else "interrupted")
        } finally {
            closed = true
            closeStreams()
        }
    }

    /** Storage failure path: release handles, retain an explicitly unfinished recording. */
    fun abort() {
        if (closed) return
        closed = true
        pending.values.forEach { it.temporaryFile.delete() }
        pending.clear()
        closeStreams()
    }

    private fun flushSamples(throughNs: Long) {
        while (samples.peek()?.let { it.timestampNs <= throughNs } == true) {
            val sample = samples.remove()
            if (sample.timestampNs < lastWrittenNs) {
                droppedSamples++
                continue
            }
            sensorWriter.write("${sample.timestampNs},${sample.type},${sample.x},${sample.y}," +
                "${sample.z},${sample.w ?: ""},${sample.accuracy}\n")
            lastWrittenNs = sample.timestampNs
            sensorCounts[sample.type] = sensorCounts.getValue(sample.type) + 1
        }
        // Bounded buffering limits data loss on process termination without per-sample flushes.
        if (clock() - lastFlushNs >= 500_000_000) {
            sensorWriter.flush()
            lastFlushNs = clock()
        }
    }

    private fun event(value: JSONObject) {
        eventWriter.write(value.toString())
        eventWriter.newLine()
        eventWriter.flush()
    }

    private fun saveMetadata(state: String) {
        metadata.put("state", state)
            .put("sensors", JSONObject(sensorAvailability.toMap()))
            .put("sensor_counts", JSONObject(sensorCounts.toMap()))
            .put("image_count", imageCount)
            .put("dropped_sensor_samples", droppedSamples)
        val temporary = File(directory, "session.json.tmp")
        FileOutputStream(temporary).use { output ->
            output.write(metadata.toString(2).toByteArray(Charsets.UTF_8))
            output.fd.sync()
        }
        val target = File(directory, "session.json").toPath()
        try {
            Files.move(temporary.toPath(), target, StandardCopyOption.ATOMIC_MOVE,
                StandardCopyOption.REPLACE_EXISTING)
        } catch (_: AtomicMoveNotSupportedException) {
            Files.move(temporary.toPath(), target, StandardCopyOption.REPLACE_EXISTING)
        }
    }

    private fun closeStreams() {
        try {
            sensorWriter.close()
        } finally {
            eventWriter.close()
        }
    }

    companion object {
        private const val REORDER_WINDOW_NS = 100_000_000L
        private val ID_FORMAT = DateTimeFormatter.ofPattern("yyyy-MM-dd_HHmmss_SSS")
            .withZone(ZoneOffset.UTC)

        private fun newSessionId() = "${ID_FORMAT.format(Instant.now())}_${UUID.randomUUID().toString().take(8)}"
    }
}
