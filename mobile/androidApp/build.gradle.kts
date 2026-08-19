plugins {
    id("com.android.application")
    kotlin("android")
    // Firebase: id("com.google.gms.google-services") после добавления google-services.json
}

android {
    namespace = "ru.voitos.app"
    compileSdk = 35
    defaultConfig {
        applicationId = "ru.voitos.app"
        minSdk = 26
        targetSdk = 35
        versionCode = 1
        versionName = "0.1.0-kmp"
    }
    buildFeatures { compose = true }
    composeOptions { kotlinCompilerExtensionVersion = "1.5.15" }
}

dependencies {
    implementation(project(":shared"))
    implementation(platform("androidx.compose:compose-bom:2024.10.01"))
    implementation("androidx.activity:activity-compose:1.9.3")
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.material3:material3")
    implementation("io.coil-kt:coil-compose:2.7.0")
    // Firebase Messaging (после google-services.json):
    // implementation(platform("com.google.firebase:firebase-bom:33.5.1"))
    // implementation("com.google.firebase:firebase-messaging-ktx")
}
