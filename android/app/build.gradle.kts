import java.io.File

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

// Keep one debug identity in the persistent checkout, rather than a container's
// disposable ~/.android directory. An existing local key is never replaced.
val localDebugKeystore = providers.gradleProperty("captureDebugKeystore")
    .map { rootProject.file(it) }
    .getOrElse(rootProject.file(".signing/debug.keystore"))
val prepareLocalDebugKeystore = tasks.register("prepareLocalDebugKeystore") {
    outputs.file(localDebugKeystore)
    onlyIf { !localDebugKeystore.exists() }
    doLast {
        localDebugKeystore.parentFile.mkdirs()
        val existing = File(System.getProperty("user.home"), ".android/debug.keystore")
        if (existing.isFile) {
            existing.copyTo(localDebugKeystore)
        } else {
            val keytool = File(System.getProperty("java.home"), "bin/keytool")
            providers.exec {
                commandLine(
                    keytool, "-genkeypair", "-noprompt", "-storetype", "JKS",
                    "-keystore", localDebugKeystore, "-storepass", "android",
                    "-alias", "androiddebugkey", "-keypass", "android",
                    "-keyalg", "RSA", "-keysize", "2048", "-validity", "10000",
                    "-dname", "CN=Android Debug,O=Android,C=US",
                )
            }.result.get().assertNormalExitValue()
        }
        localDebugKeystore.setReadable(false, false)
        localDebugKeystore.setWritable(false, false)
        localDebugKeystore.setReadable(true, true)
        localDebugKeystore.setWritable(true, true)
    }
}

android {
    namespace = "org.distancemeasuring.capture"
    compileSdk = 35

    defaultConfig {
        applicationId = "org.distancemeasuring.capture"
        minSdk = 26
        targetSdk = 35
        versionCode = 9
        versionName = "0.6.1"
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
    buildFeatures { buildConfig = true }
    signingConfigs.getByName("debug") {
        storeFile = localDebugKeystore
        storePassword = "android"
        keyAlias = "androiddebugkey"
        keyPassword = "android"
    }
    splits {
        abi {
            isEnable = providers.gradleProperty("splitApks").map { it.toBooleanStrict() }
                .getOrElse(false)
            reset()
            include("arm64-v8a", "armeabi-v7a", "x86", "x86_64")
            isUniversalApk = true
        }
    }
}

tasks.matching {
    it.name == "validateSigningDebug" || it.name == "validateSigningDebugAndroidTest"
}.configureEach { dependsOn(prepareLocalDebugKeystore) }

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
    providers.gradleProperty("hybridFixtureOutput").orNull?.let {
        systemProperty("hybrid.fixture.output", rootProject.file(it).absolutePath)
    }
    systemProperty("keyframe.cases", rootProject.file("../tools/validation/keyframe_cases.json").absolutePath)
    systemProperty("preview.gyro.cases", rootProject.file("../tools/validation/preview_gyro_cases.json").absolutePath)
}
