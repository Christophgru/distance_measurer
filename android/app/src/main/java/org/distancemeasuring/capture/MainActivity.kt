package org.distancemeasuring.capture

import android.Manifest
import android.content.pm.PackageManager
import android.hardware.camera2.CameraCharacteristics
import android.hardware.camera2.CameraManager
import android.os.Build
import android.os.Bundle
import android.util.Size
import android.view.Surface
import android.widget.Button
import android.widget.TextView
import androidx.activity.ComponentActivity
import androidx.activity.result.contract.ActivityResultContracts
import androidx.camera.camera2.interop.Camera2CameraInfo
import androidx.camera.camera2.interop.Camera2Interop
import androidx.camera.camera2.interop.ExperimentalCamera2Interop
import androidx.camera.core.CameraSelector
import androidx.camera.core.Camera
import androidx.camera.core.CameraState
import androidx.camera.core.ExperimentalImageCaptureOutputFormat
import androidx.camera.core.ImageCapture
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.Preview
import androidx.camera.core.resolutionselector.ResolutionSelector
import androidx.camera.core.resolutionselector.ResolutionStrategy
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.core.content.ContextCompat
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import org.json.JSONObject
import kotlin.math.abs

class MainActivity : ComponentActivity() {
    private lateinit var recorder: CaptureRecorder
    private lateinit var preview: PreviewView
    private lateinit var start: Button
    private lateinit var capture: Button
    private lateinit var end: Button
    private var imageCapture: ImageCapture? = null
    private var cameraMetadata = JSONObject()
    private var captureGeometry = CaptureGeometry()
    private var captureStability: CaptureStability? = null
    private var state = RecordingState()
    private var cameraMessage = "preparing camera"
    private var bindingCamera = false
    private var commandPending = false
    private lateinit var previewAnalyzer: PreviewAnalyzer
    private var analysis: ImageAnalysis? = null
    private var analysisMessage = "Preview analysis: preparing"

    private val permissionRequest = registerForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted ->
        if (granted) bindCamera() else {
            cameraMessage = getString(R.string.camera_permission)
            render()
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        val padding = (16 * resources.displayMetrics.density).toInt()
        ViewCompat.setOnApplyWindowInsetsListener(findViewById(R.id.root)) { view, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars() or
                WindowInsetsCompat.Type.displayCutout())
            view.setPadding(padding + bars.left, padding + bars.top,
                padding + bars.right, padding + bars.bottom)
            insets
        }
        preview = findViewById(R.id.preview)
        start = findViewById(R.id.start)
        capture = findViewById(R.id.capture)
        end = findViewById(R.id.end)
        previewAnalyzer = PreviewAnalyzer(onUpdate = { path, message -> preview.post {
            if (!isDestroyed && state.active && !state.ending && state.path == path) {
                findViewById<TextView>(R.id.preview_diagnostics).text = message
            }
        } })
        recorder = CaptureRecorder(this) { update ->
            if (!isDestroyed) {
                state = update
                previewAnalyzer.setSession(if (state.active && !state.ending) state.path else null)
                if (!state.active) captureStability?.stop()
                commandPending = false
                render()
            }
        }
        start.setOnClickListener {
            commandPending = true
            render()
            captureStability?.start()
            recorder.start()
        }
        capture.setOnClickListener {
            imageCapture?.let { camera ->
                commandPending = true
                render()
                val metadata = JSONObject(cameraMetadata.toString())
                    .put("capture_stability", captureStability?.snapshot() ?: JSONObject.NULL)
                recorder.takePhoto(camera, metadata,
                    preview.display?.rotation ?: Surface.ROTATION_0, captureGeometry)
            }
        }
        end.setOnClickListener {
            commandPending = true
            render()
            recorder.end()
        }
        render()
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA) ==
            PackageManager.PERMISSION_GRANTED
        ) bindCamera() else permissionRequest.launch(Manifest.permission.CAMERA)
    }

    @androidx.annotation.OptIn(ExperimentalCamera2Interop::class, ExperimentalImageCaptureOutputFormat::class)
    private fun bindCamera(allowPhysicalPin: Boolean = true, allowAnalysis: Boolean = true) {
        if (bindingCamera || imageCapture != null) return
        bindingCamera = true
        cameraMessage = "preparing camera"
        val future = ProcessCameraProvider.getInstance(this)
        future.addListener({
            if (isDestroyed) return@addListener
            try {
                val provider = future.get()
                val facingSelector = if (provider.hasCamera(CameraSelector.DEFAULT_BACK_CAMERA)) {
                    CameraSelector.DEFAULT_BACK_CAMERA
                } else CameraSelector.DEFAULT_FRONT_CAMERA
                val selectedInfo = facingSelector.filter(provider.availableCameraInfos).first()
                val logicalId = Camera2CameraInfo.from(selectedInfo).cameraId
                val selector = CameraSelector.Builder().addCameraFilter { candidates ->
                    candidates.filter { Camera2CameraInfo.from(it).cameraId == logicalId }
                }.build()
                val manager = getSystemService(CAMERA_SERVICE) as CameraManager
                val logicalCharacteristics = manager.getCameraCharacteristics(logicalId)
                val logical = Build.VERSION.SDK_INT >= 28 && logicalCharacteristics
                    .get(CameraCharacteristics.REQUEST_AVAILABLE_CAPABILITIES)
                    ?.contains(CameraCharacteristics.REQUEST_AVAILABLE_CAPABILITIES_LOGICAL_MULTI_CAMERA) == true
                var physicalId: String? = if (Build.VERSION.SDK_INT >= 28 && allowPhysicalPin && logical) {
                    // Select once, near the first advertised logical focal length.
                    val target = logicalCharacteristics.get(CameraCharacteristics.LENS_INFO_AVAILABLE_FOCAL_LENGTHS)?.firstOrNull() ?: 0f
                    try { logicalCharacteristics.physicalCameraIds.sorted().minByOrNull { id ->
                        val focal = manager.getCameraCharacteristics(id)
                            .get(CameraCharacteristics.LENS_INFO_AVAILABLE_FOCAL_LENGTHS)?.firstOrNull() ?: target
                        abs(focal - target)
                    } } catch (_: Exception) { null }
                } else null
                fun bind(physical: String?, includeAnalysis: Boolean): CameraBinding {
                    val geometry = CaptureGeometry(physical, if (logical) null else logicalId)
                    val previewBuilder = Preview.Builder()
                    val photoBuilder = ImageCapture.Builder()
                    .setOutputFormat(ImageCapture.OUTPUT_FORMAT_JPEG)
                    .setCaptureMode(ImageCapture.CAPTURE_MODE_MAXIMIZE_QUALITY)
                    .setJpegQuality(100)
                    .setResolutionSelector(ResolutionSelector.Builder()
                        .setResolutionStrategy(ResolutionStrategy.HIGHEST_AVAILABLE_STRATEGY)
                        .build())
                    val analysisBuilder = ImageAnalysis.Builder()
                        .setOutputImageFormat(ImageAnalysis.OUTPUT_IMAGE_FORMAT_YUV_420_888)
                        .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
                        .setTargetRotation(Surface.ROTATION_0)
                        .setResolutionSelector(ResolutionSelector.Builder()
                            .setResolutionStrategy(ResolutionStrategy(Size(640, 480),
                                ResolutionStrategy.FALLBACK_RULE_CLOSEST_LOWER_THEN_HIGHER)).build())
                    Camera2Interop.Extender(previewBuilder).setSessionCaptureCallback(geometry)
                    if (Build.VERSION.SDK_INT >= 28 && physical != null) {
                        Camera2Interop.Extender(previewBuilder).setPhysicalCameraId(physical)
                        Camera2Interop.Extender(photoBuilder).setPhysicalCameraId(physical)
                        Camera2Interop.Extender(analysisBuilder).setPhysicalCameraId(physical)
                    }
                    val cameraPreview = previewBuilder.build().also { it.surfaceProvider = preview.surfaceProvider }
                    val photos = photoBuilder.build()
                    val stream = if (includeAnalysis) analysisBuilder.build().also {
                        it.setAnalyzer(previewAnalyzer.executor, previewAnalyzer)
                    } else null
                    val useCases = if (stream == null) arrayOf(cameraPreview, photos) else arrayOf(cameraPreview, photos, stream)
                    return CameraBinding(provider.bindToLifecycle(this, selector, *useCases), photos, geometry, stream)
                }
                fun preserveStillResolution(physical: String?): CameraBinding {
                    val baseline = bind(physical, false)
                    val stillSize = baseline.photos.resolutionInfo?.resolution
                    if (!allowAnalysis || stillSize == null) {
                        analysisMessage = "Preview analysis unavailable; normal JPEG capture retained"
                        return baseline
                    }
                    provider.unbindAll()
                    return try {
                        val combined = bind(physical, true)
                        if (combined.photos.resolutionInfo?.resolution != stillSize) {
                            combined.stream?.clearAnalyzer()
                            provider.unbindAll()
                            analysisMessage = "Preview analysis disabled to preserve full JPEG resolution"
                            bind(physical, false)
                        } else {
                            analysisMessage = "Preview analysis: ready (processes during an active timeline)"
                            combined
                        }
                    } catch (_: Exception) {
                        provider.unbindAll()
                        analysisMessage = "Preview analysis unsupported; normal JPEG capture retained"
                        bind(physical, false)
                    }
                }
                val bound = try { preserveStillResolution(physicalId) } catch (error: Exception) {
                    if (physicalId == null) throw error
                    provider.unbindAll()
                    physicalId = null
                    preserveStillResolution(null)
                }
                val characteristics = physicalId?.let { manager.getCameraCharacteristics(it) } ?: logicalCharacteristics
                val sensorSize = characteristics.get(CameraCharacteristics.SENSOR_INFO_PHYSICAL_SIZE)
                cameraMetadata = JSONObject().put("camera_id", logicalId)
                    .put("physical_camera_id", if (logical) JSONObject.NULL else logicalId)
                    .put("lens_facing", characteristics.get(CameraCharacteristics.LENS_FACING))
                    .put("sensor_orientation_degrees",
                        characteristics.get(CameraCharacteristics.SENSOR_ORIENTATION))
                    .put("sensor_width_mm", sensorSize?.width ?: JSONObject.NULL)
                    .put("sensor_height_mm", sensorSize?.height ?: JSONObject.NULL)
                    .put("sensor_timestamp_clock_domain",
                        if (characteristics.get(CameraCharacteristics.SENSOR_INFO_TIMESTAMP_SOURCE) ==
                            CameraCharacteristics.SENSOR_INFO_TIMESTAMP_SOURCE_REALTIME)
                            "android_elapsed_realtime_ns" else "camera_unknown")
                imageCapture = bound.photos
                captureGeometry = bound.geometry
                analysis = bound.stream
                val executor = ContextCompat.getMainExecutor(this)
                val stability = CaptureStability(bound.camera, characteristics, executor,
                    physicalId?.let { "requested:$it" } ?: if (logical) "unavailable_using_logical_camera" else "single_camera")
                captureStability = stability
                if (state.active) captureStability?.start()
                captureGeometry.onFrame = { frame -> executor.execute {
                    if (captureStability === stability) stability.observe(frame)
                } }
                if (physicalId != null || bound.stream != null) bound.camera.cameraInfo.cameraState.observe(this) { cameraState ->
                    if (cameraState.error?.code == CameraState.ERROR_STREAM_CONFIG && imageCapture === bound.photos) {
                        // Some devices reject a physical JPEG stream asynchronously.
                        imageCapture = null
                        captureStability?.stop()
                        analysis?.clearAnalyzer()
                        preview.post {
                            provider.unbindAll()
                            // First drop optional analysis; only then relax physical pinning.
                            bindCamera(allowPhysicalPin = physicalId != null && bound.stream != null,
                                allowAnalysis = false)
                        }
                    }
                }
                render()
            } catch (error: Exception) {
                imageCapture = null
                cameraMessage = "Camera unavailable: ${error.message}"
                render()
            } finally {
                bindingCamera = false
            }
        }, ContextCompat.getMainExecutor(this))
    }

    private data class CameraBinding(val camera: Camera, val photos: ImageCapture,
                                    val geometry: CaptureGeometry, val stream: ImageAnalysis?)

    private fun render() {
        val cameraReady = imageCapture != null
        start.isEnabled = cameraReady && state.canStart && !commandPending
        capture.isEnabled = cameraReady && state.canTakePhoto && !commandPending
        end.isEnabled = state.canEnd && !commandPending
        findViewById<TextView>(R.id.status).text = "Status: " +
            if (!cameraReady) cameraMessage else if (state.capturing && !state.ending) {
                "recording — taking picture"
            } else state.message
        findViewById<TextView>(R.id.image_count).text = getString(R.string.image_count, state.imageCount)
        findViewById<TextView>(R.id.sensor_count).text = getString(R.string.sensor_count, state.sampleCount)
        if (!state.active || state.ending || analysis == null) {
            findViewById<TextView>(R.id.preview_diagnostics).text = analysisMessage
        }
        findViewById<TextView>(R.id.session_path).text = state.path
    }

    override fun onResume() {
        super.onResume()
        // Also recover when camera access was granted from Android Settings.
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA) ==
            PackageManager.PERMISSION_GRANTED
        ) bindCamera()
    }

    override fun onPause() {
        // Backgrounding the prototype closes the timeline; it is never a background service.
        previewAnalyzer.setSession(null)
        recorder.end("activity_paused")
        super.onPause()
    }

    override fun onDestroy() {
        analysis?.clearAnalyzer()
        previewAnalyzer.close()
        recorder.destroy()
        super.onDestroy()
    }
}
