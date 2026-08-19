package ru.voitos.app.ui

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import ru.voitos.app.api.VoitosApiClient
import ru.voitos.app.model.ExecutorMe
import ru.voitos.app.model.Me
import ru.voitos.app.model.SubscriptionInfo
import ru.voitos.app.ui.theme.VoitosColors

@Composable
fun CabinetScreen(
    client: VoitosApiClient,
    onOpenSubscription: () -> Unit,
    onOpenOnboarding: () -> Unit,
    onRegisterExecutor: () -> Unit,
    onOpenExecutorOffers: () -> Unit,
    onLogout: () -> Unit,
) {
    var me by remember { mutableStateOf<Me?>(null) }
    var sub by remember { mutableStateOf<SubscriptionInfo?>(null) }
    var executor by remember { mutableStateOf<ExecutorMe?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    var loading by remember { mutableStateOf(true) }

    LaunchedEffect(Unit) {
        loading = true
        error = null
        val errors = mutableListOf<String>()
        me = runCatching { client.me() }.getOrElse {
            errors += friendlyNetworkError(it, "Не удалось загрузить профиль")
            null
        }
        sub = runCatching { client.subscription() }.getOrNull()
        executor = runCatching { client.executorMe() }.getOrElse {
            // 404 / старый бэкенд → не исполнитель; прочие ошибки не валят весь кабинет
            val msg = it.message.orEmpty()
            if ("404" in msg || "me/executor" in msg.lowercase()) {
                ExecutorMe(isExecutor = false)
            } else {
                errors += "Исполнитель: ${friendlyNetworkError(it)}"
                null
            }
        }
        error = errors.firstOrNull()
        loading = false
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(16.dp),
    ) {
        Text("Личный кабинет", style = MaterialTheme.typography.headlineSmall, color = VoitosColors.Text)
        Spacer(modifier = Modifier.height(8.dp))
        VpnDebugBanner()
        Spacer(modifier = Modifier.height(8.dp))
        if (loading) {
            CircularProgressIndicator(
                modifier = Modifier.align(Alignment.CenterHorizontally),
                color = VoitosColors.Accent,
            )
        }
        error?.let { Text(it, color = VoitosColors.Danger) }

        PanelCard {
            Text("Профиль", style = MaterialTheme.typography.titleMedium, color = VoitosColors.Accent2)
            Spacer(modifier = Modifier.height(8.dp))
            val m = me
            if (m != null) {
                InfoLine("Имя", m.realName.ifBlank { "—" })
                InfoLine("Телефон", m.phone.ifBlank { "—" })
                InfoLine("Населённый пункт", m.locality.ifBlank { "—" })
                InfoLine("Адрес", m.address.ifBlank { "—" })
                InfoLine("Статус", m.profileStatus.ifBlank { "—" })
            } else if (!loading) {
                Text("Не удалось загрузить профиль", color = VoitosColors.Muted)
            }
        }

        Spacer(modifier = Modifier.height(12.dp))

        PanelCard {
            Text("Подписка", style = MaterialTheme.typography.titleMedium, color = VoitosColors.Accent2)
            Spacer(modifier = Modifier.height(8.dp))
            val s = sub
            if (s != null) {
                InfoLine("Статус", s.label.ifBlank { s.state })
                InfoLine("Действует до", formatUntil(s.subscriptionUntil))
                s.graceUntil?.let { InfoLine("Grace до", formatUntil(it)) }
                if (s.priceRub > 0) {
                    InfoLine("Цена", "${s.priceRub} ₽/мес")
                }
            } else if (!loading) {
                Text("Нет данных о подписке", color = VoitosColors.Muted)
            }
        }

        Spacer(modifier = Modifier.height(12.dp))

        PanelCard {
            Text("Исполнитель", style = MaterialTheme.typography.titleMedium, color = VoitosColors.Accent2)
            Spacer(modifier = Modifier.height(8.dp))
            val ex = executor
            if (ex == null && !loading) {
                Text("Нет данных", color = VoitosColors.Muted)
            } else if (ex != null) {
                if (ex.isExecutor) {
                    Text("Да, вы зарегистрированы как исполнитель", color = VoitosColors.Ok)
                    ex.profiles.forEach { p ->
                        Spacer(modifier = Modifier.height(6.dp))
                        InfoLine("Роль", p.roleName)
                        InfoLine("Статус", p.statusLabel.ifBlank { p.status })
                        if (p.locality.isNotBlank()) InfoLine("НП", p.locality)
                    }
                    if (ex.openOffersCount > 0) {
                        Spacer(modifier = Modifier.height(6.dp))
                        Text(
                            "Открытых заявок для вас: ${ex.openOffersCount}",
                            color = VoitosColors.Accent2,
                        )
                    }
                } else {
                    Text("Нет — вы пока не исполнитель", color = VoitosColors.Muted)
                }
            }
        }

        Spacer(modifier = Modifier.height(20.dp))

        Button(
            onClick = onOpenSubscription,
            modifier = Modifier.fillMaxWidth(),
            colors = ButtonDefaults.buttonColors(
                containerColor = VoitosColors.Accent,
                contentColor = VoitosColors.OnAccent,
            ),
        ) { Text("Подписка") }

        Spacer(modifier = Modifier.height(8.dp))

        Button(
            onClick = onOpenOnboarding,
            modifier = Modifier.fillMaxWidth(),
            colors = ButtonDefaults.buttonColors(
                containerColor = VoitosColors.BgSoft,
                contentColor = VoitosColors.Text,
            ),
        ) { Text("Обучение") }

        Spacer(modifier = Modifier.height(8.dp))

        Button(
            onClick = onRegisterExecutor,
            modifier = Modifier.fillMaxWidth(),
            colors = ButtonDefaults.buttonColors(
                containerColor = VoitosColors.BgSoft,
                contentColor = VoitosColors.Text,
            ),
        ) { Text("Зарегистрироваться исполнителем") }

        if (executor?.isExecutor == true) {
            Spacer(modifier = Modifier.height(8.dp))
            Button(
                onClick = onOpenExecutorOffers,
                modifier = Modifier.fillMaxWidth(),
                colors = ButtonDefaults.buttonColors(
                    containerColor = VoitosColors.Accent2,
                    contentColor = VoitosColors.OnAccent,
                ),
            ) {
                val n = executor?.openOffersCount ?: 0
                Text(if (n > 0) "Мои заявки мастера ($n)" else "Мои заявки мастера")
            }
        }

        Spacer(modifier = Modifier.height(8.dp))

        TextButton(
            onClick = onLogout,
            modifier = Modifier.fillMaxWidth(),
        ) {
            Text("Выйти", color = VoitosColors.Danger)
        }
    }
}

@Composable
private fun InfoLine(label: String, value: String) {
    Column(modifier = Modifier.padding(vertical = 4.dp)) {
        Text(label, style = MaterialTheme.typography.labelSmall, color = VoitosColors.Muted)
        Text(value, style = MaterialTheme.typography.bodyMedium, color = VoitosColors.Text)
    }
}

private fun formatUntil(raw: String?): String {
    if (raw.isNullOrBlank()) return "—"
    val date = raw.take(10)
    return if (date.length == 10 && date[4] == '-') {
        val p = date.split("-")
        "${p[2]}.${p[1]}.${p[0]}"
    } else {
        raw
    }
}
