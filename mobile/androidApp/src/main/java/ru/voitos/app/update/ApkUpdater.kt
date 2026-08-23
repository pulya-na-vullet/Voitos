package ru.voitos.app.update

import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.provider.Settings
import androidx.core.content.FileProvider
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request
import java.io.File
import java.io.FileOutputStream
import java.util.concurrent.TimeUnit
import kotlin.coroutines.coroutineContext

/**
 * Скачивание APK обновления с прогрессом и запуск системной установки
 * (как в Telegram: download → install).
 */
object ApkUpdater {
    private const val PROVIDER_SUFFIX = ".fileprovider"
    private const val SUBDIR = "updates"
    private const val APK_NAME = "voitos-update.apk"

    private val http: OkHttpClient by lazy {
        OkHttpClient.Builder()
            .connectTimeout(30, TimeUnit.SECONDS)
            .readTimeout(5, TimeUnit.MINUTES)
            .writeTimeout(5, TimeUnit.MINUTES)
            .followRedirects(true)
            .followSslRedirects(true)
            .build()
    }

    fun canInstallPackages(context: Context): Boolean {
        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            context.packageManager.canRequestPackageInstalls()
        } else {
            true
        }
    }

    fun installPermissionSettingsIntent(context: Context): Intent {
        return Intent(
            Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
            Uri.parse("package:${context.packageName}"),
        )
    }

    fun apkFile(context: Context): File {
        val dir = File(context.cacheDir, SUBDIR).apply { mkdirs() }
        return File(dir, APK_NAME)
    }

    /**
     * @param onProgress progress 0f..1f; totalBytes может быть -1, если сервер не отдал Content-Length
     */
    suspend fun download(
        context: Context,
        apkUrl: String,
        onProgress: (downloaded: Long, total: Long) -> Unit,
    ): File = withContext(Dispatchers.IO) {
        val url = apkUrl.trim()
        require(url.isNotEmpty()) { "Нет ссылки на APK" }

        val target = apkFile(context)
        val partial = File(target.parentFile, "$APK_NAME.part")
        if (partial.exists()) partial.delete()
        if (target.exists()) target.delete()

        val request = Request.Builder()
            .url(url)
            .header("User-Agent", "Voitos-Android-Updater")
            .get()
            .build()

        http.newCall(request).execute().use { response ->
            if (!response.isSuccessful) {
                throw IllegalStateException("Не удалось скачать обновление (HTTP ${response.code})")
            }
            val body = response.body ?: throw IllegalStateException("Пустой ответ сервера")
            val total = body.contentLength()
            var downloaded = 0L
            body.byteStream().use { input ->
                FileOutputStream(partial).use { output ->
                    val buf = ByteArray(64 * 1024)
                    while (true) {
                        coroutineContext.ensureActive()
                        val read = input.read(buf)
                        if (read < 0) break
                        output.write(buf, 0, read)
                        downloaded += read
                        onProgress(downloaded, total)
                    }
                    output.flush()
                }
            }
            if (downloaded <= 0L) {
                partial.delete()
                throw IllegalStateException("Файл обновления пустой")
            }
            if (!partial.renameTo(target)) {
                partial.copyTo(target, overwrite = true)
                partial.delete()
            }
        }
        target
    }

    fun startInstall(context: Context, apk: File) {
        if (!apk.exists() || apk.length() <= 0L) {
            throw IllegalStateException("APK не найден")
        }
        val authority = context.packageName + PROVIDER_SUFFIX
        val uri = FileProvider.getUriForFile(context, authority, apk)
        val intent = Intent(Intent.ACTION_VIEW).apply {
            setDataAndType(uri, "application/vnd.android.package-archive")
            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            // Некоторые прошивки требуют явного разрешения для резолверов.
            val resInfo = context.packageManager.queryIntentActivities(
                this,
                PackageManager.MATCH_DEFAULT_ONLY,
            )
            for (ri in resInfo) {
                context.grantUriPermission(
                    ri.activityInfo.packageName,
                    uri,
                    Intent.FLAG_GRANT_READ_URI_PERMISSION,
                )
            }
        }
        context.startActivity(intent)
    }

    fun formatBytes(bytes: Long): String {
        if (bytes < 0) return "…"
        if (bytes < 1024) return "$bytes Б"
        val kb = bytes / 1024.0
        if (kb < 1024) return String.format("%.0f КБ", kb)
        val mb = kb / 1024.0
        return String.format("%.1f МБ", mb)
    }
}
