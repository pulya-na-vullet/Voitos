plugins {
    // Skeleton — open in Android Studio / Fleet and sync.
    // Full multiplatform setup: kotlin("multiplatform") + android + ios targets.
    id("org.jetbrains.kotlin.multiplatform") version "2.0.21" apply false
    id("org.jetbrains.kotlin.plugin.serialization") version "2.0.21" apply false
    id("com.android.application") version "8.7.2" apply false
    id("com.android.library") version "8.7.2" apply false
}

rootProject.name = "voitos-mobile"
include(":shared")
include(":androidApp")
