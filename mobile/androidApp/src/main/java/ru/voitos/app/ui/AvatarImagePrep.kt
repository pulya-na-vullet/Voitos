package ru.voitos.app.ui

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Matrix
import android.net.Uri
import android.util.Base64
import androidx.exifinterface.media.ExifInterface
import java.io.ByteArrayOutputStream

private const val AVATAR_SIZE = 500
private const val AVATAR_JPEG_QUALITY = 88
private const val WORK_PHOTO_MAX_EDGE = 1600
private const val WORK_PHOTO_JPEG_QUALITY = 82

/**
 * Читает фото из галереи, центральный кроп в квадрат, ресайз до 500×500 JPEG.
 */
fun prepareAvatarJpegBase64(context: Context, uri: Uri): Pair<String, String> {
    val resolver = context.contentResolver
    val orientation = resolver.openInputStream(uri)?.use { input ->
        runCatching { ExifInterface(input).getAttributeInt(ExifInterface.TAG_ORIENTATION, ExifInterface.ORIENTATION_NORMAL) }
            .getOrDefault(ExifInterface.ORIENTATION_NORMAL)
    } ?: ExifInterface.ORIENTATION_NORMAL

    val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
    resolver.openInputStream(uri)?.use { BitmapFactory.decodeStream(it, null, bounds) }
    val srcW = bounds.outWidth
    val srcH = bounds.outHeight
    if (srcW <= 0 || srcH <= 0) {
        throw IllegalStateException("Не удалось прочитать изображение")
    }

    val sample = largestPowerOfTwoSample(srcW, srcH, target = AVATAR_SIZE * 2)
    val opts = BitmapFactory.Options().apply { inSampleSize = sample }
    val decoded = resolver.openInputStream(uri)?.use { BitmapFactory.decodeStream(it, null, opts) }
        ?: throw IllegalStateException("Не удалось декодировать изображение")

    val oriented = applyExifOrientation(decoded, orientation)
    if (oriented !== decoded) decoded.recycle()

    val square = centerCropSquare(oriented)
    if (square !== oriented) oriented.recycle()

    val sized = if (square.width == AVATAR_SIZE && square.height == AVATAR_SIZE) {
        square
    } else {
        Bitmap.createScaledBitmap(square, AVATAR_SIZE, AVATAR_SIZE, true).also {
            if (it !== square) square.recycle()
        }
    }

    val out = ByteArrayOutputStream()
    if (!sized.compress(Bitmap.CompressFormat.JPEG, AVATAR_JPEG_QUALITY, out)) {
        sized.recycle()
        throw IllegalStateException("Не удалось сжать изображение")
    }
    sized.recycle()
    val bytes = out.toByteArray()
    if (bytes.isEmpty()) throw IllegalStateException("Пустое изображение")
    return Base64.encodeToString(bytes, Base64.NO_WRAP) to "avatar.jpg"
}

/**
 * Сжимает фото заявки (длинная сторона ≤ 1600) в JPEG — чтобы большие снимки
 * не упирались в timeout при загрузке.
 */
fun prepareWorkPhotoJpegBase64(context: Context, uri: Uri): Pair<String, String> {
    val resolver = context.contentResolver
    val orientation = resolver.openInputStream(uri)?.use { input ->
        runCatching {
            ExifInterface(input).getAttributeInt(
                ExifInterface.TAG_ORIENTATION,
                ExifInterface.ORIENTATION_NORMAL,
            )
        }.getOrDefault(ExifInterface.ORIENTATION_NORMAL)
    } ?: ExifInterface.ORIENTATION_NORMAL

    val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
    resolver.openInputStream(uri)?.use { BitmapFactory.decodeStream(it, null, bounds) }
    val srcW = bounds.outWidth
    val srcH = bounds.outHeight
    if (srcW <= 0 || srcH <= 0) {
        throw IllegalStateException("Не удалось прочитать изображение")
    }

    val sample = largestPowerOfTwoSample(srcW, srcH, target = WORK_PHOTO_MAX_EDGE)
    val opts = BitmapFactory.Options().apply { inSampleSize = sample }
    val decoded = resolver.openInputStream(uri)?.use { BitmapFactory.decodeStream(it, null, opts) }
        ?: throw IllegalStateException("Не удалось декодировать изображение")

    val oriented = applyExifOrientation(decoded, orientation)
    if (oriented !== decoded) decoded.recycle()

    val maxEdge = maxOf(oriented.width, oriented.height)
    val sized = if (maxEdge <= WORK_PHOTO_MAX_EDGE) {
        oriented
    } else {
        val scale = WORK_PHOTO_MAX_EDGE.toFloat() / maxEdge.toFloat()
        val w = (oriented.width * scale).toInt().coerceAtLeast(1)
        val h = (oriented.height * scale).toInt().coerceAtLeast(1)
        Bitmap.createScaledBitmap(oriented, w, h, true).also {
            if (it !== oriented) oriented.recycle()
        }
    }

    val out = ByteArrayOutputStream()
    if (!sized.compress(Bitmap.CompressFormat.JPEG, WORK_PHOTO_JPEG_QUALITY, out)) {
        sized.recycle()
        throw IllegalStateException("Не удалось сжать изображение")
    }
    sized.recycle()
    val bytes = out.toByteArray()
    if (bytes.isEmpty()) throw IllegalStateException("Пустое изображение")
    return Base64.encodeToString(bytes, Base64.NO_WRAP) to "photo.jpg"
}

private fun largestPowerOfTwoSample(width: Int, height: Int, target: Int): Int {
    var sample = 1
    while (width / (sample * 2) >= target && height / (sample * 2) >= target) {
        sample *= 2
    }
    return sample.coerceAtLeast(1)
}

private fun centerCropSquare(src: Bitmap): Bitmap {
    val side = minOf(src.width, src.height)
    if (side <= 0) throw IllegalStateException("Некорректный размер фото")
    if (src.width == side && src.height == side) return src
    val left = (src.width - side) / 2
    val top = (src.height - side) / 2
    return Bitmap.createBitmap(src, left, top, side, side)
}

private fun applyExifOrientation(src: Bitmap, orientation: Int): Bitmap {
    val matrix = Matrix()
    when (orientation) {
        ExifInterface.ORIENTATION_FLIP_HORIZONTAL -> matrix.setScale(-1f, 1f)
        ExifInterface.ORIENTATION_ROTATE_180 -> matrix.setRotate(180f)
        ExifInterface.ORIENTATION_FLIP_VERTICAL -> {
            matrix.setRotate(180f)
            matrix.postScale(-1f, 1f)
        }
        ExifInterface.ORIENTATION_TRANSPOSE -> {
            matrix.setRotate(90f)
            matrix.postScale(-1f, 1f)
        }
        ExifInterface.ORIENTATION_ROTATE_90 -> matrix.setRotate(90f)
        ExifInterface.ORIENTATION_TRANSVERSE -> {
            matrix.setRotate(-90f)
            matrix.postScale(-1f, 1f)
        }
        ExifInterface.ORIENTATION_ROTATE_270 -> matrix.setRotate(-90f)
        else -> return src
    }
    return try {
        Bitmap.createBitmap(src, 0, 0, src.width, src.height, matrix, true)
    } catch (_: Exception) {
        src
    }
}
