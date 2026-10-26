plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "org.distancemeasuring.capture"
    compileSdk = 35

    defaultConfig {
        applicationId = "org.distancemeasuring.capture"
        minSdk = 26
        targetSdk = 35
        versionCode = 4
        versionName = "0.4.0"
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
}

dependencies {
    implementation("androidx.activity:activity-ktx:1.10.1")
    implementation("androidx.camera:camera-camera2:1.4.2")
    implementation("androidx.camera:camera-lifecycle:1.4.2")
    implementation("androidx.camera:camera-view:1.4.2")
    implementation("androidx.exifinterface:exifinterface:1.4.1")
    // P7 dependency gate: CameraX/AndroidX have no Shi–Tomasi / pyramidal LK.
    // Reuse the validated OpenCV primitives rather than adding another solver.
    implementation("org.opencv:opencv:4.12.0")
    testImplementation("junit:junit:4.13.2")
    testImplementation("org.json:json:20240303")
    androidTestImplementation("androidx.test.ext:junit:1.2.1")
    androidTestImplementation("androidx.test:runner:1.6.2")
}

tasks.withType<Test>().configureEach {
    systemProperty("keyframe.cases", rootProject.file("../tools/validation/keyframe_cases.json").absolutePath)
    systemProperty("preview.gyro.cases", rootProject.file("../tools/validation/preview_gyro_cases.json").absolutePath)
}
