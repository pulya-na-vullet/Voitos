package ru.voitos.app.ui

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.launch
import ru.voitos.app.api.VoitosApiClient
import ru.voitos.app.model.ApiException
import ru.voitos.app.model.Ballot
import ru.voitos.app.model.BallotOption
import ru.voitos.app.ui.theme.VoitosColors
import ru.voitos.app.ui.theme.voitosPrimaryButtonColors
import ru.voitos.app.ui.theme.voitosSecondaryButtonColors

/**
 * Голосования по пожеланиям двора.
 *
 * Тот же раунд, что бот присылает в MAX: топ-3 темы группы, голос за вариант
 * или «пока не делаем». Голос из приложения и из бота — один и тот же голос.
 */
@Composable
fun BallotsScreen(
    client: VoitosApiClient,
    onBack: () -> Unit,
) {
    BackHandler(enabled = true) { onBack() }
    var items by remember { mutableStateOf<List<Ballot>>(emptyList()) }
    var error by remember { mutableStateOf<String?>(null) }
    var message by remember { mutableStateOf<String?>(null) }
    var loading by remember { mutableStateOf(true) }
    var votingFor by remember { mutableStateOf<Int?>(null) }
    val scope = rememberCoroutineScope()

    fun reload(showLoader: Boolean = true) {
        scope.launch {
            if (showLoader) loading = true
            runCatching { client.ballots() }
                .onSuccess {
                    items = it.items
                    error = null
                }
                .onFailure { error = friendlyNetworkError(it) }
            loading = false
        }
    }

    LaunchedEffect(Unit) { reload() }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(16.dp),
    ) {
        VoitosBackButton(onClick = onBack)
        Text(
            "Голосования",
            style = MaterialTheme.typography.headlineSmall,
            color = VoitosColors.Text,
        )
        Text(
            "Раз в две недели выбираем, что делать во дворе. Тот же голос, что и в Max.",
            color = VoitosColors.Muted,
            style = MaterialTheme.typography.bodySmall,
        )
        Spacer(modifier = Modifier.height(10.dp))
        error?.let { NetworkErrorText(it) }
        message?.let { Text(it, color = VoitosColors.Ok) }

        when {
            loading -> CircularProgressIndicator(
                modifier = Modifier.align(Alignment.CenterHorizontally),
                color = VoitosColors.Accent,
            )
            items.isEmpty() -> Text(
                "Активных голосований пока нет.",
                color = VoitosColors.Muted,
            )
            else -> items.forEach { ballot ->
                BallotCard(
                    ballot = ballot,
                    busy = votingFor == ballot.id,
                    onVote = { topic ->
                        scope.launch {
                            votingFor = ballot.id
                            error = null
                            message = null
                            try {
                                val result = client.ballotVote(ballot.id, topic)
                                val vote = result.vote
                                message = if (vote != null && vote.willDo) {
                                    val label = ballot.options
                                        .firstOrNull { it.topic == vote.topic }
                                        ?.label
                                        .orEmpty()
                                    "Голос учтён: ${label.ifBlank { vote.topic }}"
                                } else {
                                    "Принято: пока не делаем"
                                }
                                reload(showLoader = false)
                            } catch (e: ApiException) {
                                error = e.message
                            } catch (e: Exception) {
                                error = friendlyNetworkError(e)
                            } finally {
                                votingFor = null
                            }
                        }
                    },
                )
                Spacer(modifier = Modifier.height(12.dp))
            }
        }
    }
}

@Composable
private fun BallotCard(
    ballot: Ballot,
    busy: Boolean,
    onVote: (String?) -> Unit,
) {
    val myVote = ballot.myVote
    val myTopic = myVote?.topic.orEmpty()
    val refused = myVote != null && !myVote.willDo

    Column(
        modifier = Modifier
            .fillMaxWidth()
            .padding(12.dp),
    ) {
        Text(
            ballot.groupName.ifBlank { "Двор" },
            style = MaterialTheme.typography.titleMedium,
            color = VoitosColors.Accent2,
        )
        Text(
            if (ballot.status == "voting") {
                "Идёт голосование"
            } else {
                "Голосование завершено"
            },
            color = VoitosColors.Muted,
            style = MaterialTheme.typography.bodySmall,
        )
        val endsAt = ballot.votingEndsAt.take(10)
        if (endsAt.isNotBlank()) {
            Text(
                "До $endsAt",
                color = VoitosColors.Muted,
                style = MaterialTheme.typography.bodySmall,
            )
        }

        Spacer(modifier = Modifier.height(10.dp))

        ballot.options.forEach { option ->
            BallotOptionRow(
                option = option,
                selected = option.topic == myTopic,
                enabled = !busy && ballot.status == "voting",
                onClick = { onVote(option.topic) },
            )
            Spacer(modifier = Modifier.height(6.dp))
        }

        HorizontalDivider(color = VoitosColors.Line)

        val myTopicLabel = ballot.options
            .firstOrNull { it.topic == myTopic }
            ?.label
            .orEmpty()
        when {
            myTopicLabel.isNotBlank() -> Text(
                "Ваш голос: «$myTopicLabel»",
                color = VoitosColors.Ok,
                style = MaterialTheme.typography.bodySmall,
            )
            refused -> Text(
                "Ваш ответ: пока не делаем",
                color = VoitosColors.Muted,
                style = MaterialTheme.typography.bodySmall,
            )
            else -> Text(
                "Вы ещё не голосовали",
                color = VoitosColors.Muted,
                style = MaterialTheme.typography.bodySmall,
            )
        }

        if (ballot.status == "voting" && !refused) {
            TextButton(
                onClick = { onVote(null) },
                enabled = !busy,
            ) {
                Text("Пока не делаем", color = VoitosColors.Muted)
            }
        }
    }
}

@Composable
private fun BallotOptionRow(
    option: BallotOption,
    selected: Boolean,
    enabled: Boolean,
    onClick: () -> Unit,
) {
    Button(
        onClick = onClick,
        enabled = enabled,
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(12.dp),
        colors = if (selected) voitosPrimaryButtonColors() else voitosSecondaryButtonColors(),
    ) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.SpaceBetween,
        ) {
            Text(
                text = if (selected) "✓ ${option.label}" else option.label,
                fontWeight = if (selected) FontWeight.Bold else FontWeight.Normal,
            )
            if (option.wishCount > 0) {
                Spacer(modifier = Modifier.width(8.dp))
                Text(
                    "пожеланий: ${option.wishCount}",
                    style = MaterialTheme.typography.labelSmall,
                    color = VoitosColors.Muted,
                )
            }
        }
    }
}
