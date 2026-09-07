package ru.voitos.app

import android.content.Context
import androidx.core.content.edit
import org.json.JSONArray

/** Простое хранение access token + debug-настроек сервера. */
class SessionStore(context: Context) {
    private val prefs = context.getSharedPreferences("voitos_session", Context.MODE_PRIVATE)

    var accessToken: String?
        get() = prefs.getString(KEY_TOKEN, null)
        set(value) = prefs.edit(commit = true) { putString(KEY_TOKEN, value) }

    var displayName: String
        get() = prefs.getString(KEY_NAME, "") ?: ""
        set(value) = prefs.edit(commit = true) { putString(KEY_NAME, value) }

    var baseUrl: String
        get() = prefs.getString(KEY_BASE, VoitosApi.DEFAULT_BASE_URL) ?: VoitosApi.DEFAULT_BASE_URL
        set(value) = prefs.edit(commit = true) {
            putString(KEY_BASE, value.trim().trimEnd('/'))
            putBoolean(KEY_CUSTOM_BASE, true)
        }

    /** true если пользователь хотя бы раз сохранил свой URL (не дефолт эмулятора). */
    var hasCustomBaseUrl: Boolean
        get() = prefs.getBoolean(KEY_CUSTOM_BASE, false)
        set(value) = prefs.edit(commit = true) { putBoolean(KEY_CUSTOM_BASE, value) }

    var recentBaseUrls: List<String>
        get() {
            val raw = prefs.getString(KEY_RECENT, null) ?: return emptyList()
            return runCatching {
                val arr = JSONArray(raw)
                buildList {
                    for (i in 0 until arr.length()) {
                        val s = arr.optString(i).trim().trimEnd('/')
                        if (s.isNotBlank()) add(s)
                    }
                }
            }.getOrDefault(emptyList())
        }
        set(value) = prefs.edit(commit = true) {
            putString(KEY_RECENT, JSONArray(value.take(6)).toString())
        }

    fun addRecentBaseUrl(url: String) {
        val n = url.trim().trimEnd('/')
        if (n.isBlank()) return
        recentBaseUrls = listOf(n) + recentBaseUrls.filter { it != n }
    }

    /** Последний телефон (debug): не сбрасываем при «Выйти». */
    var lastPhone: String
        get() = prefs.getString(KEY_PHONE, "") ?: ""
        set(value) = prefs.edit(commit = true) { putString(KEY_PHONE, value) }

    /** Последний OTP из debug_code (удобно при повторном входе в dev). */
    var lastDebugCode: String
        get() = prefs.getString(KEY_DEBUG_CODE, "") ?: ""
        set(value) = prefs.edit(commit = true) { putString(KEY_DEBUG_CODE, value) }

    /** На устройстве уже задавали постоянный PIN. */
    var hasPinSetup: Boolean
        get() = prefs.getBoolean(KEY_HAS_PIN, false)
        set(value) = prefs.edit(commit = true) { putBoolean(KEY_HAS_PIN, value) }

    /** Бэкенд сказал показать онбординг (кэш до /onboarding). */
    var needsOnboarding: Boolean
        get() = prefs.getBoolean(KEY_NEEDS_ONBOARDING, false)
        set(value) = prefs.edit(commit = true) { putBoolean(KEY_NEEDS_ONBOARDING, value) }

    /** Уже был хотя бы один запуск после установки (не чистая установка). */
    var hasLaunchedBefore: Boolean
        get() = prefs.getBoolean(KEY_LAUNCHED, false)
        set(value) = prefs.edit(commit = true) { putBoolean(KEY_LAUNCHED, value) }

    fun clearSession() {
        // Выход: только сессия. Debug-поля (URL, телефон, код) и флаг PIN оставляем.
        prefs.edit(commit = true) {
            remove(KEY_TOKEN)
            remove(KEY_NAME)
            remove(KEY_NEEDS_ONBOARDING)
        }
    }

    /** @deprecated используйте clearSession — debug-данные сохраняются. */
    fun clear() = clearSession()

    fun isLoggedIn(): Boolean = !accessToken.isNullOrBlank()

    var selectedGroupId: Int?
        get() {
            val v = prefs.getInt(KEY_GROUP, 0)
            return if (v > 0) v else null
        }
        set(value) = prefs.edit(commit = true) {
            if (value != null && value > 0) putInt(KEY_GROUP, value) else remove(KEY_GROUP)
        }

    companion object {
        private const val KEY_TOKEN = "access_token"
        private const val KEY_NAME = "display_name"
        private const val KEY_BASE = "base_url"
        private const val KEY_CUSTOM_BASE = "has_custom_base_url"
        private const val KEY_RECENT = "recent_base_urls"
        private const val KEY_PHONE = "last_phone"
        private const val KEY_DEBUG_CODE = "last_debug_code"
        private const val KEY_HAS_PIN = "has_pin_setup"
        private const val KEY_NEEDS_ONBOARDING = "needs_onboarding"
        private const val KEY_LAUNCHED = "has_launched_before"
        private const val KEY_GROUP = "selected_group_id"
    }
}
