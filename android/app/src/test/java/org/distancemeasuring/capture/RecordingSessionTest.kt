package org.distancemeasuring.capture

import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File

class RecordingSessionTest {
    @get:Rule val temporary = TemporaryFolder()
    private var now = 1_000_000_000L
    private val availability = mapOf("accelerometer" to true, "gyroscope" to true,
        "rotation_vector" to false)

    private fun session(id: String = "test_session") = RecordingSession(
        temporary.root, JSONObject().put("model", "test"), availability, { now }, id,
    )

    private fun events(recording: RecordingSession) = File(recording.directory, "events.jsonl")
        .readLines().map(::JSONObject)

    private fun metadata(recording: RecordingSession) =
        JSONObject(File(recording.directory, "session.json").readText())

    private fun sample(timestampNs: Long, type: String = "accelerometer") =
        SensorSample(timestampNs, type, 1f, 2f, 3f, if (type == "rotation_vector") 0.5f else null, 3)

    @Test fun recordsOptionalCompassAndGameOrientationWithoutChangingTheCsvContract() {
        val recording = RecordingSession(temporary.root, JSONObject(), availability + mapOf(
            "magnetometer" to true, "game_rotation_vector" to true), { now }, "optional")
        now += 1_000_000
        recording.recordSensor(SensorSample(now, "magnetometer", 12f, 35f, -20f, null, 3))
        recording.recordSensor(SensorSample(now, "game_rotation_vector", 0f, 0f, 0f, 1f, 3))
        finish(recording)
        val rows = File(recording.directory, "sensors.csv").readLines()
        assertEquals(3, rows.size)
        assertTrue(rows.any { it.contains(",magnetometer,12.0,35.0,-20.0,,3") })
        assertTrue(rows.any { it.contains(",game_rotation_vector,0.0,0.0,0.0,1.0,3") })
        assertEquals(1, metadata(recording).getJSONObject("sensor_counts").getInt("magnetometer"))
    }

    @Test fun selectsFrameTimeOnlyWhenItsClockAndBoundsAreValid() {
        for ((index, domain, offset) in listOf(
            Triple(0, "android_elapsed_realtime_ns", 500_000L),
            Triple(1, "camera_unknown", 500_000L),
            Triple(2, "android_elapsed_realtime_ns", -1L),
            Triple(3, "android_elapsed_realtime_ns", 2_000_000L),
        )) {
            val recording = session("timestamp_$index")
            now += 1_000_000
            val request = now
            val ticket = checkNotNull(recording.beginPhoto(request))
            ticket.temporaryFile.writeBytes(byteArrayOf(1, 2, 3))
            now += 1_000_000
            recording.completePhoto(ticket, now, JSONObject()
                .put("sensor_timestamp_ns", request + offset)
                .put("sensor_timestamp_clock_domain", domain))
            finish(recording)
            val image = events(recording).single { it.getString("type") == "image" }
            assertEquals(if (index == 0) "camera_frame" else "button_request", image.getString("timestamp_source"))
            assertEquals(if (index == 0) request + offset else request, image.getLong("timestamp_ns"))
            assertEquals(request, image.getLong("request_timestamp_ns"))
        }
    }

    private fun finish(recording: RecordingSession, reason: String = "user") {
        now += 1_000_000
        recording.requestEnd(reason)
        recording.finish()
    }

    private fun savePhoto(recording: RecordingSession): PhotoTicket {
        now += 1_000_000
        val ticket = checkNotNull(recording.beginPhoto(now))
        // File publication tests use a payload; actual JPEG decoding is checked by PhotoMetadata.
        ticket.temporaryFile.writeBytes(byteArrayOf(1, 2, 3))
        now += 1_000_000
        recording.completePhoto(ticket, now, JSONObject().put("width", 4000)
            .put("height", 3000).put("iso", JSONObject.NULL))
        return ticket
    }

    @Test fun createsIndependentSessionDirectories() {
        val first = session("first")
        finish(first)
        val second = session("second")
        finish(second)
        for (recording in listOf(first, second)) {
            assertTrue(File(recording.directory, "images").isDirectory)
            assertEquals("timestamp_ns,sensor_type,x,y,z,w,accuracy",
                File(recording.directory, "sensors.csv").readLines().first())
            assertEquals(listOf("timeline_start", "timeline_end"),
                events(recording).map { it.getString("type") })
            assertEquals("complete", metadata(recording).getString("state"))
        }
        assertNotEquals(first.directory, second.directory)
    }

    @Test fun generatedIdentifiersAreCollisionSafe() {
        val first = RecordingSession(temporary.root, JSONObject(), availability, { now })
        finish(first)
        val second = RecordingSession(temporary.root, JSONObject(), availability, { now })
        finish(second)
        assertNotEquals(first.directory.name, second.directory.name)
        assertTrue(first.directory.name.matches(Regex("\\d{4}-\\d{2}-\\d{2}_\\d{6}_\\d{3}_[a-f0-9]{8}")))
    }

    @Test fun repeatedIdentifierNeverOverwritesRecording() {
        val first = session()
        finish(first)
        val before = File(first.directory, "session.json").readText()
        assertThrows(IllegalStateException::class.java) { session() }
        assertEquals(before, File(first.directory, "session.json").readText())
    }

    @Test fun threeSuccessfulCapturesHaveExactlyThreeFilesAndEvents() {
        val recording = session()
        repeat(3) { index -> assertEquals(index, savePhoto(recording).index) }
        finish(recording)
        val images = events(recording).filter { it.getString("type") == "image" }
        assertEquals(3, images.size)
        assertEquals(3, File(recording.directory, "images").listFiles()!!.size)
        assertEquals(3, metadata(recording).getInt("image_count"))
        images.forEachIndexed { index, image ->
            assertEquals("images/image_%03d.jpg".format(index), image.getString("filename"))
            assertTrue(File(recording.directory, image.getString("filename")).isFile)
            assertEquals("button_request", image.getString("timestamp_source"))
            assertEquals("android_elapsed_realtime_ns", image.getString("clock_domain"))
            assertEquals(image.getLong("request_timestamp_ns"), image.getLong("timestamp_ns"))
            assertTrue(image.getLong("timestamp_ns") > recording.startNs)
            assertTrue(image.getLong("callback_timestamp_ns") >= image.getLong("timestamp_ns"))
            assertTrue(image.getLong("callback_timestamp_ns") < metadata(recording).getLong("timeline_end_ns"))
            assertEquals(4000, image.getJSONObject("metadata").getInt("width"))
            assertTrue(image.getJSONObject("metadata").isNull("iso"))
        }
    }

    @Test fun waitsForPendingPhotoAndKeepsSensorsUntilItSettles() {
        val recording = session()
        now += 1_000_000
        val ticket = checkNotNull(recording.beginPhoto(now))
        assertNull(recording.beginPhoto(now))
        recording.requestEnd("user")
        assertNull(recording.beginPhoto(now))
        assertThrows(IllegalStateException::class.java) { recording.finish() }
        now += 1_000_000
        recording.recordSensor(sample(now))
        ticket.temporaryFile.writeBytes(byteArrayOf(1))
        recording.completePhoto(ticket, now, JSONObject())
        now += 1_000_000
        recording.finish()
        assertEquals(1, recording.sampleCount)
        val csv = File(recording.directory, "sensors.csv").readText()
        recording.recordSensor(sample(++now))
        assertEquals(csv, File(recording.directory, "sensors.csv").readText())
        assertNull(recording.beginPhoto(now))
    }

    @Test fun mergesSensorStreamsAndRejectsDuplicateAndLateSamples() {
        val recording = session()
        now += 500_000_000
        recording.recordSensor(sample(recording.startNs + 450_000_000, "gyroscope"))
        recording.recordSensor(sample(recording.startNs + 440_000_000))
        recording.recordSensor(sample(recording.startNs + 440_000_000)) // duplicate
        now += 500_000_000
        recording.recordSensor(sample(recording.startNs + 950_000_000)) // flush earlier samples
        recording.recordSensor(sample(recording.startNs + 460_000_000, "gyroscope"))
        // A third stream arriving behind already written rows is counted and dropped.
        recording.setSensorAvailable("rotation_vector", true)
        recording.recordSensor(sample(recording.startNs + 420_000_000, "rotation_vector"))
        finish(recording)
        val rows = File(recording.directory, "sensors.csv").readLines().drop(1)
        val timestamps = rows.map { it.substringBefore(',').toLong() }
        assertEquals(timestamps.sorted(), timestamps)
        assertEquals(4, rows.size)
        assertTrue(rows.any { it.contains(",gyroscope,") })
        assertTrue(metadata(recording).getInt("dropped_sensor_samples") >= 2)
    }

    @Test fun optionalRotationSensorIsExplicitlyUnavailable() {
        val recording = session()
        now += 1_000_000
        recording.recordSensor(sample(now, "rotation_vector"))
        finish(recording)
        assertFalse(metadata(recording).getJSONObject("sensors").getBoolean("rotation_vector"))
        assertEquals(0, recording.sampleCount)
    }

    @Test fun failedCaptureDoesNotPublishImageAndAllowsAnotherCapture() {
        val recording = session()
        now += 1_000_000
        val ticket = checkNotNull(recording.beginPhoto(now))
        ticket.temporaryFile.writeText("partial")
        recording.failPhoto(ticket, "camera disconnected")
        assertFalse(ticket.temporaryFile.exists())
        assertFalse(ticket.file.exists())
        assertEquals(1, savePhoto(recording).index)
        finish(recording)
        assertEquals(1, recording.imageCount)
        assertEquals(1, events(recording).count { it.getString("type") == "image_error" })
    }

    @Test fun timeoutAndLateSuccessCannotPublishOrphanImage() {
        val recording = session()
        now += 1_000_000
        val ticket = checkNotNull(recording.beginPhoto(now))
        recording.requestEnd("activity_paused")
        recording.failPhoto(ticket, "timeout")
        finish(recording)
        ticket.temporaryFile.writeText("late camera output")
        assertFalse(recording.completePhoto(ticket, now, JSONObject()))
        assertFalse(ticket.temporaryFile.exists())
        assertFalse(ticket.file.exists())
        assertEquals("interrupted", metadata(recording).getString("state"))
    }

    @Test fun rejectsWrongClockAndNonFiniteSamples() {
        val recording = session()
        assertThrows(IllegalArgumentException::class.java) { recording.beginPhoto(recording.startNs) }
        assertThrows(IllegalArgumentException::class.java) { recording.beginPhoto(now + 1) }
        now += 1_000_000
        recording.recordSensor(sample(now + 1))
        recording.recordSensor(sample(now).copy(x = Float.NaN))
        finish(recording)
        assertEquals(0, recording.sampleCount)
        assertEquals(2, metadata(recording).getInt("dropped_sensor_samples"))
    }

    @Test fun abortLeavesAnExplicitlyUnfinishedSession() {
        val recording = session()
        recording.abort()
        assertTrue(recording.closed)
        assertEquals("recording", metadata(recording).getString("state"))
        assertFalse(metadata(recording).has("timeline_end_ns"))
    }
}
