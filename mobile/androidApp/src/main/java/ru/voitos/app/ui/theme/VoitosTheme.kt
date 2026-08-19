package ru.voitos.app.ui.theme

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.TextFieldColors
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.Typography
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.sp
import androidx.compose.material3.LocalContentColor
import androidx.compose.material3.LocalTextStyle

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
    tertiary = VoitosColors.Accent2,
    onTertiary = VoitosColors.OnAccent,
    background = VoitosColors.Bg,
    onBackground = VoitosColors.Text,
    surface = VoitosColors.Panel,
    onSurface = VoitosColors.Text,
    surfaceVariant = VoitosColors.BgSoft,
    onSurfaceVariant = VoitosColors.Muted,
    surfaceTint = VoitosColors.Accent,
    inverseSurface = VoitosColors.Text,
    inverseOnSurface = VoitosColors.Bg,
    outline = Color(0xFF3A4656),
    outlineVariant = Color(0xFF2A3442),
    error = VoitosColors.Danger,
    onError = VoitosColors.Text,
    errorContainer = Color(0xFF4A1F24),
    onErrorContainer = VoitosColors.Text,
    primaryContainer = Color(0xFF163A5F),
    onPrimaryContainer = VoitosColors.Text,
    secondaryContainer = Color(0xFF164A45),
    onSecondaryContainer = VoitosColors.Text,
    surfaceContainerLowest = VoitosColors.Bg,
    surfaceContainerLow = VoitosColors.BgSoft,
    surfaceContainer = VoitosColors.Panel,
    surfaceContainerHigh = Color(0xFF222B38),
    surfaceContainerHighest = Color(0xFF2A3442),
)

private val baseText = TextStyle(
    fontFamily = FontFamily.SansSerif,
    color = VoitosColors.Text,
)

private val VoitosTypography = Typography(
    headlineLarge = baseText.copy(fontWeight = FontWeight.Bold, fontSize = 28.sp, letterSpacing = 0.3.sp),
    headlineMedium = baseText.copy(fontWeight = FontWeight.SemiBold, fontSize = 24.sp),
    headlineSmall = baseText.copy(fontWeight = FontWeight.SemiBold, fontSize = 22.sp),
    titleLarge = baseText.copy(fontWeight = FontWeight.SemiBold, fontSize = 18.sp),
    titleMedium = baseText.copy(fontWeight = FontWeight.Medium, fontSize = 16.sp),
    titleSmall = baseText.copy(fontWeight = FontWeight.Medium, fontSize = 14.sp),
    bodyLarge = baseText.copy(fontWeight = FontWeight.Normal, fontSize = 16.sp),
    bodyMedium = baseText.copy(fontWeight = FontWeight.Normal, fontSize = 14.sp),
    bodySmall = baseText.copy(fontWeight = FontWeight.Normal, fontSize = 12.sp, color = VoitosColors.Muted),
    labelLarge = baseText.copy(fontWeight = FontWeight.Medium, fontSize = 14.sp),
    labelMedium = baseText.copy(fontWeight = FontWeight.Medium, fontSize = 12.sp, color = VoitosColors.Muted),
    labelSmall = baseText.copy(fontWeight = FontWeight.Medium, fontSize = 11.sp, color = VoitosColors.Muted),
)

@Composable
fun voitosOutlinedFieldColors(): TextFieldColors = OutlinedTextFieldDefaults.colors(
    focusedTextColor = VoitosColors.Text,
    unfocusedTextColor = VoitosColors.Text,
    disabledTextColor = VoitosColors.Muted,
    focusedContainerColor = VoitosColors.BgSoft,
    unfocusedContainerColor = VoitosColors.BgSoft,
    disabledContainerColor = VoitosColors.BgSoft,
    cursorColor = VoitosColors.Accent2,
    focusedBorderColor = VoitosColors.Accent,
    unfocusedBorderColor = Color(0xFF3A4656),
    focusedLabelColor = VoitosColors.Accent2,
    unfocusedLabelColor = VoitosColors.Muted,
    focusedPlaceholderColor = VoitosColors.Muted,
    unfocusedPlaceholderColor = VoitosColors.Muted,
    focusedSupportingTextColor = VoitosColors.Muted,
    unfocusedSupportingTextColor = VoitosColors.Muted,
)

@Composable
fun VoitosTheme(content: @Composable () -> Unit) {
    @Suppress("UNUSED_VARIABLE")
    val ignoreSystem = isSystemInDarkTheme()
    MaterialTheme(
        colorScheme = VoitosDarkScheme,
        typography = VoitosTypography,
    ) {
        // Гарантируем светлый текст даже если вложенные Surface/Card сбросили контраст.
        CompositionLocalProvider(
            LocalContentColor provides VoitosColors.Text,
            LocalTextStyle provides MaterialTheme.typography.bodyMedium.copy(color = VoitosColors.Text),
        ) {
            content()
        }
    }
}
