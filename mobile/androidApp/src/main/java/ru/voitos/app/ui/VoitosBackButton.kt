package ru.voitos.app.ui

import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import ru.voitos.app.ui.theme.VoitosColors
import ru.voitos.app.ui.theme.voitosSecondaryButtonColors

/** Кнопка «Назад» вместо TextButton-гиперссылки. */
@Composable
fun VoitosBackButton(
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    label: String = "← Назад",
) {
    Button(
        onClick = onClick,
        modifier = modifier.padding(bottom = 4.dp),
        colors = voitosSecondaryButtonColors(),
        contentPadding = PaddingValues(horizontal = 16.dp, vertical = 8.dp),
    ) {
        Text(label, color = VoitosColors.Text)
    }
}
