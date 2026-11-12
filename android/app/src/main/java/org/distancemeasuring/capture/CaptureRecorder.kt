package org.distancemeasuring.capture

import android.content.Context
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import android.os.Build
import android.os.Handler
import android.os.HandlerThread
import android.os.Looper
import android.os.SystemClock
import androidx.camera.core.ImageCapture
import androidx.camera.core.ImageCaptureException
import androidx.camera.core.ImageProxy
import org.json.JSONObject
import java.io.File
import java.util.concurrent.Executor
import java.util.concurrent.atomic.AtomicLong
import kotlin.math.max
import kotlin.math.sqrt

/** Sensor callbacks, file writes, and session transitions share one worker queue. */
class CaptureRecorder(
    context: Context,
    private val requestAutomatic: (String, Long) -> Unit,
    private val stopPreview: (String, () -> Unit) -> Unit,
    private val closePreview: () -> Unit,
    private val onState: (RecordingState) -> Unit,
) :
    SensorEventListener {
    private val app = context.applicationContext
    private val manager = app.getSystemService(Context.SENSOR_SERVICE) as SensorManager
    private val worker = HandlerThread("capture-recorder").apply { start() }
    private val handler = Handler(worker.looper)
    private val main = Handler(Looper.getMainLooper())
    private var session: RecordingSession? = null
    private var destroying = false
    private var lastUiNs = 0L
    private var state = RecordingState()
    private var tracking: TrackingFrameWriter? = null
    private class PreviewExportBinding(val path: String, val writer: TrackingFrameWriter) {
        val lastQueuedNs = AtomicLong(-1)
        @Volatile var comparableClock: Boolean? = null
    }
    @Volatile private var exportBinding: PreviewExportBinding? = null
    @Volatile private var cameraReady = false
    @Volatile private var analysisReady = false
    @Volatile private var analysisUnavailableReason = "analysis_stream_unavailable"
    private var schedule: PeriodicStillSchedule? = null
    private val automaticGate = AutomaticStillGate()
    private var pendingAutomaticCallback: Runnable? = null
    private var previewDrained = false
    private var stoppingPreview = false
    private var endReason = "user"
    private var previewEnd = PreviewEndPolicy()
    private val previewTailTick = Runnable { guarded { if (!finishIfReady()) publish() } }
    private val automaticTick = Runnable { guarded { tickAutomatic() } }
    val previewGyro = PreviewGyroHistory()
    private val sensors = mapOf(
        "accelerometer" to manager.getDefaultSensor(Sensor.TYPE_ACCELEROMETER),
        "gyroscope" to manager.getDefaultSensor(Sensor.TYPE_GYROSCOPE),
        "rotation_vector" to manager.getDefaultSensor(Sensor.TYPE_ROTATION_VECTOR),
        "game_rotation_vector" to manager.getDefaultSensor(Sensor.TYPE_GAME_ROTATION_VECTOR),
        "magnetometer" to manager.getDefaultSensor(Sensor.TYPE_MAGNETIC_FIELD),
    )

    fun setCameraAvailability(photos: Boolean, analysis: Boolean, reason: String) {
        cameraReady = photos; analysisReady = analysis; analysisUnavailableReason = reason
        if (!analysis) exportBinding?.writer?.unavailable(reason)
    }

    fun offerPreview(path: String, value: PreviewFrameExport) {
        val binding = exportBinding ?: return
        if (binding.path != path) return
        if (value.diagnostics.optString("unavailable_reason") == "opencv_unavailable")
            binding.writer.unavailable("opencv_unavailable")
        else if (binding.writer.offer(value)) {
            binding.comparableClock = value.clockDomain == "android_elapsed_realtime_ns"
            if (binding.comparableClock == true) binding.lastQueuedNs.set(value.frame.timestampNs)
        }
    }

    fun start(config: HybridCaptureConfig = HybridCaptureConfig()) = dispatch {
        if (session != null || destroying) {
            publish()
            return@dispatch
        }
        check(sensors["accelerometer"] != null && sensors["gyroscope"] != null) {
            "An accelerometer and gyroscope are required on this device"
        }
        val root = File(checkNotNull(app.getExternalFilesDir(null)) {
            "Phone storage is unavailable"
        }, "sessions")
        val recording = RecordingSession(root, JSONObject()
            .put("manufacturer", Build.MANUFACTURER).put("model", Build.MODEL)
            .put("android_api", Build.VERSION.SDK_INT)
            .put("app_version", BuildConfig.VERSION_NAME)
            .put("app_version_code", BuildConfig.VERSION_CODE),
            sensors.mapValues { it.value != null }, SystemClock::elapsedRealtimeNanos)
        session = recording
        val writer = TrackingFrameWriter(recording.directory, recording.directory.name,
            recording.startNs, config, SystemClock::elapsedRealtimeNanos, PreviewPng::write,
            if (analysisReady) null else analysisUnavailableReason)
        tracking = writer; exportBinding = PreviewExportBinding(recording.directory.absolutePath, writer)
        previewEnd = PreviewEndPolicy()
        previewDrained = false; stoppingPreview = false; endReason = "user"
        schedule = if (config.automaticStills) PeriodicStillSchedule(recording.startNs, config.stillIntervalMs) else null
        schedule?.let { handler.postDelayed(automaticTick, it.delayMs(SystemClock.elapsedRealtimeNanos())) }
        previewGyro.begin(recording.startNs)
        sensors.forEach { (name, sensor) ->
            // 100 Hz requested, no hardware batching. Actual rates are device dependent.
            val registered = sensor != null && manager.registerListener(this, sensor, 10_000, handler)
            recording.setSensorAvailable(name, registered)
            check(registered || name !in setOf("accelerometer", "gyroscope")) {
                "Cannot register $name"
            }
        }
        publish()
    }

    fun takePhoto(
        imageCapture: ImageCapture, cameraMetadata: JSONObject, targetRotation: Int,
        geometry: CaptureGeometry,
        automaticPath: String? = null, scheduledNs: Long? = null,
    ) {
        // Sample the button timestamp on the UI thread, before scheduling storage work.
        val requestNs = SystemClock.elapsedRealtimeNanos()
        dispatch {
            val recording = session
            if (scheduledNs != null) {
                if (recording?.directory?.absolutePath != automaticPath ||
                    !automaticGate.consume(automaticPath ?: "", scheduledNs)) return@dispatch
                pendingAutomaticCallback = null
                if (!cameraReady) {
                    tracking?.capture(scheduledNs, reason = "camera_unavailable")
                    return@dispatch
                }
            }
            if (recording == null) {
                publish()
                return@dispatch
            }
            val ticket = recording.beginPhoto(requestNs)
            if (ticket == null) {
                if (scheduledNs != null) tracking?.capture(scheduledNs, reason = "camera_busy")
                publish()
                return@dispatch
            }
            if (scheduledNs != null) tracking?.capture(scheduledNs, ticket)
            publish()
            val timeout = Runnable {
                guarded {
                    recording.failPhoto(ticket, "Camera capture timed out after 30 seconds")
                    if (session === recording) {
                        if (!finishIfReady()) publish("Photo failed: camera timed out")
                    }
                }
            }
            handler.postDelayed(timeout, 30_000)
            // If destruction/timeout already closed the worker, only delete late output.
            val callbackExecutor = Executor { command ->
                // Deliver late callbacks too, so their ImageProxy is always closed.
                if (!handler.post(command)) command.run()
            }
            main.post {
                if (!ticket.captureAllowed) return@post
                try {
                    imageCapture.targetRotation = targetRotation
                    imageCapture.takePicture(
                        callbackExecutor,
                        object : ImageCapture.OnImageCapturedCallback() {
                            override fun onCaptureSuccess(image: ImageProxy) {
                                val callbackNs = SystemClock.elapsedRealtimeNanos()
                                handler.removeCallbacks(timeout)
                                val saveImage = Runnable {
                                    try {
                                        guarded {
                                            if (recording.closed || session !== recording || !ticket.captureAllowed) {
                                                ticket.temporaryFile.delete()
                                                return@guarded
                                            }
                                            try {
                                                val metadata = PhotoMetadata.saveCapturedJpeg(
                                                    ticket.temporaryFile, image, cameraMetadata, targetRotation,
                                                    geometry.frames.take(image.imageInfo.timestamp))
                                                recording.completePhoto(ticket, callbackNs, metadata)
                                                if (!finishIfReady()) publish()
                                            } catch (error: Exception) {
                                                recording.failPhoto(ticket, error.message ?: "Invalid JPEG")
                                                if (!finishIfReady()) publish("Photo failed: ${error.message}")
                                            }
                                        }
                                    } finally {
                                        image.close()
                                    }
                                }
                                // Camera2 and ImageProxy callbacks may arrive in either
                                // order. Yield the sensor worker for a bounded join wait.
                                if (geometry.frames.contains(image.imageInfo.timestamp)) saveImage.run()
                                else if (!handler.postDelayed(saveImage, 500)) saveImage.run()
                            }

                            override fun onError(error: ImageCaptureException) {
                                handler.removeCallbacks(timeout)
                                guarded {
                                    recording.failPhoto(ticket, error.message ?: "Camera error")
                                    if (session === recording && !finishIfReady()) {
                                        publish("Photo failed: ${error.message}")
                                    }
                                }
                            }
                        },
                    )
                } catch (error: Exception) {
                    dispatch {
                        handler.removeCallbacks(timeout)
                        recording.failPhoto(ticket, error.message ?: "Camera unavailable")
                        if (session === recording && !finishIfReady()) publish("Photo failed")
                    }
                }
            }
        }
    }

    fun end(reason: String = "user") = dispatch {
        session?.let { beginEnd(it, reason) }
        if (!finishIfReady()) publish()
    }

    fun destroy() = dispatch {
        destroying = true
        session?.let { beginEnd(it, "activity_destroyed") }
        if (session == null) finishDestroy() else if (!finishIfReady()) publish()
    }

    private fun tickAutomatic() {
        val recording = session ?: return
        val active = schedule ?: return
        if (recording.ending || recording.closed) return
        val now = SystemClock.elapsedRealtimeNanos()
        active.due(now)?.let { tick ->
            // Resolve an older UI request before recording any newer ticks, preserving
            // capture-event order even if the main thread is stalled.
            cancelPendingAutomatic("request_delayed")
            if (tick.missedCount > 0) tracking?.capture(tick.firstMissedNs,
                reason = "scheduler_delayed", missedCount = tick.missedCount,
                lastSkippedNs = tick.lastMissedNs)
            val reason = when {
                recording.hasPendingPhoto -> "camera_busy"
                !cameraReady -> "camera_unavailable"
                else -> null
            }
            if (reason != null) tracking?.capture(tick.scheduledNs, reason = reason)
            else {
                val token = Pair(recording.directory.absolutePath, tick.scheduledNs)
                automaticGate.reserve(token.first, token.second)
                val callback = Runnable { requestAutomatic(token.first, token.second) }
                pendingAutomaticCallback = callback
                main.post(callback)
            }
        }
        handler.postDelayed(automaticTick, active.delayMs(SystemClock.elapsedRealtimeNanos()))
    }

    fun skipAutomatic(path: String, scheduledNs: Long, reason: String) = dispatch {
        if (automaticGate.consume(path, scheduledNs)) {
            pendingAutomaticCallback = null
            tracking?.capture(scheduledNs, reason = reason)
        }
    }

    private fun stopAutomatic() {
        schedule?.stop(); schedule = null; handler.removeCallbacks(automaticTick)
        cancelPendingAutomatic("session_ending")
    }

    private fun cancelPendingAutomatic(reason: String) {
        pendingAutomaticCallback?.let { main.removeCallbacks(it) }
        pendingAutomaticCallback = null
        automaticGate.cancel()?.let { tracking?.capture(it.second, reason = reason) }
    }

    private fun beginEnd(recording: RecordingSession, reason: String) {
        stopAutomatic()
        if (!recording.ending || (endReason == "user" && reason != "user")) endReason = reason
        recording.requestEnd(reason)
        previewEnd.begin(reason)
    }

    private fun stopRecordingPreview(recording: RecordingSession) {
        stoppingPreview = true
        handler.removeCallbacks(previewTailTick)
        stopPreview(recording.directory.absolutePath) {
            dispatch {
                if (session === recording) {
                    previewDrained = true; exportBinding = null
                    if (!finishIfReady()) publish()
                }
            }
        }
    }

    override fun onSensorChanged(event: SensorEvent) = guarded {
        val recording = session ?: return@guarded
        if (recording.closed) return@guarded
        val type = when (event.sensor.type) {
            Sensor.TYPE_ACCELEROMETER -> "accelerometer"
            Sensor.TYPE_GYROSCOPE -> "gyroscope"
            Sensor.TYPE_ROTATION_VECTOR -> "rotation_vector"
            Sensor.TYPE_GAME_ROTATION_VECTOR -> "game_rotation_vector"
            Sensor.TYPE_MAGNETIC_FIELD -> "magnetometer"
            else -> return@guarded
        }
        val values = event.values
        val w = if (type in setOf("rotation_vector", "game_rotation_vector")) values.getOrNull(3)
            ?: sqrt(max(0f, 1f - values[0] * values[0] - values[1] * values[1] - values[2] * values[2]))
            else null
        recording.recordSensor(SensorSample(event.timestamp, type,
            values[0], values[1], values[2], w, event.accuracy))
        val now = SystemClock.elapsedRealtimeNanos()
        if (type == "gyroscope" && event.timestamp > recording.startNs && event.timestamp <= now) {
            previewGyro.add(PreviewGyroSample(event.timestamp, values[0].toDouble(), values[1].toDouble(), values[2].toDouble()))
        }
        if (now - lastUiNs > 500_000_000) {
            lastUiNs = now
            publish()
        }
    }

    override fun onAccuracyChanged(sensor: Sensor?, accuracy: Int) = Unit

    private fun finishIfReady(): Boolean {
        val recording = session ?: return false
        if (!recording.ending || recording.closed) return false
        if (!stoppingPreview) {
            val binding = exportBinding?.takeIf { it.path == recording.directory.absolutePath }
            val latest = binding?.lastQueuedNs?.get()?.takeIf { it > 0 }
            val decision = previewEnd.decision(SystemClock.elapsedRealtimeNanos(),
                recording.hasPendingPhoto, recording.lastPhotoBoundaryNs, latest,
                analysisReady && cameraReady && binding?.comparableClock != false)
            if (decision == null) {
                // Pending JPEG completion/watchdog calls us again; only poll a short preview tail.
                if (!recording.hasPendingPhoto) {
                    handler.removeCallbacks(previewTailTick)
                    handler.postDelayed(previewTailTick, 50)
                }
                return false
            }
            recording.notePreviewEnd(decision, latest)
            stopRecordingPreview(recording)
        }
        if (recording.hasPendingPhoto || !previewDrained) return false
        // Unregister before the final timestamp. Already queued callbacks see no session.
        manager.unregisterListener(this)
        recording.finish()
        exportBinding = null
        val writer = checkNotNull(tracking)
        writer.finish(if (endReason == "user") null else "session_ended:$endReason").whenComplete { result, _ ->
            handler.post {
                if (session === recording) {
                    state = snapshot(recording).copy(active = false, ending = false,
                        message = if (result.state == "complete") "saved" else "saved; tracking ${result.state}",
                        trackingMessage = writer.message)
                    session = null; tracking = null
                    val update = state; main.post { onState(update) }
                    if (destroying) finishDestroy()
                }
            }
        }
        return true
    }

    private fun snapshot(recording: RecordingSession) = RecordingState(
        active = true, ending = recording.ending,
        capturing = recording.hasPendingPhoto, imageCount = recording.imageCount,
        sampleCount = recording.sampleCount, path = recording.directory.absolutePath,
        message = if (recording.ending) "finishing" else "recording",
        trackingMessage = tracking?.message ?: "",
        previewFinishing = recording.ending && !stoppingPreview && endReason == "user",
    )

    private fun publish(message: String? = null) {
        state = session?.let { snapshot(it) } ?: state
        if (message != null) state = state.copy(message = message)
        val update = state
        main.post { onState(update) }
    }

    private fun dispatch(block: () -> Unit) {
        handler.post { guarded(block) }
    }

    private fun guarded(block: () -> Unit) {
        try {
            block()
        } catch (error: Exception) {
            manager.unregisterListener(this)
            handler.removeCallbacks(previewTailTick)
            stopAutomatic()
            exportBinding = null
            val recording = session
            val writer = tracking
            val path = session?.directory?.absolutePath ?: state.path
            try {
                session?.abort()
            } catch (_: Exception) {
                // Preserve the original failure; unfinished metadata remains on disk.
            }
            fun finishFailure() {
                writer?.finish("capture_recording_failed: ${error.message}")?.whenComplete { _, _ ->
                    handler.post { publishFailure(path, error.message, writer.message) }
                } ?: publishFailure(path, error.message, "")
            }
            if (recording != null && !stoppingPreview) {
                stoppingPreview = true
                stopPreview(recording.directory.absolutePath) { handler.post { finishFailure() } }
            } else finishFailure()
        }
    }

    private fun publishFailure(path: String, reason: String?, trackingMessage: String) {
        session = null; tracking = null
        state = state.copy(active = false, ending = false, capturing = false,
            path = path, message = "Recording failed: $reason", trackingMessage = trackingMessage)
        val update = state; main.post { onState(update) }
        if (destroying) finishDestroy()
    }

    private fun finishDestroy() {
        worker.quitSafely()
        main.post { closePreview() }
    }
}
