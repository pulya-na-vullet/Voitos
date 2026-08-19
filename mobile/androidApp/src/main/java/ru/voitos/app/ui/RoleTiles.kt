package ru.voitos.app.ui

import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.ColorFilter
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import ru.voitos.app.model.ExecutorRole
import ru.voitos.app.ui.theme.VoitosColors

@Composable
fun RoleTileGrid(
    roles: List<ExecutorRole>,
    selectedId: Int?,
    onSelect: (Int) -> Unit,
) {
    val columns = 2
    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
        roles.chunked(columns).forEach { rowRoles ->
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                rowRoles.forEach { role ->
                    RoleTileButton(
                        role = role,
                        selected = selectedId == role.id,
                        onClick = { onSelect(role.id) },
                        modifier = Modifier.weight(1f),
                    )
                }
                if (rowRoles.size < columns) {
                    Spacer(modifier = Modifier.weight(1f))
                }
            }
        }
    }
}

@Composable
fun RoleTileButton(
    role: ExecutorRole,
    selected: Boolean,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val shape = RoundedCornerShape(14.dp)
    val bg = if (selected) VoitosColors.Accent.copy(alpha = 0.28f) else VoitosColors.Panel.copy(alpha = 0.92f)
    val tint = if (selected) VoitosColors.Accent2 else VoitosColors.Text
    Column(
        modifier = modifier
            .heightIn(min = 112.dp)
            .border(
                width = if (selected) 2.dp else 1.dp,
                color = if (selected) VoitosColors.Accent2 else VoitosColors.Line,
                shape = shape,
            )
            .background(bg, shape)
            .clickable(onClick = onClick)
            .padding(horizontal = 10.dp, vertical = 14.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
    ) {
        Image(
            painter = painterResource(roleIconRes(role)),
            contentDescription = role.name,
            modifier = Modifier.size(36.dp),
            colorFilter = ColorFilter.tint(tint),
        )
        Spacer(modifier = Modifier.height(8.dp))
        Text(
            text = role.name,
            color = tint,
            style = MaterialTheme.typography.bodyMedium,
            textAlign = TextAlign.Center,
            maxLines = 2,
            overflow = TextOverflow.Ellipsis,
            modifier = Modifier.fillMaxWidth(),
        )
    }
}
