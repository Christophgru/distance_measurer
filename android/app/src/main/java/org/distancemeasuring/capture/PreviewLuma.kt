package org.distancemeasuring.capture

import java.nio.ByteBuffer
import kotlin.math.ceil

data class PreviewLuma(
    val timestampNs: Long, val width: Int, val height: Int, val pixels: ByteArray,
    val rotationDegrees: Int, val bufferWidth: Int, val bufferHeight: Int,
    val cropLeft: Int, val cropTop: Int, val cropWidth: Int, val cropHeight: Int,
    val sampleStep: Int,
    val receivedTimestampNs: Long = timestampNs,
) {
    /** Maps a native buffer pixel into the unrotated, cropped, sampled Y image. */
    fun sampledPixel(x: Double, y: Double) = Pair((x - cropLeft) / sampleStep, (y - cropTop) / sampleStep)
}

/** Owns a bounded Y copy, honors plane strides/position/crop, never rotates pixels. */
object PreviewLumaSampler {
    fun copy(buffer: ByteBuffer, width: Int, height: Int, rowStride: Int, pixelStride: Int,
             left: Int, top: Int, cropWidth: Int, cropHeight: Int, timestampNs: Long,
             rotationDegrees: Int, maxEdge: Int = 640): PreviewLuma {
        require(width > 0 && height > 0 && rowStride > 0 && pixelStride > 0 && maxEdge > 0)
        require(left >= 0 && top >= 0 && cropWidth > 0 && cropHeight > 0)
        require(left.toLong() + cropWidth <= width && top.toLong() + cropHeight <= height)
        require((width - 1L) * pixelStride < rowStride && timestampNs > 0)
        require(rotationDegrees in listOf(0, 90, 180, 270))
        val step = maxOf(1, ceil(maxOf(cropWidth, cropHeight).toDouble() / maxEdge).toInt())
        val w = (cropWidth - 1) / step + 1
        val h = (cropHeight - 1) / step + 1
        val base = buffer.position()
        val last = base.toLong() + (top + (h - 1) * step).toLong() * rowStride +
            (left + (w - 1) * step).toLong() * pixelStride
        require(last < buffer.limit() && w.toLong() * h <= Int.MAX_VALUE)
        val pixels = ByteArray(w * h)
        for (y in 0 until h) for (x in 0 until w) {
            pixels[y * w + x] = buffer.get(base + (top + y * step) * rowStride + (left + x * step) * pixelStride)
        }
        return PreviewLuma(timestampNs, w, h, pixels, rotationDegrees, width, height,
            left, top, cropWidth, cropHeight, step)
    }
}
