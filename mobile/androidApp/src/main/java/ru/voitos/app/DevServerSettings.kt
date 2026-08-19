package ru.voitos.app

import android.content.ContentUris
import android.content.ContentValues
import android.content.Context
import android.net.Uri
import android.os.Build
import android.os.Environment
import android.provider.MediaStore
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.net.URI

/**
 * Сохранение API-адреса так, чтобы он переживал переустановку APK.
 *
 * 1) SharedPreferences — пока приложение не удалили
 * 2) MediaStore Downloads + File в Download/ — часто остаётся на Huawei после uninstall
 * 3) При старте всегда пытаемся подтянуть бэкап, если prefs пустые/дефолтные
 */
object DevServerSettings {
    private const val BACKUP_NAME = "voitos_dev_server.json"
    private const val DOWNLOADS_NAME = "voitos_dev_server.json"

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

    fun isDefaultEmulatorUrl(baseUrl: String): Boolean =
        baseUrl.contains("10.0.2.2") || baseUrl.isBlank()

    fun save(context: Context, store: SessionStore, baseUrl: String) {
        val normalized = baseUrl.trim().trimEnd('/')
        if (normalized.isBlank()) return
        store.baseUrl = normalized
        store.addRecentBaseUrl(normalized)
        writeBackup(context, normalized, store.recentBaseUrls)
    }

    /** Вернёт восстановленный URL или null. */
    fun restoreIfNeeded(context: Context, store: SessionStore): String? {
        if (store.hasCustomBaseUrl && !isDefaultEmulatorUrl(store.baseUrl)) {
            // Подтянем недавние из бэкапа, если prefs пустые.
            if (store.recentBaseUrls.isEmpty()) {
                readBackup(context)?.optJSONArray("recent")?.let { recent ->
                    val list = buildList {
                        for (i in 0 until recent.length()) {
                            val s = recent.optString(i).trim().trimEnd('/')
                            if (s.isNotBlank()) add(s)
                        }
                    }
                    if (list.isNotEmpty()) store.recentBaseUrls = list
                }
            }
            return null
        }

        val fromDisk = readBackup(context) ?: return null
        val url = fromDisk.optString("base_url").trim().trimEnd('/')
        if (url.isBlank() || isDefaultEmulatorUrl(url)) return null
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
            store.recentBaseUrls = list.ifEmpty { listOf(url) }
        } else {
            store.addRecentBaseUrl(url)
        }
        return url
    }

    fun writeBackup(context: Context, baseUrl: String, recent: List<String>) {
        val payload = JSONObject()
            .put("base_url", baseUrl)
            .put("recent", JSONArray(recent.take(6)))
            .toString()
        runCatching {
            val dir = context.getExternalFilesDir("debug") ?: context.filesDir
            File(dir, BACKUP_NAME).writeText(payload)
        }
        runCatching {
            File(context.filesDir, BACKUP_NAME).writeText(payload)
        }
        runCatching { writeDownloadsFile(context, payload) }
        runCatching { writeDownloadsMediaStore(context, payload) }
    }

    fun readBackup(context: Context): JSONObject? {
        // MediaStore / public Download first — это то, что переживает uninstall.
        readDownloadsMediaStore(context)?.let { return it }
        readDownloadsFile()?.let { return it }
        val candidates = listOfNotNull(
            File(context.filesDir, BACKUP_NAME),
            context.getExternalFilesDir("debug")?.let { File(it, BACKUP_NAME) },
        )
        for (f in candidates) {
            val text = runCatching { if (f.exists()) f.readText() else null }.getOrNull()
            if (!text.isNullOrBlank()) {
                return runCatching { JSONObject(text) }.getOrNull()
            }
        }
        return null
    }

    private fun writeDownloadsFile(context: Context, payload: String) {
        val downloads = Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS)
            ?: return
        if (!downloads.exists()) downloads.mkdirs()
        File(downloads, DOWNLOADS_NAME).writeText(payload)
        // Дубль в Documents — на части EMUI Download чистят агрессивнее.
        val docs = Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOCUMENTS)
        if (docs != null) {
            if (!docs.exists()) docs.mkdirs()
            val voitos = File(docs, "Voitos").also { it.mkdirs() }
            File(voitos, DOWNLOADS_NAME).writeText(payload)
        }
    }

    private fun readDownloadsFile(): JSONObject? {
        val paths = listOfNotNull(
            Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS)
                ?.let { File(it, DOWNLOADS_NAME) },
            Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOCUMENTS)
                ?.let { File(File(it, "Voitos"), DOWNLOADS_NAME) },
        )
        for (f in paths) {
            val text = runCatching { if (f.canRead() && f.exists()) f.readText() else null }.getOrNull()
            if (!text.isNullOrBlank()) {
                return runCatching { JSONObject(text) }.getOrNull()
            }
        }
        return null
    }

    private fun writeDownloadsMediaStore(context: Context, payload: String) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) return
        val resolver = context.contentResolver
        // Удаляем старые копии с тем же именем.
        resolver.query(
            MediaStore.Downloads.EXTERNAL_CONTENT_URI,
            arrayOf(MediaStore.MediaColumns._ID),
            "${MediaStore.MediaColumns.DISPLAY_NAME}=?",
            arrayOf(DOWNLOADS_NAME),
            null,
        )?.use { cursor ->
            while (cursor.moveToNext()) {
                val id = cursor.getLong(0)
                val uri = ContentUris.withAppendedId(MediaStore.Downloads.EXTERNAL_CONTENT_URI, id)
                runCatching { resolver.delete(uri, null, null) }
            }
        }
        val values = ContentValues().apply {
            put(MediaStore.MediaColumns.DISPLAY_NAME, DOWNLOADS_NAME)
            put(MediaStore.MediaColumns.MIME_TYPE, "application/json")
            put(MediaStore.MediaColumns.RELATIVE_PATH, Environment.DIRECTORY_DOWNLOADS)
            put(MediaStore.MediaColumns.IS_PENDING, 1)
        }
        val uri: Uri = resolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values) ?: return
        resolver.openOutputStream(uri)?.use { out ->
            out.write(payload.toByteArray(Charsets.UTF_8))
            out.flush()
        } ?: return
        values.clear()
        values.put(MediaStore.MediaColumns.IS_PENDING, 0)
        resolver.update(uri, values, null, null)
    }

    private fun readDownloadsMediaStore(context: Context): JSONObject? {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) return null
        val resolver = context.contentResolver
        resolver.query(
            MediaStore.Downloads.EXTERNAL_CONTENT_URI,
            arrayOf(MediaStore.MediaColumns._ID),
            "${MediaStore.MediaColumns.DISPLAY_NAME}=?",
            arrayOf(DOWNLOADS_NAME),
            "${MediaStore.MediaColumns.DATE_MODIFIED} DESC",
        )?.use { cursor ->
            if (!cursor.moveToFirst()) return null
            val id = cursor.getLong(0)
            val uri = ContentUris.withAppendedId(MediaStore.Downloads.EXTERNAL_CONTENT_URI, id)
            val text = resolver.openInputStream(uri)?.bufferedReader()?.use { it.readText() }
            if (!text.isNullOrBlank()) {
                return runCatching { JSONObject(text) }.getOrNull()
            }
        }
        return null
    }
}
