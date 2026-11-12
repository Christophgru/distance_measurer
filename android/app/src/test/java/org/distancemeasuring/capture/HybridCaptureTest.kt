package org.distancemeasuring.capture

import org.junit.Assert.*
import org.junit.Test
import java.io.File

class HybridCaptureTest {
    @Test fun scheduleIsAnchoredAndDoesNotFireBeforeItsFirstInterval() {
        val schedule = PeriodicStillSchedule(1_000_000_000, 1_000)
        assertNull(schedule.due(1_999_999_999))
        val first = checkNotNull(schedule.due(2_050_000_000))
        assertEquals(2_000_000_000, first.scheduledNs)
        assertEquals(0, first.missedCount)
        assertEquals(950, schedule.delayMs(2_050_000_000))
        assertNull(schedule.due(2_999_999_999))
        assertEquals(3_000_000_000, checkNotNull(schedule.due(3_000_000_000)).scheduledNs)
    }

    @Test fun delayedSchedulerReportsMissedTicksAndReturnsOnlyOneLatestRequest() {
        val schedule = PeriodicStillSchedule(1_000_000_000, 1_000)
        val due = checkNotNull(schedule.due(8_400_000_000))
        assertEquals(2_000_000_000, due.firstMissedNs)
        assertEquals(7_000_000_000, due.lastMissedNs)
        assertEquals(6, due.missedCount)
        assertEquals(8_000_000_000, due.scheduledNs)
        assertNull(schedule.due(8_400_000_000))
        assertEquals(600, schedule.delayMs(8_400_000_000))
    }

    @Test fun stoppingSchedulePreventsMoreTicksAndNewSessionsGetANewAnchor() {
        val old = PeriodicStillSchedule(1_000_000_000, 500)
        old.stop()
        assertNull(old.due(60_000_000_000))
        val next = PeriodicStillSchedule(60_000_000_000, 500)
        assertNull(next.due(60_499_999_999))
        assertEquals(60_500_000_000, checkNotNull(next.due(60_500_000_000)).scheduledNs)
    }

    @Test fun manualCaptureDefaultsAndInvalidIntervalsAreExplicit() {
        assertTrue(HybridCaptureConfig().automaticStills)
        assertEquals(1_000, HybridCaptureConfig().stillIntervalMs)
        assertFalse(HybridCaptureConfig(automaticStills = false).automaticStills)
        for (interval in listOf(0L, 249L, 60_001L, Long.MAX_VALUE)) {
            assertThrows(IllegalArgumentException::class.java) { HybridCaptureConfig(stillIntervalMs = interval) }
        }
    }

    @Test fun staleUiCallbacksCannotConsumeANewSessionOrANewerTick() {
        val gate = AutomaticStillGate()
        gate.reserve("old", 2_000_000_000)
        assertEquals(Pair("old", 2_000_000_000L), gate.cancel())
        gate.reserve("new", 5_000_000_000)
        assertFalse(gate.consume("old", 2_000_000_000))
        assertFalse(gate.consume("new", 4_000_000_000))
        assertTrue(gate.consume("new", 5_000_000_000))
        assertFalse(gate.consume("new", 5_000_000_000))
    }

    @Test fun expiredPhotoTicketsCannotStartALateCameraRequest() {
        val ticket = PhotoTicket(0, 2_000_000_000, File("unused"))
        assertTrue(ticket.captureAllowed)
        ticket.invalidate()
        assertFalse(ticket.captureAllowed)
    }
}
