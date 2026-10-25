package org.distancemeasuring.capture

import android.graphics.BitmapFactory
import android.graphics.ImageFormat
import androidx.camera.core.ImageProxy
import androidx.exifinterface.media.ExifInterface
import org.json.JSONObject
import java.io.File

object PhotoMetadata {
    /** Preserve the JPEG's own frame timestamp; never associate a preview callback by proximity. */
    fun saveCapturedJpeg(
        file: File, image: ImageProxy, camera: JSONObject, targetRotation: Int,
        frameMetadata: JSONObject? = null,
    ): JSONObject {
        check(image.format == ImageFormat.JPEG) { "Expected a JPEG camera buffer" }
        val buffer = image.planes[0].buffer.duplicate()
        buffer.rewind()
        val bytes = ByteArray(buffer.remaining())
        buffer.get(bytes)
        file.writeBytes(bytes)
        // CameraX returns unrotated pixels. Store its requested display rotation
        // in EXIF, preserving the working-pixel convention (lecture 002 pp. 65–76).
        val exif = ExifInterface(file)
        val orientation = when (image.imageInfo.rotationDegrees) {
            0 -> ExifInterface.ORIENTATION_NORMAL
            90 -> ExifInterface.ORIENTATION_ROTATE_90
            180 -> ExifInterface.ORIENTATION_ROTATE_180
            270 -> ExifInterface.ORIENTATION_ROTATE_270
            else -> error("Unsupported JPEG rotation")
        }
        exif.setAttribute(ExifInterface.TAG_ORIENTATION, orientation.toString())
        exif.saveAttributes()
        val metadata = read(file, camera, targetRotation)
            .put("sensor_timestamp_ns", image.imageInfo.timestamp.takeIf { it > 0 } ?: JSONObject.NULL)
            .put("sensor_timestamp_clock_domain", camera.optString("sensor_timestamp_clock_domain", "camera_unknown"))
            .put("sensor_timestamp_source", "camerax_image_info")
            .put("metadata_source", "jpeg_exif_camera_characteristics_and_image_info")
            .put("capture_result_source", "unavailable")
        frameMetadata?.keys()?.forEach { name ->
            // A single-camera ID from characteristics remains valid if this
            // device does not expose the logical-multicamera result key.
            if (name != "physical_camera_id" || !frameMetadata.isNull(name)) {
                metadata.put(name, frameMetadata.get(name))
            }
        }
        return metadata
    }

    fun read(file: File, camera: JSONObject, targetRotation: Int): JSONObject {
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeFile(file.absolutePath, bounds)
        check(bounds.outWidth > 0 && bounds.outHeight > 0) { "Captured JPEG cannot be decoded" }
        val exif = ExifInterface(file)
        fun optionalNumber(tag: String): Any =
            if (exif.hasAttribute(tag)) exif.getAttributeDouble(tag, Double.NaN)
                .takeIf { it.isFinite() } ?: JSONObject.NULL else JSONObject.NULL

        return JSONObject(camera.toString())
            .put("width", bounds.outWidth)
            .put("height", bounds.outHeight)
            .put("exif_orientation", exif.getAttributeInt(ExifInterface.TAG_ORIENTATION, 1))
            .put("rotation_degrees", exif.rotationDegrees)
            .put("is_flipped", exif.isFlipped)
            .put("target_rotation", targetRotation)
            .put("focal_length_mm", optionalNumber(ExifInterface.TAG_FOCAL_LENGTH))
            .put("focal_length_35mm", optionalNumber(ExifInterface.TAG_FOCAL_LENGTH_IN_35MM_FILM))
            .put("exposure_time_s", optionalNumber(ExifInterface.TAG_EXPOSURE_TIME))
            .put("iso", optionalNumber(ExifInterface.TAG_PHOTOGRAPHIC_SENSITIVITY))
            .put("sensor_timestamp_ns", JSONObject.NULL)
            .put("sensor_timestamp_clock_domain", JSONObject.NULL)
            .put("metadata_source", "jpeg_exif_and_camera_characteristics")
    }
}
