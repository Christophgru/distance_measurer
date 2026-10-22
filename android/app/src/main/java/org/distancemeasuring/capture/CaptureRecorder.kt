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
import kotlin.math.max
import kotlin.math.sqrt

/** Sensor callbacks, file writes, and session transitions share one worker queue. */
class CaptureRecorder(context: Context, private val onState: (RecordingState) -> Unit) :
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
    private val sensors = mapOf(
        "accelerometer" to manager.getDefaultSensor(Sensor.TYPE_ACCELEROMETER),
        "gyroscope" to manager.getDefaultSensor(Sensor.TYPE_GYROSCOPE),
        "rotation_vector" to manager.getDefaultSensor(Sensor.TYPE_ROTATION_VECTOR),
        "game_rotation_vector" to manager.getDefaultSensor(Sensor.TYPE_GAME_ROTATION_VECTOR),
        "magnetometer" to manager.getDefaultSensor(Sensor.TYPE_MAGNETIC_FIELD),
    )

    fun start() = dispatch {
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
            .put("android_api", Build.VERSION.SDK_INT).put("app_version", "0.2.0"),
            sensors.mapValues { it.value != null }, SystemClock::elapsedRealtimeNanos)
        session = recording
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

    fun takePhoto(imageCapture: ImageCapture, cameraMetadata: JSONObject, targetRotation: Int) {
        // Sample the button timestamp on the UI thread, before scheduling storage work.
        val requestNs = SystemClock.elapsedRealtimeNanos()
        dispatch {
            val recording = session
            if (recording == null) {
                publish()
                return@dispatch
            }
            val ticket = recording.beginPhoto(requestNs)
            if (ticket == null) {
                publish()
                return@dispatch
            }
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
                try {
                    imageCapture.targetRotation = targetRotation
                    imageCapture.takePicture(
                        callbackExecutor,
                        object : ImageCapture.OnImageCapturedCallback() {
                            override fun onCaptureSuccess(image: ImageProxy) {
                                val callbackNs = SystemClock.elapsedRealtimeNanos()
                                handler.removeCallbacks(timeout)
                                try {
                                    guarded {
                                        if (recording.closed || session !== recording) {
                                            ticket.temporaryFile.delete()
                                            return@guarded
                                        }
                                        try {
                                            val metadata = PhotoMetadata.saveCapturedJpeg(
                                                ticket.temporaryFile, image, cameraMetadata, targetRotation)
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
        session?.requestEnd(reason)
        if (!finishIfReady()) publish()
    }

    fun destroy() = dispatch {
        destroying = true
        session?.requestEnd("activity_destroyed")
        if (session == null) worker.quitSafely() else if (!finishIfReady()) publish()
    }

    override fun onSensorChanged(event: SensorEvent) = guarded {
        val recording = session ?: return@guarded
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
        if (now - lastUiNs > 500_000_000) {
            lastUiNs = now
            publish()
        }
    }

    override fun onAccuracyChanged(sensor: Sensor?, accuracy: Int) = Unit

    private fun finishIfReady(): Boolean {
        val recording = session ?: return false
        if (!recording.ending || recording.hasPendingPhoto) return false
        // Unregister before the final timestamp. Already queued callbacks see no session.
        manager.unregisterListener(this)
        recording.finish()
        state = snapshot(recording).copy(active = false, ending = false, message = "saved")
        session = null
        val update = state
        main.post { onState(update) }
        if (destroying) worker.quitSafely()
        return true
    }

    private fun snapshot(recording: RecordingSession) = RecordingState(
        active = !recording.closed, ending = recording.ending,
        capturing = recording.hasPendingPhoto, imageCount = recording.imageCount,
        sampleCount = recording.sampleCount, path = recording.directory.absolutePath,
        message = if (recording.ending) "finishing" else "recording",
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
            val path = session?.directory?.absolutePath ?: state.path
            try {
                session?.abort()
            } catch (_: Exception) {
                // Preserve the original failure; unfinished metadata remains on disk.
            }
            session = null
            state = state.copy(active = false, ending = false, capturing = false,
                path = path, message = "Recording failed: ${error.message}")
            val update = state
            main.post { onState(update) }
            if (destroying) worker.quitSafely()
        }
    }
}
