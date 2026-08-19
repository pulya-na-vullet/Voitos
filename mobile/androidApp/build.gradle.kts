plugins {
    id("com.android.application")
    kotlin("android")
    id("org.jetbrains.kotlin.plugin.compose")
    // Firebase: id("com.google.gms.google-services") после google-services.json
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
    buildFeatures {
        compose = true
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    packaging {
        resources {
            excludes += "/META-INF/{AL2.0,LGPL2.1}"
        }
    }
}

kotlin {
    compilerOptions {
        jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17)
    }
}

dependencies {
    implementation(project(":shared"))
    implementation(platform("androidx.compose:compose-bom:2024.10.01"))
    implementation("androidx.activity:activity-compose:1.9.3")
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-tooling-preview")
    implementation("androidx.compose.foundation:foundation")
    implementation("androidx.compose.material3:material3")
    implementation("io.coil-kt:coil-compose:2.7.0")
    // Ktor types may leak through shared; keep engine available to app module.
    implementation("io.ktor:ktor-client-okhttp:3.0.1")
    debugImplementation("androidx.compose.ui:ui-tooling")
}
