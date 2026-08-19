package ru.voitos.app.debug

import android.content.Context
import android.os.Build
import java.io.File
import java.io.PrintWriter
import java.io.StringWriter
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * Без logcat на Huawei: пишем последний краш в filesDir и показываем при следующем запуске.
 */
object CrashFileLogger {
    private const val FILE_NAME = "voitos_last_crash.txt"

    fun install(context: Context) {
        val app = context.applicationContext
        val previous = Thread.getDefaultUncaughtExceptionHandler()
        Thread.setDefaultUncaughtExceptionHandler { thread, throwable ->
            runCatching { writeCrash(app, thread, throwable) }
            previous?.uncaughtException(thread, throwable)
        }
    }

    fun consumeLastCrash(context: Context): String? {
        val file = File(context.filesDir, FILE_NAME)
        if (!file.exists()) return null
        return runCatching {
            val text = file.readText()
            file.delete()
            text
        }.getOrNull()?.takeIf { it.isNotBlank() }
    }

    fun peekPath(context: Context): String =
        File(context.filesDir, FILE_NAME).absolutePath

    private fun writeCrash(context: Context, thread: Thread, throwable: Throwable) {
        val sw = StringWriter()
        throwable.printStackTrace(PrintWriter(sw))
        val stamp = SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.US).format(Date())
        val body = buildString {
            appendLine("Voitos crash $stamp")
            appendLine("device=${Build.MANUFACTURER} ${Build.MODEL}")
            appendLine("sdk=${Build.VERSION.SDK_INT} release=${Build.VERSION.RELEASE}")
            appendLine("thread=${thread.name}")
            appendLine()
            append(sw.toString())
        }
        File(context.filesDir, FILE_NAME).writeText(body)
        // Дубль в external files (если доступно) — удобнее вытащить через файловый менеджер.
        runCatching {
            val ext = context.getExternalFilesDir(null) ?: return@runCatching
            File(ext, FILE_NAME).writeText(body)
        }
    }
}
