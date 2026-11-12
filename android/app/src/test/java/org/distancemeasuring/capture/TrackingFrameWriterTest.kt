package org.distancemeasuring.capture

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicLong

class TrackingFrameWriterTest {
    @get:Rule val temporary = TemporaryFolder()
    private val now = AtomicLong(5_000_000_000)

    private fun value(index: Int, signature: String = "camera0"): PreviewFrameExport {
        val timestamp = 1_200_000_000L + index * 1_000_000L
        val frame = PreviewLuma(timestamp, 8, 8, ByteArray(64) { it.toByte() }, 90,
            20, 20, 2, 4, 16, 16, 2, timestamp + 1_000_000)
        val metadata = JSONObject().put("width", 8).put("height", 8).put("camera_id", "0")
            .put("physical_camera_id", "synthetic").put("rotation_degrees", 90)
            .put("target_rotation", 0).put("is_flipped", false).put("lens_facing", 1)
            .put("sensor_orientation_degrees", 90)
        val intrinsics = JSONObject().put("image_width", 8).put("image_height", 8)
            .put("camera_matrix", JSONArray(listOf(listOf(10, 0, 3.5), listOf(0, 10, 3.5), listOf(0, 0, 1))))
            .put("source", "calibrated").put("approximate", false)
            .put("distortion", JSONArray(listOf(0, 0, 0, 0, 0)))
            .put("pixel_convention", "stored_pixels_ignore_exif").put("camera_id", "0")
            .put("assumptions", JSONArray())
        return PreviewFrameExport(frame, metadata, intrinsics, "android_elapsed_realtime_ns",
            JSONObject().put("quality_status", "synthetic_writer_test"), signature)
    }

    private fun encode(frame: PreviewLuma, file: File) {
        // Native encoding is a separate instrumentation test. This original known
        // PNG has exactly these pixels, allowing pure JVM queue/lifecycle tests.
        check(frame.width == 8 && frame.height == 8 && frame.pixels.contentEquals(ByteArray(64) { it.toByte() }))
        file.writeBytes(checkNotNull(javaClass.getResourceAsStream("/hybrid/frame.png")).use { it.readBytes() })
    }

    private fun writer(dir: File, config: HybridCaptureConfig = HybridCaptureConfig(),
                       unavailable: String? = null, encoder: (PreviewLuma, File) -> Unit = ::encode) =
        TrackingFrameWriter(dir, dir.name, 1_000_000_000, config, now::get, encoder, unavailable)

    private fun records(dir: File) = File(dir, "tracking/events.jsonl").readLines().map(::JSONObject)
    private fun manifest(dir: File) = JSONObject(File(dir, "tracking.json").readText())

    private fun publishCase(name: String, directory: File) {
        val root = System.getProperty("hybrid.fixture.output")?.let(::File) ?: return
        val destination = File(root, "writer_cases/${name}_${System.nanoTime()}")
        check(directory.copyRecursively(destination))
        File(root, "writer_case_paths.jsonl").appendText(JSONObject().put("name", name)
            .put("directory", destination.absolutePath).toString() + "\n")
    }

    @Test fun slowWriterDropsExplicitRangesInsteadOfRetainingUnlimitedPixels() {
        val dir = temporary.newFolder()
        val entered = CountDownLatch(1); val release = CountDownLatch(1)
        val first = AtomicBoolean(true)
        val output = writer(dir, HybridCaptureConfig(writerQueueCapacity = 2)) { frame, file ->
            if (first.compareAndSet(true, false)) { entered.countDown(); check(release.await(10, TimeUnit.SECONDS)) }
            encode(frame, file)
        }
        assertTrue(output.offer(value(0)))
        assertTrue(entered.await(5, TimeUnit.SECONDS))
        try {
            assertTrue(output.offer(value(1))); assertTrue(output.offer(value(2)))
            repeat(1_000) { assertFalse(output.offer(value(it + 3))) }
            assertEquals(2, output.queuedPixelCount())
            val future = output.finish()
            assertFalse(future.isDone)
            assertFalse(output.offer(value(2_000)))
        } finally { release.countDown() }
        val result = output.completion.get(10, TimeUnit.SECONDS)
        assertEquals("complete", result.state)
        assertEquals(3, result.frames); assertEquals(1_000, result.dropped)
        val rows = records(dir)
        assertEquals(listOf(0L, 1L, 2L), rows.filter { it.getString("type") == "frame" }.map { it.getLong("frame_id") })
        val drop = rows.single { it.getString("type") == "drop" }
        assertEquals(3, drop.getLong("first_frame_id")); assertEquals(1_000, drop.getLong("count"))
        assertEquals("writer_queue_full", drop.getString("reason"))
        assertEquals(rows.indices.map { it.toLong() }, rows.map { it.getLong("sequence") })
        assertEquals("complete", manifest(dir).getString("state"))
        publishCase("bounded_queue", dir)
    }

    @Test fun pngStorageFailureIsAnExplicitDropAndAnInterruptedCapture() {
        val dir = temporary.newFolder(); val fail = AtomicBoolean(true)
        val output = writer(dir) { frame, file ->
            if (fail.compareAndSet(true, false)) { file.writeText("partial"); error("simulated storage failure") }
            encode(frame, file)
        }
        output.offer(value(0)); output.offer(value(1))
        val result = output.finish().get(5, TimeUnit.SECONDS)
        assertEquals("interrupted", result.state)
        assertEquals(1, result.frames); assertEquals(1, result.dropped)
        assertTrue(result.failureReason!!.contains("storage failure"))
        assertFalse(File(dir, "tracking/frames/frame_000000.png").exists())
        assertTrue(File(dir, "tracking/frames/frame_000001.png").isFile)
        assertFalse(File(dir, "tracking/frames").listFiles()!!.any { it.name.contains("pending") })
        assertEquals("frame_storage_error", records(dir).first().getString("reason"))
        publishCase("png_storage_failure", dir)
    }

    @Test fun controlsAreBoundedAndOverflowCannotBeReportedAsSuccess() {
        val dir = temporary.newFolder(); val entered = CountDownLatch(1); val release = CountDownLatch(1)
        val output = writer(dir, HybridCaptureConfig(diagnosticQueueCapacity = 2)) { frame, file ->
            entered.countDown(); check(release.await(10, TimeUnit.SECONDS)); encode(frame, file)
        }
        output.offer(value(0)); assertTrue(entered.await(5, TimeUnit.SECONDS))
        try {
            assertTrue(output.capture(2_000_000_000, reason = "camera_busy"))
            assertTrue(output.capture(3_000_000_000, reason = "camera_busy"))
            assertFalse(output.capture(4_000_000_000, reason = "camera_busy"))
            assertFalse(output.offer(value(1)))
            output.finish()
        } finally { release.countDown() }
        val result = output.completion.get(5, TimeUnit.SECONDS)
        assertEquals("interrupted", result.state)
        assertEquals("diagnostic_queue_full", result.failureReason)
        assertEquals(2, records(dir).count { it.getString("type") == "capture" })
        publishCase("bounded_controls", dir)
    }

    @Test fun unsupportedStreamWritesAnUnavailableSidecarAndPreservesPhotoDecisions() {
        val dir = temporary.newFolder(); val output = writer(dir, unavailable = "analysis_stream_unsupported")
        assertFalse(output.offer(value(0)))
        assertTrue(output.capture(2_000_000_000, reason = "camera_busy"))
        val result = output.finish().get(5, TimeUnit.SECONDS)
        assertEquals("unavailable", result.state); assertEquals(0, result.frames)
        assertEquals("analysis_stream_unsupported", manifest(dir).getString("failure_reason"))
        assertEquals("capture", records(dir).single().getString("type"))
        publishCase("unavailable_stream", dir)
    }

    @Test fun metadataIsSnapshottedAndGeometryChangesGetResetMarkers() {
        val dir = temporary.newFolder(); val output = writer(dir)
        val original = value(0)
        output.offer(original); original.metadata.put("camera_id", "mutated_after_offer")
        output.offer(value(1, "changed_crop"))
        output.finish().get(5, TimeUnit.SECONDS)
        val rows = records(dir)
        assertEquals("0", rows.first { it.getString("type") == "frame" }.getJSONObject("metadata").getString("camera_id"))
        assertEquals(listOf(0L, 1L), rows.filter { it.getString("type") == "reset" }.map { it.getLong("next_frame_id") })
        publishCase("geometry_reset", dir)
    }

    @Test fun finishingIsIdempotentAndLateCallbacksCannotEnterAnotherRecording() {
        val dir = temporary.newFolder(); val old = writer(dir)
        old.offer(value(0))
        val completion = old.finish("session_ended:activity_paused")
        assertSame(completion, old.finish())
        assertEquals("interrupted", completion.get(5, TimeUnit.SECONDS).state)
        val nextDir = temporary.newFolder(); val next = writer(nextDir)
        assertFalse(old.offer(value(1)))
        assertFalse(old.capture(2_000_000_000, reason = "late_callback"))
        assertTrue(next.offer(value(0)))
        next.finish().get(5, TimeUnit.SECONDS)
        assertEquals(0, records(nextDir).first { it.getString("type") == "frame" }.getLong("frame_id"))
        assertEquals(1, records(dir).count { it.getString("type") == "frame" })
        publishCase("session_stop", dir)
    }

    @Test fun invalidAndRegressedCameraTimesAreDropsRatherThanInvalidSavedFrames() {
        val dir = temporary.newFolder(); val output = writer(dir)
        assertFalse(output.offer(value(0).let { it.copy(frame = it.frame.copy(timestampNs = 0)) }))
        assertTrue(output.offer(value(1)))
        assertFalse(output.offer(value(0)))
        assertEquals(2, output.finish().get(5, TimeUnit.SECONDS).dropped)
        assertEquals(1, records(dir).count { it.getString("type") == "frame" })
        publishCase("invalid_timestamps", dir)
    }

    @Test fun unknownCameraClockIsStoredWithoutInventingAnOffsetToTheSensorTimeline() {
        val dir = temporary.newFolder(); val output = writer(dir)
        val data = value(0)
        assertTrue(output.offer(data.copy(clockDomain = "camera_unknown",
            frame = data.frame.copy(timestampNs = 91_200_000_000))))
        assertEquals("complete", output.finish().get(5, TimeUnit.SECONDS).state)
        assertEquals(91_200_000_000, records(dir).last().getLong("timestamp_ns"))
        publishCase("unknown_camera_clock", dir)
    }

    @Test fun delayedAutomaticTicksRecordAnAggregateSkipBeforeTheLatestDecision() {
        val dir = temporary.newFolder(); val output = writer(dir, HybridCaptureConfig(stillIntervalMs = 250))
        val schedule = PeriodicStillSchedule(1_000_000_000, 250)
        val tick = checkNotNull(schedule.due(2_300_000_000))
        output.capture(tick.firstMissedNs, reason = "scheduler_delayed",
            missedCount = tick.missedCount, lastSkippedNs = tick.lastMissedNs)
        output.capture(tick.scheduledNs, reason = "camera_busy")
        output.finish().get(5, TimeUnit.SECONDS)
        assertEquals(4, records(dir).first().getLong("missed_tick_count"))
        assertTrue(records(dir).first().getLong("last_skipped_timestamp_ns") < records(dir).last().getLong("scheduled_timestamp_ns"))
        publishCase("delayed_ticks", dir)
    }

    @Test fun initializationStorageErrorCannotLeaveACompleteManifest() {
        val dir = temporary.newFolder(); File(dir, "tracking").writeText("blocked directory")
        val output = writer(dir)
        assertEquals("interrupted", output.completion.get(5, TimeUnit.SECONDS).state)
        assertFalse(output.offer(value(0)))
        assertTrue(manifest(dir).getString("failure_reason").contains("tracking_storage_error"))
    }

    @Test fun exportGeneratedSessionForIndependentPythonContractValidation() {
        val destination = System.getProperty("hybrid.fixture.output")?.let(::File) ?: temporary.newFolder()
        val root = File(destination, "run_${System.nanoTime()}").also { check(it.mkdirs()) }
        val clock = AtomicLong(1_000_000_000)
        val recording = RecordingSession(root, JSONObject().put("manufacturer", "Synthetic")
            .put("model", "JVM hybrid contract fixture").put("app_version", BuildConfig.VERSION_NAME)
            .put("app_version_code", BuildConfig.VERSION_CODE), mapOf("accelerometer" to true,
                "gyroscope" to true, "rotation_vector" to false), clock::get, "synthetic_hybrid")
        clock.set(3_000_000_000)
        for (i in 1..199) for (kind in listOf("accelerometer", "gyroscope"))
            recording.recordSensor(SensorSample(1_000_000_000L + i * 10_000_000L, kind,
                0f, 0f, if (kind == "accelerometer") 9.81f else 0f, null, 3))
        val output = TrackingFrameWriter(recording.directory, recording.directory.name,
            recording.startNs, HybridCaptureConfig(), clock::get, ::encode)
        for (index in listOf(0, 100, 200)) assertTrue(output.offer(value(index)))
        val ticket = checkNotNull(recording.beginPhoto(2_000_000_000))
        ticket.temporaryFile.writeBytes(checkNotNull(javaClass.getResourceAsStream("/hybrid/still.jpg")).use { it.readBytes() })
        output.capture(2_000_000_000, ticket)
        recording.completePhoto(ticket, 2_010_000_000, JSONObject().put("width", 80).put("height", 48)
            .put("camera_id", "0").put("exif_orientation", 1).put("rotation_degrees", 0)
            .put("is_flipped", false).put("focal_length_mm", 4.2).put("iso", 100).put("exposure_time_s", .01)
            .put("sensor_timestamp_ns", 2_005_000_000).put("sensor_timestamp_clock_domain", "android_elapsed_realtime_ns"))
        output.capture(2_500_000_000, reason = "camera_busy")
        recording.requestEnd("user"); clock.set(3_010_000_000); recording.finish()
        assertEquals("complete", output.finish().get(5, TimeUnit.SECONDS).state)
        assertEquals(3, manifest(recording.directory).getJSONObject("counts").getInt("frames"))
        if (System.getProperty("hybrid.fixture.output") != null)
            File(destination, "fixture_path.txt").writeText(recording.directory.absolutePath)
    }
}
