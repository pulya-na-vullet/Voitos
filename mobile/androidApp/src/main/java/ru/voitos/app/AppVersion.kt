package ru.voitos.app

/**
 * Версия установленного APK (BuildConfig).
 * Сравнивается с min_app_version_code с бэкенда.
 */
object AppVersion {
    val code: Int get() = BuildConfig.VERSION_CODE
    val name: String get() = BuildConfig.VERSION_NAME
}
