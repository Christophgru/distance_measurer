package org.distancemeasuring.capture

import androidx.camera.core.ImageAnalysis
import androidx.camera.core.ImageProxy
import android.os.SystemClock
import java.io.Closeable
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicReference
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.RejectedExecutionException

/** Analysis owns its worker. Sensor callbacks/JPEG saving never run on this queue. */
class PreviewAnalyzer(
    private val onUpdate: (String, String) -> Unit,
    private val process: (PreviewLuma, String) -> String = { frame, _ ->
        "Preview Y: ${frame.width}×${frame.height}; timestamp: ${frame.timestampNs} ns"
    },
    private val closeProcessor: () -> Unit = {},
    private val flushProcessor: (String) -> Unit = {},
) : ImageAnalysis.Analyzer, Closeable {
    val executor = Executors.newSingleThreadExecutor { task -> Thread(task, "preview-analysis") }
    private val session = AtomicReference<String?>(null)
    private var previousSession: String? = null
    private var lastTimestamp = -1L
    private val closed = AtomicBoolean(false)

    fun setSession(path: String?) { session.set(path) }

    fun stopSession(path: String, onStopped: () -> Unit) {
        session.compareAndSet(path, null)
        try {
            executor.execute { try { flushProcessor(path) } finally { onStopped() } }
        } catch (_: RejectedExecutionException) { onStopped() }
    }

    override fun analyze(image: ImageProxy) {
        val receivedNs = SystemClock.elapsedRealtimeNanos()
        val token = session.get()
        var copied: PreviewLuma? = null
        try {
            if (token != null) {
                if (token != previousSession) { previousSession = token; lastTimestamp = -1 }
                val timestamp = image.imageInfo.timestamp
                if (timestamp > 0 && (lastTimestamp < 0 || timestamp < lastTimestamp ||
                    timestamp - lastTimestamp >= 100_000_000)) {
                    val plane = image.planes[0]
                    val crop = image.cropRect
                    copied = PreviewLumaSampler.copy(plane.buffer, image.width, image.height,
                        plane.rowStride, plane.pixelStride, crop.left, crop.top, crop.width(), crop.height(),
                        timestamp, image.imageInfo.rotationDegrees).copy(receivedTimestampNs = receivedNs)
                    lastTimestamp = timestamp
                }
            }
        } catch (error: Exception) {
            if (token != null && session.get() == token) onUpdate(token, "Preview analysis: ${error.message}")
        } finally {
            image.close()
        }
        // The camera buffer is released before processing the owned luminance copy.
        if (copied != null && token != null && session.get() == token) {
            try {
                val result = process(copied, token)
                if (session.get() == token) onUpdate(token, result)
            } catch (error: Exception) {
                if (session.get() == token) onUpdate(token, "Preview processing: ${error.message}")
            }
        }
    }

    override fun close() {
        if (!closed.compareAndSet(false, true)) return
        session.set(null)
        executor.execute(closeProcessor)
        executor.shutdown()
    }
}
