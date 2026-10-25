package org.distancemeasuring.capture

import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class FrameMetadataStoreTest {
    @Test fun joinsOnlyTheExactSensorTimestampAndConsumesItOnce() {
        val store = FrameMetadataStore()
        store.put(100, JSONObject().put("focus_distance_diopters", 2))
        assertNull(store.take(101))
        assertTrue(store.contains(100))
        assertEquals(2, store.take(100)!!.getInt("focus_distance_diopters"))
        assertNull(store.take(100))
    }

    @Test fun evictsOldFramesAndIgnoresInvalidTimestamps() {
        val store = FrameMetadataStore(2)
        for (t in listOf(0L, -1L, 1L, 2L, 3L)) store.put(t, JSONObject())
        assertNull(store.take(0))
        assertNull(store.take(-1))
        assertNull(store.take(1))
        assertNotNull(store.take(2))
        assertNotNull(store.take(3))
    }

    @Test fun storesASnapshotRatherThanMutableCallbackState() {
        val store = FrameMetadataStore()
        val metadata = JSONObject().put("physical_camera_id", "wide")
        store.put(1, metadata)
        metadata.put("physical_camera_id", "tele")
        assertEquals("wide", store.take(1)!!.getString("physical_camera_id"))
    }

    @Test fun slowJpegDeliveryDoesNotLoseItsResultToPreviewEviction() {
        val store = FrameMetadataStore(2)
        store.put(1, JSONObject().put("iso", 100), isStill = true)
        for (t in 2L..20L) store.put(t, JSONObject())
        assertTrue(store.contains(1))
        assertEquals(100, store.take(1)!!.getInt("iso"))
        assertFalse(store.contains(1))
    }
}
