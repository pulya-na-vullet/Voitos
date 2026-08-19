package ru.voitos.app

import android.content.Context
import androidx.core.content.edit

/** Простое хранение access token (SharedPreferences). */
class SessionStore(context: Context) {
    private val prefs = context.getSharedPreferences("voitos_session", Context.MODE_PRIVATE)

    var accessToken: String?
        get() = prefs.getString(KEY_TOKEN, null)
        set(value) = prefs.edit { putString(KEY_TOKEN, value) }

    var displayName: String
        get() = prefs.getString(KEY_NAME, "") ?: ""
        set(value) = prefs.edit { putString(KEY_NAME, value) }

    var baseUrl: String
        get() = prefs.getString(KEY_BASE, VoitosApi.DEFAULT_BASE_URL) ?: VoitosApi.DEFAULT_BASE_URL
        set(value) = prefs.edit { putString(KEY_BASE, value) }

    /** Уже был хотя бы один запуск после установки (не чистая установка). */
    var hasLaunchedBefore: Boolean
        get() = prefs.getBoolean(KEY_LAUNCHED, false)
        set(value) = prefs.edit { putBoolean(KEY_LAUNCHED, value) }

    fun clear() {
        // Не трогаем hasLaunchedBefore и baseUrl — это не «чистая установка».
        prefs.edit {
            remove(KEY_TOKEN)
            remove(KEY_NAME)
        }
    }

    fun isLoggedIn(): Boolean = !accessToken.isNullOrBlank()

    companion object {
        private const val KEY_TOKEN = "access_token"
        private const val KEY_NAME = "display_name"
        private const val KEY_BASE = "base_url"
        private const val KEY_LAUNCHED = "has_launched_before"
    }
}
