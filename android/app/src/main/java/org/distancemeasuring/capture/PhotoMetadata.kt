package org.distancemeasuring.capture

import android.graphics.BitmapFactory
import androidx.exifinterface.media.ExifInterface
import org.json.JSONObject
import java.io.File

object PhotoMetadata {
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
