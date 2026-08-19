package ru.voitos.app.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.launch
import ru.voitos.app.api.VoitosApiClient
import ru.voitos.app.model.FeedbackTicket
import ru.voitos.app.model.ManagerFeedbackContext
import ru.voitos.app.ui.theme.VoitosColors
import ru.voitos.app.ui.theme.voitosPrimaryButtonColors
import ru.voitos.app.ui.theme.voitosSecondaryButtonColors

private enum class FeedbackFormMode {
    None,
    App,
    Manager,
}

@Composable
fun FeedbackScreen(
    client: VoitosApiClient,
    onBack: () -> Unit,
) {
    var items by remember { mutableStateOf<List<FeedbackTicket>>(emptyList()) }
    var manager by remember { mutableStateOf(ManagerFeedbackContext()) }
    var notice by remember { mutableStateOf("Все обращения рассматриваются администратором.") }
    var error by remember { mutableStateOf<String?>(null) }
    var message by remember { mutableStateOf<String?>(null) }
    var loading by remember { mutableStateOf(true) }
    var submitting by remember { mutableStateOf(false) }
    var formMode by remember { mutableStateOf(FeedbackFormMode.None) }
    var kind by remember { mutableStateOf("feedback") }
    var subject by remember { mutableStateOf("") }
    var body by remember { mutableStateOf("") }
    var score by remember { mutableIntStateOf(0) }
    val scope = rememberCoroutineScope()

    fun reload() {
        scope.launch {
            loading = true
            error = null
            try {
                val res = client.feedback()
                items = res.items
                manager = res.manager
                if (res.notice.isNotBlank()) notice = res.notice
            } catch (e: Exception) {
                error = friendlyNetworkError(e)
            } finally {
                loading = false
            }
        }
    }

    LaunchedEffect(Unit) { reload() }

    fun submit() {
        scope.launch {
            submitting = true
            error = null
            message = null
            try {
                when (formMode) {
                    FeedbackFormMode.App -> {
                        client.createFeedback(
                            kind = kind,
                            body = body,
                            subject = subject,
                        )
                    }
                    FeedbackFormMode.Manager -> {
                        if (score !in 1..5) {
                            error = "Выберите оценку от 1 до 5"
                            return@launch
                        }
                        client.createFeedback(
                            kind = "manager",
                            body = body,
                            subject = subject,
                            score = score,
                        )
                    }
                    FeedbackFormMode.None -> return@launch
                }
                message = "Обращение отправлено. Администратор рассмотрит его и ответит здесь."
                formMode = FeedbackFormMode.None
                subject = ""
                body = ""
                score = 0
                kind = "feedback"
                reload()
            } catch (e: Exception) {
                error = friendlyNetworkError(e)
            } finally {
                submitting = false
            }
        }
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(16.dp),
    ) {
        VoitosBackButton(onClick = onBack)
        Text("Обратная связь", style = MaterialTheme.typography.headlineSmall, color = VoitosColors.Text)
        Spacer(modifier = Modifier.height(6.dp))
        Text(
            notice,
            color = VoitosColors.Muted,
            style = MaterialTheme.typography.bodySmall,
        )
        Spacer(modifier = Modifier.height(12.dp))
        error?.let { Text(it, color = VoitosColors.Danger) }
        message?.let { Text(it, color = VoitosColors.Ok) }

        if (formMode == FeedbackFormMode.None) {
            Button(
                onClick = { formMode = FeedbackFormMode.App },
                modifier = Modifier.fillMaxWidth(),
                colors = voitosPrimaryButtonColors(),
            ) { Text("Баг или обратная связь") }
            Spacer(modifier = Modifier.height(8.dp))
            Button(
                onClick = { formMode = FeedbackFormMode.Manager },
                modifier = Modifier.fillMaxWidth(),
                enabled = manager.available,
                colors = voitosSecondaryButtonColors(),
            ) {
                Text(
                    if (manager.available) {
                        "ОС по менеджеру${if (manager.managerName.isNotBlank()) " · ${manager.managerName}" else ""}"
                    } else {
                        "Менеджер района не закреплён"
                    },
                )
            }
        } else {
            PanelCard {
                when (formMode) {
                    FeedbackFormMode.App -> {
                        Text("Тип обращения", style = MaterialTheme.typography.titleMedium, color = VoitosColors.Accent2)
                        Spacer(modifier = Modifier.height(8.dp))
                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            KindChip("Баг", selected = kind == "bug") { kind = "bug" }
                            KindChip("Обратная связь", selected = kind == "feedback") { kind = "feedback" }
                        }
                        Spacer(modifier = Modifier.height(10.dp))
                        FeedbackField(
                            value = subject,
                            onValueChange = { subject = it },
                            label = "Тема (необязательно)",
                            singleLine = true,
                        )
                        Spacer(modifier = Modifier.height(8.dp))
                        FeedbackField(
                            value = body,
                            onValueChange = { body = it },
                            label = if (kind == "bug") {
                                "Что произошло? Шаги и ожидание"
                            } else {
                                "Ваше предложение или комментарий"
                            },
                            minLines = 4,
                        )
                    }
                    FeedbackFormMode.Manager -> {
                        Text("Оценка менеджера", style = MaterialTheme.typography.titleMedium, color = VoitosColors.Accent2)
                        Spacer(modifier = Modifier.height(4.dp))
                        Text(
                            listOfNotNull(
                                manager.managerName.takeIf { it.isNotBlank() },
                                manager.groupName.takeIf { it.isNotBlank() },
                            ).joinToString(" · ").ifBlank { "Менеджер вашего района" },
                            color = VoitosColors.Muted,
                            style = MaterialTheme.typography.bodySmall,
                        )
                        Spacer(modifier = Modifier.height(10.dp))
                        Text("Оценка от 1 до 5", color = VoitosColors.Text)
                        Spacer(modifier = Modifier.height(6.dp))
                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            (1..5).forEach { n ->
                                KindChip("$n", selected = score == n) { score = n }
                            }
                        }
                        Spacer(modifier = Modifier.height(10.dp))
                        FeedbackField(
                            value = body,
                            onValueChange = { body = it },
                            label = "Комментарий: что улучшить или что понравилось",
                            minLines = 4,
                        )
                    }
                    FeedbackFormMode.None -> Unit
                }
                Spacer(modifier = Modifier.height(12.dp))
                Text(
                    "Все заявки рассматриваются администратором.",
                    color = VoitosColors.Muted,
                    style = MaterialTheme.typography.labelSmall,
                )
                Spacer(modifier = Modifier.height(10.dp))
                Button(
                    onClick = { submit() },
                    modifier = Modifier.fillMaxWidth(),
                    enabled = !submitting && body.trim().length >= 10,
                    colors = voitosPrimaryButtonColors(),
                ) {
                    Text(if (submitting) "Отправка…" else "Отправить")
                }
                Spacer(modifier = Modifier.height(6.dp))
                Button(
                    onClick = {
                        formMode = FeedbackFormMode.None
                        error = null
                    },
                    modifier = Modifier.fillMaxWidth(),
                    colors = voitosSecondaryButtonColors(),
                ) { Text("Отмена") }
            }
        }

        Spacer(modifier = Modifier.height(20.dp))
        Text("Мои обращения", style = MaterialTheme.typography.titleMedium, color = VoitosColors.Accent2)
        Spacer(modifier = Modifier.height(8.dp))
        if (loading && items.isEmpty()) {
            CircularProgressIndicator(
                modifier = Modifier.align(Alignment.CenterHorizontally),
                color = VoitosColors.Accent,
            )
        }
        if (!loading && items.isEmpty()) {
            Text("Пока нет обращений", color = VoitosColors.Muted)
        }
        items.forEach { ticket ->
            FeedbackTicketCard(ticket)
            Spacer(modifier = Modifier.height(10.dp))
        }
    }
}

@Composable
private fun FeedbackTicketCard(ticket: FeedbackTicket) {
    val answered = ticket.status == "answered" || ticket.adminReply.isNotBlank()
    PanelCard {
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                ticket.kindLabel.ifBlank { ticket.kind },
                style = MaterialTheme.typography.titleSmall,
                color = VoitosColors.Text,
                fontWeight = FontWeight.SemiBold,
            )
            StatusPill(
                label = ticket.statusLabel.ifBlank {
                    if (answered) "Рассмотрено" else "На рассмотрении"
                },
                answered = answered,
            )
        }
        Spacer(modifier = Modifier.height(4.dp))
        Text(
            formatFeedbackDate(ticket.createdAt),
            color = VoitosColors.Muted,
            style = MaterialTheme.typography.labelSmall,
        )
        if (ticket.subject.isNotBlank()) {
            Spacer(modifier = Modifier.height(6.dp))
            Text(ticket.subject, color = VoitosColors.Text)
        }
        if (ticket.kind == "manager" && ticket.score != null) {
            Spacer(modifier = Modifier.height(4.dp))
            Text(
                "Оценка: ${ticket.score}/5"
                    + if (ticket.managerName.isNotBlank()) " · ${ticket.managerName}" else "",
                color = VoitosColors.Muted,
                style = MaterialTheme.typography.bodySmall,
            )
        }
        Spacer(modifier = Modifier.height(6.dp))
        Text(ticket.body, color = VoitosColors.Text.copy(alpha = 0.9f), style = MaterialTheme.typography.bodyMedium)
        if (answered && ticket.adminReply.isNotBlank()) {
            Spacer(modifier = Modifier.height(10.dp))
            HorizontalDivider(color = VoitosColors.Line.copy(alpha = 0.6f))
            Spacer(modifier = Modifier.height(8.dp))
            Text("Ответ администратора", style = MaterialTheme.typography.labelLarge, color = VoitosColors.Accent2)
            ticket.adminRepliedAt?.let {
                Text(
                    formatFeedbackDate(it),
                    color = VoitosColors.Muted,
                    style = MaterialTheme.typography.labelSmall,
                )
            }
            Spacer(modifier = Modifier.height(4.dp))
            Text(ticket.adminReply, color = VoitosColors.Text)
        } else {
            Spacer(modifier = Modifier.height(8.dp))
            Text(
                "Ещё на рассмотрении — ответ появится здесь.",
                color = VoitosColors.Muted,
                style = MaterialTheme.typography.bodySmall,
            )
        }
    }
}

@Composable
private fun StatusPill(label: String, answered: Boolean) {
    Box(
        modifier = Modifier
            .clip(RoundedCornerShape(8.dp))
            .background(
                if (answered) VoitosColors.Ok.copy(alpha = 0.18f)
                else VoitosColors.Accent2.copy(alpha = 0.16f),
            )
            .padding(horizontal = 8.dp, vertical = 4.dp),
    ) {
        Text(
            label,
            color = if (answered) VoitosColors.Ok else VoitosColors.Accent2,
            style = MaterialTheme.typography.labelSmall,
        )
    }
}

@Composable
private fun KindChip(label: String, selected: Boolean, onClick: () -> Unit) {
    Box(
        modifier = Modifier
            .clip(RoundedCornerShape(10.dp))
            .border(
                width = 1.dp,
                color = if (selected) VoitosColors.Accent2 else VoitosColors.Line,
                shape = RoundedCornerShape(10.dp),
            )
            .background(
                if (selected) VoitosColors.Accent2.copy(alpha = 0.18f) else VoitosColors.BgSoft,
            )
            .clickable(onClick = onClick)
            .padding(horizontal = 12.dp, vertical = 8.dp),
    ) {
        Text(
            label,
            color = if (selected) VoitosColors.Accent2 else VoitosColors.Text,
            style = MaterialTheme.typography.labelLarge,
        )
    }
}

@Composable
private fun FeedbackField(
    value: String,
    onValueChange: (String) -> Unit,
    label: String,
    singleLine: Boolean = false,
    minLines: Int = 1,
) {
    OutlinedTextField(
        value = value,
        onValueChange = onValueChange,
        modifier = Modifier.fillMaxWidth(),
        label = { Text(label) },
        singleLine = singleLine,
        minLines = minLines,
        colors = OutlinedTextFieldDefaults.colors(
            focusedTextColor = VoitosColors.Text,
            unfocusedTextColor = VoitosColors.Text,
            focusedBorderColor = VoitosColors.Accent2,
            unfocusedBorderColor = VoitosColors.Line,
            focusedLabelColor = VoitosColors.Accent2,
            unfocusedLabelColor = VoitosColors.Muted,
            cursorColor = VoitosColors.Accent2,
        ),
    )
}

private fun formatFeedbackDate(raw: String): String {
    if (raw.isBlank()) return ""
    return raw.replace('T', ' ').take(16)
}
