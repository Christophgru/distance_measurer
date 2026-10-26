package org.distancemeasuring.capture

import kotlin.math.cos
import kotlin.math.sin
import kotlin.math.sqrt

data class PreviewPoint(val x: Double, val y: Double) {
    fun inBounds(width: Int, height: Int) = x.isFinite() && y.isFinite() && x >= 0 && y >= 0 && x < width && y < height
    fun distance(other: PreviewPoint) = kotlin.math.hypot(x - other.x, y - other.y)
}

/** Minimal SO(3) port of P3's midpoint right-composition; not a new pose solver. */
object PreviewRotation {
    fun identity() = doubleArrayOf(1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)
    fun transpose(a: DoubleArray) = DoubleArray(9) { i -> a[(i % 3) * 3 + i / 3] }
    fun multiply(a: DoubleArray, b: DoubleArray) = DoubleArray(9) { i ->
        (0..2).sumOf { k -> a[i / 3 * 3 + k] * b[k * 3 + i % 3] }
    }
    fun apply(a: DoubleArray, x: Double, y: Double, z: Double) = doubleArrayOf(
        a[0] * x + a[1] * y + a[2] * z, a[3] * x + a[4] * y + a[5] * z,
        a[6] * x + a[7] * y + a[8] * z)
    fun exponential(x: Double, y: Double, z: Double): DoubleArray {
        val theta2 = x * x + y * y + z * z
        val theta = sqrt(theta2)
        val a = if (theta < 1e-8) 1 - theta2 / 6 else sin(theta) / theta
        val b = if (theta < 1e-8) .5 - theta2 / 24 else (1 - cos(theta)) / theta2
        val skew = doubleArrayOf(0.0, -z, y, z, 0.0, -x, -y, x, 0.0)
        val square = multiply(skew, skew)
        return identity().also { result -> for (i in result.indices) result[i] += a * skew[i] + b * square[i] }
    }
    fun cameraRayRotation(passiveDevice: DoubleArray, sensorOrientationDegrees: Int): DoubleArray {
        require(sensorOrientationDegrees in listOf(0, 90, 180, 270))
        val mount = multiply(exponential(0.0, 0.0, -Math.toRadians(sensorOrientationDegrees.toDouble())),
            doubleArrayOf(1.0, 0.0, 0.0, 0.0, -1.0, 0.0, 0.0, 0.0, -1.0))
        return multiply(multiply(mount, passiveDevice), transpose(mount))
    }
}

data class PreviewIntrinsics(val fx: Double, val fy: Double, val cx: Double, val cy: Double, val skew: Double = 0.0) {
    init { require(listOf(fx, fy, cx, cy, skew).all { it.isFinite() } && fx > 0 && fy > 0) }
    fun rotate(pixel: PreviewPoint, rotation: DoubleArray, target: PreviewIntrinsics = this): PreviewPoint? {
        val y = (pixel.y - cy) / fy
        val x = (pixel.x - cx - skew * y) / fx
        val ray = PreviewRotation.apply(rotation, x, y, 1.0)
        if (!ray.all { it.isFinite() } || ray[2] <= 1e-9) return null
        return PreviewPoint(target.fx * ray[0] / ray[2] + target.skew * ray[1] / ray[2] + target.cx,
            target.fy * ray[1] / ray[2] + target.cy)
    }
}

fun previewMedian(values: List<Double>): Double? {
    if (values.isEmpty()) return null
    val sorted = values.sorted()
    return if (sorted.size % 2 == 0) (sorted[sorted.size / 2 - 1] + sorted[sorted.size / 2]) / 2 else sorted[sorted.size / 2]
}

fun previewCell(point: PreviewPoint, width: Int, height: Int, rows: Int = 6, cols: Int = 8): Int {
    require(rows in 1..height && cols in 1..width && point.inBounds(width, height))
    val x = (1 until cols).count { point.x >= it * width / cols }
    val y = (1 until rows).count { point.y >= it * height / rows }
    return y * cols + x
}

data class PreviewCrop(val left: Int, val top: Int, val right: Int, val bottom: Int) {
    val width: Int get() = right - left
    val height: Int get() = bottom - top
    init { require(width > 0 && height > 0) }
}

/** Focal/sensor/crop approximation; no calibrated distortion or principal point. */
fun approximatePreviewIntrinsics(frame: PreviewLuma, sensorWidthMm: Double, sensorHeightMm: Double,
                                focalMm: Double, active: PreviewCrop, crop: PreviewCrop): PreviewIntrinsics {
    require(listOf(sensorWidthMm, sensorHeightMm, focalMm).all { it.isFinite() && it > 0 })
    require(crop.left >= active.left && crop.top >= active.top && crop.right <= active.right && crop.bottom <= active.bottom)
    val aspect = frame.bufferWidth.toDouble() / frame.bufferHeight
    val effectiveWidth = minOf(crop.width.toDouble(), crop.height * aspect)
    val effectiveHeight = minOf(crop.height.toDouble(), crop.width / aspect)
    val left = crop.left + (crop.width - effectiveWidth) / 2
    val top = crop.top + (crop.height - effectiveHeight) / 2
    val sx = frame.bufferWidth / effectiveWidth
    val sy = frame.bufferHeight / effectiveHeight
    val cx = (active.left + (active.width - 1) / 2.0 - left + .5) * sx - .5
    val cy = (active.top + (active.height - 1) / 2.0 - top + .5) * sy - .5
    return PreviewIntrinsics(focalMm / sensorWidthMm * active.width * sx / frame.sampleStep,
        focalMm / sensorHeightMm * active.height * sy / frame.sampleStep,
        (cx - frame.cropLeft) / frame.sampleStep, (cy - frame.cropTop) / frame.sampleStep)
}
