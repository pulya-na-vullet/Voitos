package ru.voitos.app.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import ru.voitos.app.ui.theme.VoitosColors

/**
 * Спокойный скелетон без shimmer / мигания — просто нейтральные блоки.
 */
@Composable
fun VoitosSkeletonBlock(
    modifier: Modifier = Modifier,
    height: Dp = 14.dp,
) {
    Box(
        modifier = modifier
            .height(height)
            .clip(RoundedCornerShape(8.dp))
            .background(VoitosColors.BgSoft),
    )
}

@Composable
fun VoitosListSkeleton(
    modifier: Modifier = Modifier,
    rows: Int = 4,
) {
    Column(
        modifier = modifier
            .fillMaxWidth()
            .padding(top = 8.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        repeat(rows.coerceAtLeast(1)) {
            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .clip(RoundedCornerShape(14.dp))
                    .background(VoitosColors.Panel.copy(alpha = 0.86f))
                    .padding(14.dp),
            ) {
                VoitosSkeletonBlock(
                    modifier = Modifier.fillMaxWidth(0.72f),
                    height = 16.dp,
                )
                Spacer(modifier = Modifier.height(10.dp))
                VoitosSkeletonBlock(
                    modifier = Modifier.fillMaxWidth(0.45f),
                    height = 12.dp,
                )
                Spacer(modifier = Modifier.height(12.dp))
                VoitosSkeletonBlock(
                    modifier = Modifier.fillMaxWidth(),
                    height = 8.dp,
                )
                Spacer(modifier = Modifier.height(8.dp))
                VoitosSkeletonBlock(
                    modifier = Modifier.fillMaxWidth(0.9f),
                    height = 12.dp,
                )
            }
        }
    }
}

@Composable
fun VoitosDetailSkeleton(modifier: Modifier = Modifier) {
    Column(
        modifier = modifier.fillMaxWidth(),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .height(180.dp)
                .clip(RoundedCornerShape(14.dp))
                .background(VoitosColors.BgSoft),
        )
        VoitosSkeletonBlock(modifier = Modifier.fillMaxWidth(0.8f), height = 18.dp)
        VoitosSkeletonBlock(modifier = Modifier.fillMaxWidth(0.5f), height = 12.dp)
        VoitosSkeletonBlock(modifier = Modifier.fillMaxWidth(), height = 8.dp)
        VoitosSkeletonBlock(modifier = Modifier.fillMaxWidth(0.95f), height = 14.dp)
        VoitosSkeletonBlock(modifier = Modifier.fillMaxWidth(0.7f), height = 14.dp)
    }
}

@Composable
fun VoitosMaintenanceMessage(
    text: String = "Проводятся технические работы",
    modifier: Modifier = Modifier,
) {
    Text(
        text = text,
        color = VoitosColors.Muted,
        style = MaterialTheme.typography.bodyLarge,
        modifier = modifier.padding(vertical = 8.dp),
    )
}
