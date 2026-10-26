package org.distancemeasuring.capture

import java.io.File
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class PreviewMotionTest {
    @Test fun midpointMountingAndPixelsMatchExistingPythonP3() {
        val root = JSONObject(File(requireNotNull(System.getProperty("preview.gyro.cases"))).readText())
        val cases = root.getJSONArray("cases")
        for (i in 0 until cases.length()) {
            val c = cases.getJSONObject(i)
            val history = PreviewGyroHistory()
            val ts = c.getJSONArray("timestamps_ns"); val rates = c.getJSONArray("rates")
            for (k in 0 until ts.length()) {
                val r = rates.getJSONArray(k)
                assertTrue(history.add(PreviewGyroSample(ts.getLong(k), r.getDouble(0), r.getDouble(1), r.getDouble(2))))
            }
            val estimate = history.rotation(c.getLong("start_ns"), c.getLong("end_ns"))
            assertNull(estimate.reason)
            fun vector(name: String) = c.getJSONArray(name).let { a -> DoubleArray(a.length()) { a.getDouble(it) } }
            assertArrayEquals(c.getString("name"), vector("R_device_second_first"), estimate.matrix, 1e-10)
            val camera = PreviewRotation.cameraRayRotation(estimate.matrix!!, c.getInt("sensor_orientation"))
            assertArrayEquals(vector("R_camera_second_first"), camera, 1e-10)
            fun intrinsics(name: String) = vector(name).let { PreviewIntrinsics(it[0], it[1], it[2], it[3], it[4]) }
            val pixels = c.getJSONArray("source_pixels"); val expected = c.getJSONArray("predicted_pixels")
            for (k in 0 until pixels.length()) {
                val p = pixels.getJSONArray(k)
                val result = intrinsics("first_intrinsics").rotate(PreviewPoint(p.getDouble(0), p.getDouble(1)), camera, intrinsics("second_intrinsics"))!!
                assertEquals(expected.getJSONArray(k).getDouble(0), result.x, 1e-9)
                assertEquals(expected.getJSONArray(k).getDouble(1), result.y, 1e-9)
            }
        }
    }

    @Test fun missingSparseAndOutOfOrderGyroNeverExtrapolate() {
        val history = PreviewGyroHistory(3)
        history.begin(10)
        assertFalse(history.add(PreviewGyroSample(10, 0.0, 0.0, 0.0)))
        assertTrue(history.add(PreviewGyroSample(20, 0.0, 0.0, 0.0)))
        assertFalse(history.add(PreviewGyroSample(19, 0.0, 0.0, 0.0)))
        assertTrue(history.add(PreviewGyroSample(500_000_000, 0.0, 0.0, 0.0)))
        assertEquals("gyro_not_bracketed", history.rotation(10, 100).reason)
        assertEquals("gyro_sample_gap", history.rotation(20, 500_000_000).reason)
        history.begin(1_000_000_000)
        assertEquals("gyro_not_bracketed", history.rotation(20, 100).reason)
        repeat(4) { history.add(PreviewGyroSample(1_000_000_001L + it, 0.0, 0.0, 0.0)) }
        assertEquals("gyro_not_bracketed", history.rotation(1_000_000_001, 1_000_000_004).reason)
    }

    @Test fun cropAndSamplingPreserveNominalCameraRayCoordinates() {
        val frame = PreviewLuma(1, 380, 290, ByteArray(380 * 290), 90,
            800, 600, 20, 10, 760, 580, 2)
        val active = PreviewCrop(0, 0, 4000, 3000)
        val camera = approximatePreviewIntrinsics(frame, 6.0, 4.5, 3.0, active, active)
        assertEquals(200.0, camera.fx, 1e-10)
        assertEquals(200.0, camera.fy, 1e-10)
        assertEquals(189.75, camera.cx, 1e-10)
        assertEquals(144.75, camera.cy, 1e-10)
        assertNull(camera.rotate(PreviewPoint(camera.cx, camera.cy), PreviewRotation.exponential(0.0, Math.PI, 0.0)))
    }

    @Test fun gridBoundariesAndForwardBackwardRejectionsMatchP2() {
        assertEquals(0, previewCell(PreviewPoint(11.9, 0.0), 101, 79))
        assertEquals(1, previewCell(PreviewPoint(12.0, 0.0), 101, 79))
        val source = PreviewPoint(10.0, 10.0)
        assertEquals(1.0, PreviewFlowChecks.backwardError(source, PreviewPoint(11.0, 10.0), true, 0.0, 100, 100, 1.5)!!, 0.0)
        assertNull(PreviewFlowChecks.backwardError(source, PreviewPoint(12.0, 10.0), true, 0.0, 100, 100, 1.5))
        assertNull(PreviewFlowChecks.backwardError(source, source, false, 0.0, 100, 100, 1.5))
        assertFalse(PreviewFlowChecks.forwardUsable(PreviewPoint(-1.0, 0.0), true, 0.0, 100, 100))
        assertFalse(PreviewFlowChecks.forwardUsable(source, true, Double.NaN, 100, 100))
    }

    @Test fun exactMetadataPeekDoesNotConsumeOrExposeStoredCaptureData() {
        val frames = FrameMetadataStore()
        frames.put(123, JSONObject().put("focal_length_mm", 4.2), true)
        frames.peek(123)!!.put("focal_length_mm", 99)
        assertEquals(4.2, frames.peek(123)!!.getDouble("focal_length_mm"), 0.0)
        assertEquals(4.2, frames.take(123)!!.getDouble("focal_length_mm"), 0.0)
        assertNull(frames.peek(123))
    }
}
