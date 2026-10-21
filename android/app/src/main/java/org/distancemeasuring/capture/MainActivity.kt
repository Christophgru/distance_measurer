package org.distancemeasuring.capture

import android.Manifest
import android.content.pm.PackageManager
import android.hardware.camera2.CameraCharacteristics
import android.os.Bundle
import android.view.Surface
import android.widget.Button
import android.widget.TextView
import androidx.activity.ComponentActivity
import androidx.activity.result.contract.ActivityResultContracts
import androidx.camera.camera2.interop.Camera2CameraInfo
import androidx.camera.camera2.interop.ExperimentalCamera2Interop
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageCapture
import androidx.camera.core.Preview
import androidx.camera.core.resolutionselector.ResolutionSelector
import androidx.camera.core.resolutionselector.ResolutionStrategy
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.core.content.ContextCompat
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import org.json.JSONObject

class MainActivity : ComponentActivity() {
    private lateinit var recorder: CaptureRecorder
    private lateinit var preview: PreviewView
    private lateinit var start: Button
    private lateinit var capture: Button
    private lateinit var end: Button
    private var imageCapture: ImageCapture? = null
    private var cameraMetadata = JSONObject()
    private var state = RecordingState()
    private var cameraMessage = "preparing camera"
    private var bindingCamera = false
    private var commandPending = false

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
        recorder = CaptureRecorder(this) { update ->
            if (!isDestroyed) {
                state = update
                commandPending = false
                render()
            }
        }
        start.setOnClickListener {
            commandPending = true
            render()
            recorder.start()
        }
        capture.setOnClickListener {
            imageCapture?.let { camera ->
                commandPending = true
                render()
                recorder.takePhoto(camera, cameraMetadata,
                    preview.display?.rotation ?: Surface.ROTATION_0)
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

    @androidx.annotation.OptIn(ExperimentalCamera2Interop::class)
    private fun bindCamera() {
        if (bindingCamera || imageCapture != null) return
        bindingCamera = true
        cameraMessage = "preparing camera"
        val future = ProcessCameraProvider.getInstance(this)
        future.addListener({
            if (isDestroyed) return@addListener
            try {
                val provider = future.get()
                val selector = if (provider.hasCamera(CameraSelector.DEFAULT_BACK_CAMERA)) {
                    CameraSelector.DEFAULT_BACK_CAMERA
                } else CameraSelector.DEFAULT_FRONT_CAMERA
                val cameraPreview = Preview.Builder().build().also {
                    it.surfaceProvider = preview.surfaceProvider
                }
                val photos = ImageCapture.Builder()
                    .setCaptureMode(ImageCapture.CAPTURE_MODE_MAXIMIZE_QUALITY)
                    .setJpegQuality(100)
                    .setResolutionSelector(ResolutionSelector.Builder()
                        .setResolutionStrategy(ResolutionStrategy.HIGHEST_AVAILABLE_STRATEGY)
                        .build())
                    .build()
                val camera = provider.bindToLifecycle(this, selector, cameraPreview, photos)
                val info = Camera2CameraInfo.from(camera.cameraInfo)
                val sensorSize = info.getCameraCharacteristic(CameraCharacteristics.SENSOR_INFO_PHYSICAL_SIZE)
                cameraMetadata = JSONObject().put("camera_id", info.cameraId)
                    .put("lens_facing", info.getCameraCharacteristic(CameraCharacteristics.LENS_FACING))
                    .put("sensor_orientation_degrees",
                        info.getCameraCharacteristic(CameraCharacteristics.SENSOR_ORIENTATION))
                    .put("sensor_width_mm", sensorSize?.width ?: JSONObject.NULL)
                    .put("sensor_height_mm", sensorSize?.height ?: JSONObject.NULL)
                imageCapture = photos
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
        recorder.end("activity_paused")
        super.onPause()
    }

    override fun onDestroy() {
        recorder.destroy()
        super.onDestroy()
    }
}
