package org.distancemeasuring.capture

import org.opencv.android.OpenCVLoader
import org.opencv.core.Core
import org.opencv.core.CvType
import org.opencv.core.Mat
import org.opencv.imgcodecs.Imgcodecs
import java.io.File

/** Share initialization between analysis and export; use the existing OpenCV AAR. */
object PreviewNativeRuntime {
    private var loaded: Boolean? = null
    @Synchronized fun available(): Boolean {
        if (loaded == null) {
            loaded = try {
                if (OpenCVLoader.initLocal()) { Core.setNumThreads(1); true } else false
            } catch (_: LinkageError) { false }
        }
        return loaded == true
    }
}

object PreviewPng {
    fun write(frame: PreviewLuma, file: File) {
        try {
            check(PreviewNativeRuntime.available()) { "OpenCV PNG encoder unavailable" }
            require(frame.pixels.size == frame.width * frame.height)
            val matrix = Mat(frame.height, frame.width, CvType.CV_8UC1)
            try {
                matrix.put(0, 0, frame.pixels)
                check(Imgcodecs.imwrite(file.absolutePath, matrix)) { "PNG encoder failed" }
            } finally { matrix.release() }
        } catch (error: LinkageError) {
            throw IllegalStateException("Native PNG encoding unavailable", error)
        }
    }
}
