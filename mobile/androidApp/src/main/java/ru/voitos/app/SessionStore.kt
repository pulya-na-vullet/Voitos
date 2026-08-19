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

    /** Последний телефон (debug): не сбрасываем при «Выйти». */
    var lastPhone: String
        get() = prefs.getString(KEY_PHONE, "") ?: ""
        set(value) = prefs.edit { putString(KEY_PHONE, value) }

    /** Последний OTP из debug_code (удобно при повторном входе в dev). */
    var lastDebugCode: String
        get() = prefs.getString(KEY_DEBUG_CODE, "") ?: ""
        set(value) = prefs.edit { putString(KEY_DEBUG_CODE, value) }

    /** Уже был хотя бы один запуск после установки (не чистая установка). */
    var hasLaunchedBefore: Boolean
        get() = prefs.getBoolean(KEY_LAUNCHED, false)
        set(value) = prefs.edit { putBoolean(KEY_LAUNCHED, value) }

    fun clearSession() {
        // Выход: только сессия. Debug-поля (URL, телефон, код) оставляем.
        prefs.edit {
            remove(KEY_TOKEN)
            remove(KEY_NAME)
        }
    }

    /** @deprecated используйте clearSession — debug-данные сохраняются. */
    fun clear() = clearSession()

    fun isLoggedIn(): Boolean = !accessToken.isNullOrBlank()

    companion object {
        private const val KEY_TOKEN = "access_token"
        private const val KEY_NAME = "display_name"
        private const val KEY_BASE = "base_url"
        private const val KEY_PHONE = "last_phone"
        private const val KEY_DEBUG_CODE = "last_debug_code"
        private const val KEY_LAUNCHED = "has_launched_before"
    }
}
