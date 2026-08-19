package ru.voitos.app.ui

import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.defaultMinSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import ru.voitos.app.ui.theme.VoitosColors

/** Кнопка «Назад»: бирюзовый фон, только стрелка (или свой label для «Закрыть» и т.п.). */
@Composable
fun VoitosBackButton(
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    label: String = "←",
) {
    Button(
        onClick = onClick,
        modifier = modifier
            .padding(bottom = 4.dp)
            .defaultMinSize(minWidth = 48.dp, minHeight = 40.dp),
        colors = ButtonDefaults.buttonColors(
            containerColor = VoitosColors.Accent2,
            contentColor = VoitosColors.Text,
        ),
        contentPadding = PaddingValues(horizontal = 14.dp, vertical = 8.dp),
    ) {
        Text(label, color = VoitosColors.Text, fontSize = if (label == "←") 20.sp else 14.sp)
    }
}
