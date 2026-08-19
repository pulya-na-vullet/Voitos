package ru.voitos.app.ui.theme

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.Typography
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.sp

/** Цвета как в static/panel/app.css */
object VoitosColors {
    val Bg = Color(0xFF0F1419)
    val BgSoft = Color(0xFF171E26)
    val Panel = Color(0xFF1C2430)
    val Line = Color(0x14FFFFFF)
    val Text = Color(0xFFE8EEF6)
    val Muted = Color(0xFF8B9BB0)
    val Accent = Color(0xFF3D9CFD)
    val Accent2 = Color(0xFF2DD4BF)
    val Danger = Color(0xFFF07178)
    val Ok = Color(0xFF7FD99A)
    val Warn = Color(0xFFE6C07B)
    val OnAccent = Color(0xFF041018)
}

private val VoitosDarkScheme = darkColorScheme(
    primary = VoitosColors.Accent,
    onPrimary = VoitosColors.OnAccent,
    secondary = VoitosColors.Accent2,
    onSecondary = VoitosColors.OnAccent,
    background = VoitosColors.Bg,
    onBackground = VoitosColors.Text,
    surface = VoitosColors.Panel,
    onSurface = VoitosColors.Text,
    surfaceVariant = VoitosColors.BgSoft,
    onSurfaceVariant = VoitosColors.Muted,
    outline = VoitosColors.Line,
    error = VoitosColors.Danger,
    onError = VoitosColors.Text,
)

private val VoitosTypography = Typography(
    headlineLarge = TextStyle(
        fontFamily = FontFamily.SansSerif,
        fontWeight = FontWeight.Bold,
        fontSize = 28.sp,
        letterSpacing = 0.3.sp,
        color = VoitosColors.Text,
    ),
    headlineSmall = TextStyle(
        fontFamily = FontFamily.SansSerif,
        fontWeight = FontWeight.SemiBold,
        fontSize = 22.sp,
        color = VoitosColors.Text,
    ),
    titleLarge = TextStyle(
        fontFamily = FontFamily.SansSerif,
        fontWeight = FontWeight.SemiBold,
        fontSize = 18.sp,
        color = VoitosColors.Text,
    ),
    titleMedium = TextStyle(
        fontFamily = FontFamily.SansSerif,
        fontWeight = FontWeight.Medium,
        fontSize = 16.sp,
        color = VoitosColors.Text,
    ),
    bodyMedium = TextStyle(
        fontFamily = FontFamily.SansSerif,
        fontWeight = FontWeight.Normal,
        fontSize = 14.sp,
        color = VoitosColors.Text,
    ),
    bodySmall = TextStyle(
        fontFamily = FontFamily.SansSerif,
        fontWeight = FontWeight.Normal,
        fontSize = 12.sp,
        color = VoitosColors.Muted,
    ),
    labelSmall = TextStyle(
        fontFamily = FontFamily.SansSerif,
        fontWeight = FontWeight.Medium,
        fontSize = 11.sp,
        color = VoitosColors.Muted,
    ),
)

@Composable
fun VoitosTheme(content: @Composable () -> Unit) {
    // Панель всегда тёмная — приложение тоже, независимо от системы.
    @Suppress("UNUSED_VARIABLE")
    val ignoreSystem = isSystemInDarkTheme()
    MaterialTheme(
        colorScheme = VoitosDarkScheme,
        typography = VoitosTypography,
        content = content,
    )
}
