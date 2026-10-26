package org.distancemeasuring.capture

import java.nio.ByteBuffer
import org.junit.Assert.*
import org.junit.Test

class PreviewLumaTest {
    @Test fun copiesPaddedStridedCroppedPlaneWithoutChangingBufferPosition() {
        val bytes = ByteArray(50) { it.toByte() }
        val buffer = ByteBuffer.wrap(bytes).apply { position(3); limit(40) }
        val frame = PreviewLumaSampler.copy(buffer, 4, 3, 10, 2, 1, 1, 2, 2, 123, 90)
        assertArrayEquals(byteArrayOf(15, 17, 25, 27), frame.pixels)
        assertEquals(3, buffer.position())
        assertEquals(40, buffer.limit())
        assertEquals(123L, frame.timestampNs)
        assertEquals(90, frame.rotationDegrees)
        assertEquals(Pair(0.0, 0.0), frame.sampledPixel(1.0, 1.0))
    }

    @Test fun boundsTheLongEdgeAndRetainsExactSamplingTransform() {
        val buffer = ByteBuffer.wrap(ByteArray(1200 * 800) { (it % 251).toByte() })
        val frame = PreviewLumaSampler.copy(buffer, 1200, 800, 1200, 1,
            0, 0, 1200, 800, 321, 0, 640)
        assertEquals(600, frame.width)
        assertEquals(400, frame.height)
        assertEquals(2, frame.sampleStep)
        assertEquals(buffer.get(2), frame.pixels[1])
        assertEquals(Pair(10.0, 20.0), frame.sampledPixel(20.0, 40.0))
    }

    @Test fun rejectsTruncatedPlanesAndInvalidCrops() {
        assertThrows(IllegalArgumentException::class.java) {
            PreviewLumaSampler.copy(ByteBuffer.allocate(5), 4, 3, 4, 1, 0, 0, 4, 3, 1, 0)
        }
        assertThrows(IllegalArgumentException::class.java) {
            PreviewLumaSampler.copy(ByteBuffer.allocate(12), 4, 3, 4, 1, 3, 0, 2, 3, 1, 0)
        }
    }
}
