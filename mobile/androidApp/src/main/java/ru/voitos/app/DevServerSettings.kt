package ru.voitos.app

import android.content.Context
import android.os.Environment
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.net.URI

/**
 * Хранение API-адреса для тестовых сборок.
 * SharedPreferences + файл-зеркало (переживает update поверх),
 * плюс копия в Downloads (часто переживает uninstall на Huawei).
 */
object DevServerSettings {
    private const val BACKUP_NAME = "voitos_dev_server.json"
    private const val DOWNLOADS_NAME = "voitos_dev_server.json"
    private const val MAX_RECENT = 6

    data class HostPort(
        val host: String,
        val port: Int = 18765,
        val path: String = "/api/v1",
    ) {
        fun toBaseUrl(): String {
            val p = if (path.startsWith("/")) path else "/$path"
            return "http://$host:$port${p.trimEnd('/')}"
        }
    }

    fun parse(baseUrl: String): HostPort {
        return try {
            val uri = URI(baseUrl.trim())
            val host = uri.host?.takeIf { it.isNotBlank() } ?: "10.0.2.2"
            val port = if (uri.port > 0) uri.port else 18765
            val path = uri.path?.takeIf { it.isNotBlank() && it != "/" } ?: "/api/v1"
            HostPort(host = host, port = port, path = path)
        } catch (_: Exception) {
            HostPort(host = "10.0.2.2")
        }
    }

    fun save(context: Context, store: SessionStore, baseUrl: String) {
        val normalized = baseUrl.trim().trimEnd('/')
        if (normalized.isBlank()) return
        store.baseUrl = normalized
        store.addRecentBaseUrl(normalized)
        writeBackup(context, normalized, store.recentBaseUrls)
    }

    fun restoreIfNeeded(context: Context, store: SessionStore) {
        // Уже сохраняли на этом устройстве — не трогаем.
        if (store.hasCustomBaseUrl) return

        val fromDisk = readBackup(context) ?: return
        val url = fromDisk.optString("base_url").trim().trimEnd('/')
        if (url.isBlank()) return
        store.baseUrl = url
        store.hasCustomBaseUrl = true
        val recent = fromDisk.optJSONArray("recent")
        if (recent != null) {
            val list = buildList {
                for (i in 0 until recent.length()) {
                    val s = recent.optString(i).trim().trimEnd('/')
                    if (s.isNotBlank()) add(s)
                }
            }
            store.recentBaseUrls = list
        } else {
            store.addRecentBaseUrl(url)
        }
    }

    private fun writeBackup(context: Context, baseUrl: String, recent: List<String>) {
        val payload = JSONObject()
            .put("base_url", baseUrl)
            .put("recent", JSONArray(recent))
            .toString()
        runCatching {
            val dir = context.getExternalFilesDir("debug") ?: context.filesDir
            File(dir, BACKUP_NAME).writeText(payload)
        }
        runCatching {
            File(context.filesDir, BACKUP_NAME).writeText(payload)
        }
        // Публичные Downloads — чтобы IP не пропадал после uninstall APK.
        runCatching {
            val downloads = Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS)
            if (downloads != null) {
                if (!downloads.exists()) downloads.mkdirs()
                File(downloads, DOWNLOADS_NAME).writeText(payload)
            }
        }
    }

    private fun readBackup(context: Context): JSONObject? {
        val candidates = listOfNotNull(
            File(context.filesDir, BACKUP_NAME),
            context.getExternalFilesDir("debug")?.let { File(it, BACKUP_NAME) },
            Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS)
                ?.let { File(it, DOWNLOADS_NAME) },
        )
        for (f in candidates) {
            val text = runCatching { if (f.exists()) f.readText() else null }.getOrNull()
            if (!text.isNullOrBlank()) {
                return runCatching { JSONObject(text) }.getOrNull()
            }
        }
        return null
    }
}
