package org.distancemeasuring.capture

import androidx.test.ext.junit.runners.AndroidJUnit4
import java.util.Random
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith
import org.opencv.android.OpenCVLoader
import org.opencv.core.Core
import org.opencv.core.CvType
import org.opencv.core.Mat
import org.opencv.core.Size
import org.opencv.imgproc.Imgproc

/** Native algorithm checks; separate from the manual physical camera acceptance. */
@RunWith(AndroidJUnit4::class)
class PreviewFlowInstrumentedTest {
    private fun texture(): Mat {
        assertTrue(OpenCVLoader.initLocal())
        Core.setNumThreads(1)
        val image = Mat(480, 640, CvType.CV_8UC1)
        image.put(0, 0, ByteArray(640 * 480).also { Random(5).nextBytes(it) })
        Imgproc.GaussianBlur(image, image, Size(3.0, 3.0), .7)
        return image
    }

    private fun frame(image: Mat, timestamp: Long): PreviewLuma {
        val pixels = ByteArray(image.rows() * image.cols())
        image.get(0, 0, pixels)
        return PreviewLuma(timestamp, image.cols(), image.rows(), pixels, 90,
            image.cols(), image.rows(), 0, 0, image.cols(), image.rows(), 1)
    }

    private fun translated(image: Mat, x: Double, y: Double): Mat {
        val transform = Mat(2, 3, CvType.CV_64FC1)
        val result = Mat()
        try {
            transform.put(0, 0, *doubleArrayOf(1.0, 0.0, x, 0.0, 1.0, y))
            Imgproc.warpAffine(image, result, transform, image.size())
        } finally { transform.release() }
        return result
    }

    @Test fun persistentLkTranslationAndSelectiveReplenishment() {
        val first = texture(); val second = translated(first, 4.0, 2.0); val third = translated(first, 8.0, 4.0)
        try {
            PreviewFlowTracker().use { tracker ->
                val seeded = tracker.step(frame(first, 1_000_000_000))
                assertTrue(seeded.activeCount > 100)
                val result = tracker.step(frame(second, 1_100_000_000))
                assertTrue(result.matches.size > 100)
                assertTrue(result.activeCount <= 240)
                assertTrue(result.medianFbError!! < .3)
                assertEquals(4.0, previewMedian(result.matches.map { it.target.point.x - it.source.point.x })!!, .2)
                assertEquals(2.0, previewMedian(result.matches.map { it.target.point.y - it.source.point.y })!!, .2)
                assertTrue(result.matches.all { it.source.id == it.target.id && it.target.age == 2 })
                if (result.matches.size >= 180) assertTrue(result.detectedCells.isEmpty())
                val later = tracker.step(frame(third, 1_200_000_000))
                assertTrue(later.matches.count { it.target.age >= 3 } > 80)
            }
        } finally { first.release(); second.release(); third.release() }
    }

    @Test fun knownPureRotationHasNearZeroResidualRatherThanAKeyframe() {
        val first = texture(); val second = Mat(); val homography = Mat(3, 3, CvType.CV_64FC1)
        val camera = PreviewIntrinsics(400.0, 400.0, 319.5, 239.5)
        val rotation = PreviewRotation.exponential(0.0, 0.0, .02)
        val k = doubleArrayOf(400.0, 0.0, 319.5, 0.0, 400.0, 239.5, 0.0, 0.0, 1.0)
        val inverse = doubleArrayOf(1 / 400.0, 0.0, -319.5 / 400, 0.0, 1 / 400.0, -239.5 / 400, 0.0, 0.0, 1.0)
        try {
            homography.put(0, 0, *PreviewRotation.multiply(PreviewRotation.multiply(k, rotation), inverse))
            Imgproc.warpPerspective(first, second, homography, first.size())
            PreviewFlowTracker().use { tracker ->
                tracker.step(frame(first, 1_000_000_000))
                val result = tracker.step(frame(second, 1_100_000_000))
                assertTrue(result.matches.size > 100)
                val errors = result.matches.map { camera.rotate(it.source.point, rotation)!!.distance(it.target.point) }
                val median = previewMedian(errors)!!
                assertTrue(median < .5)
                assertFalse(KeyframePolicy.score(result.matches.size, 10, 10, median, 640, 480, true).candidate)
            }
        } finally { first.release(); second.release(); homography.release() }
    }

    @Test fun processorUsesGyroBracketingAndRejectsUnknownCameraClock() {
        val first = texture(); val second = translated(first, 4.0, 2.0); val third = translated(first, 8.0, 4.0)
        val history = PreviewGyroHistory()
        repeat(41) { history.add(PreviewGyroSample(900_000_000L + it * 10_000_000, 0.0, 0.0, 0.0)) }
        val setup = PreviewCameraSettings(true, true, 90, 6.0, 4.5, 3.0,
            PreviewCrop(0, 0, 4000, 3000), "synthetic", FrameMetadataStore())
        try {
            LivePreviewProcessor(history).use { processor ->
                processor.configure(setup)
                processor.process(frame(first, 1_000_000_000), "session")
                processor.process(frame(second, 1_100_000_000), "session")
                val supported = processor.process(frame(third, 1_200_000_000), "session")
                assertTrue(supported, supported.contains("candidate keyframe"))
                processor.configure(setup.copy(realtimeClock = false))
                processor.process(frame(first, 1_000_000_000), "session")
                processor.process(frame(second, 1_100_000_000), "session")
                val unknown = processor.process(frame(third, 1_200_000_000), "session")
                assertTrue(unknown, unknown.contains("camera_clock_unknown") && unknown.contains("skip"))
            }
        } finally { first.release(); second.release(); third.release() }
    }
}
