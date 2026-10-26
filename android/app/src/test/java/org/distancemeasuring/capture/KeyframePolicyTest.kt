package org.distancemeasuring.capture

import java.io.File
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class KeyframePolicyTest {
    @Test fun sharedPythonKotlinCasesAndDefaultsAgree() {
        val root = JSONObject(File(requireNotNull(System.getProperty("keyframe.cases"))).readText())
        val expected = root.getJSONObject("settings")
        val defaults = KeyframeConfig().json()
        for (key in expected.keys()) assertEquals(expected.getDouble(key), defaults.getDouble(key), 0.0)
        val cases = root.getJSONArray("cases")
        for (i in 0 until cases.length()) {
            val c = cases.getJSONObject(i)
            val size = c.getJSONArray("size")
            val decision = KeyframePolicy.score(c.getInt("tracks"), c.getInt("source_cells"),
                c.getInt("target_cells"), if (c.isNull("parallax_px")) null else c.getDouble("parallax_px"),
                size.getInt(0), size.getInt(1), c.getBoolean("rotation_available"))
            assertEquals(c.getString("name"), c.getBoolean("candidate"), decision.candidate)
            assertEquals(c.getJSONArray("reasons").toString(), decision.json().getJSONArray("reasons").toString())
        }
    }

    @Test fun invalidThresholdsAndMeasurementsFail() {
        assertThrows(IllegalArgumentException::class.java) { KeyframeConfig(minTracks = 0) }
        assertThrows(IllegalArgumentException::class.java) {
            KeyframePolicy.score(10, 11, 2, 3.0, 640, 480, true)
        }
    }
}
