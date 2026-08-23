package ru.voitos.app.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.launch
import ru.voitos.app.api.VoitosApiClient
import ru.voitos.app.model.ExecutorJobBrief
import ru.voitos.app.model.ExecutorOfferBrief
import ru.voitos.app.model.ExecutorProfileBrief
import ru.voitos.app.ui.theme.VoitosColors
import ru.voitos.app.ui.theme.voitosOutlinedFieldColors
import ru.voitos.app.ui.theme.voitosPrimaryButtonColors
import ru.voitos.app.ui.theme.voitosSecondaryButtonColors

@Composable
fun ExecutorOffersScreen(
    client: VoitosApiClient,
    onBack: (() -> Unit)? = null,
) {
    var profiles by remember { mutableStateOf<List<ExecutorProfileBrief>>(emptyList()) }
    var offers by remember { mutableStateOf<List<ExecutorOfferBrief>>(emptyList()) }
    var jobs by remember { mutableStateOf<List<ExecutorJobBrief>>(emptyList()) }
    var error by remember { mutableStateOf<String?>(null) }
    var message by remember { mutableStateOf<String?>(null) }
    var loading by remember { mutableStateOf(true) }
    var busyId by remember { mutableStateOf<Int?>(null) }
    var slotDrafts by remember { mutableStateOf<Map<Int, String>>(emptyMap()) }
    val scope = rememberCoroutineScope()

    fun offersFor(profile: ExecutorProfileBrief): List<ExecutorOfferBrief> =
        offers.filter { offer ->
            when {
                profile.roleId > 0 && offer.roleId > 0 -> offer.roleId == profile.roleId
                else -> offer.roleName.equals(profile.roleName, ignoreCase = true)
            }
        }

    fun reload() {
        scope.launch {
            loading = true
            error = null
            try {
                profiles = client.executorMe().profiles
                offers = client.executorOffers().items
                jobs = runCatching { client.executorJobs().items }.getOrDefault(emptyList())
            } catch (e: Exception) {
                error = friendlyNetworkError(e)
            } finally {
                loading = false
            }
        }
    }

    LaunchedEffect(Unit) { reload() }

    Column(modifier = Modifier.fillMaxSize().padding(16.dp)) {
        if (onBack != null) {
            VoitosBackButton(onClick = onBack)
        }
        Text("Работа", style = MaterialTheme.typography.headlineSmall, color = VoitosColors.Text)
        Spacer(modifier = Modifier.height(8.dp))
        error?.let { NetworkErrorText(it) }
        message?.let { Text(it, color = VoitosColors.Ok) }
        if (loading) {
            VoitosListSkeleton(rows = 3)
        } else {
            LazyColumn(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                if (jobs.isNotEmpty()) {
                    item {
                        Text(
                            "Согласование времени",
                            style = MaterialTheme.typography.titleMedium,
                            color = VoitosColors.Accent2,
                        )
                    }
                    items(jobs, key = { "job-${it.id}" }) { job ->
                        PanelCard {
                            Text(
                                "#${job.id} · ${job.roleName}",
                                style = MaterialTheme.typography.titleMedium,
                                color = VoitosColors.Text,
                            )
                            Text(job.clientName, color = VoitosColors.Muted)
                            if (job.clientPhone.isNotBlank()) {
                                Text("Тел. клиента: ${job.clientPhone}", color = VoitosColors.Text)
                            }
                            if (job.acceptsAtHome) {
                                Text(
                                    "Приём на дому: ${job.masterAddress.ifBlank { "адрес уточните" }}",
                                    color = VoitosColors.Muted,
                                )
                            } else {
                                if (job.clientLocality.isNotBlank() || job.clientAddress.isNotBlank()) {
                                    Text(
                                        listOf(job.clientLocality, job.clientAddress)
                                            .filter { it.isNotBlank() }
                                            .joinToString(", "),
                                        color = VoitosColors.Muted,
                                    )
                                }
                            }
                            Text(job.description, color = VoitosColors.Text)
                            Spacer(modifier = Modifier.height(8.dp))
                            if (job.proposedSlots.isNotEmpty()) {
                                Text(
                                    "Окна отправлены клиенту:\n" +
                                        job.proposedSlots.mapIndexed { i, s -> "${i + 1}. $s" }
                                            .joinToString("\n"),
                                    color = VoitosColors.Ok,
                                )
                            } else {
                                Text(
                                    "Предложите окна — каждое с новой строки, затем отправьте.",
                                    color = VoitosColors.Muted,
                                    style = MaterialTheme.typography.bodySmall,
                                )
                                OutlinedTextField(
                                    value = slotDrafts[job.id].orEmpty(),
                                    onValueChange = { text ->
                                        slotDrafts = slotDrafts + (job.id to text)
                                    },
                                    modifier = Modifier.fillMaxWidth(),
                                    minLines = 3,
                                    placeholder = { Text("15.03 10:00–12:00\n15.03 14:00–16:00") },
                                    colors = voitosOutlinedFieldColors(),
                                )
                                Spacer(modifier = Modifier.height(8.dp))
                                Button(
                                    onClick = {
                                        val raw = slotDrafts[job.id].orEmpty()
                                        val slots = raw.lines().map { it.trim() }.filter { it.isNotEmpty() }
                                        scope.launch {
                                            busyId = job.id
                                            error = null
                                            try {
                                                val res = client.proposeWorkRequestSlots(job.id, slots)
                                                message = res.message.ifBlank { "Окна отправлены клиенту" }
                                                slotDrafts = slotDrafts - job.id
                                                reload()
                                            } catch (e: Exception) {
                                                error = friendlyNetworkError(e)
                                            } finally {
                                                busyId = null
                                            }
                                        }
                                    },
                                    enabled = busyId != job.id &&
                                        slotDrafts[job.id].orEmpty().lines().any { it.isNotBlank() },
                                    colors = voitosPrimaryButtonColors(),
                                ) { Text("Отправить окна клиенту") }
                            }
                        }
                    }
                }

                if (profiles.isEmpty()) {
                    item {
                        Text("Вы ещё не зарегистрированы как исполнитель", color = VoitosColors.Muted)
                    }
                }
                items(profiles, key = { it.id }) { profile ->
                    val roleOffers = offersFor(profile)
                    PanelCard {
                        Text(profile.roleName, style = MaterialTheme.typography.titleMedium, color = VoitosColors.Text)
                        Spacer(modifier = Modifier.height(6.dp))
                        if (roleOffers.isEmpty()) {
                            Text(
                                "Нет новых предложений для этой роли",
                                color = VoitosColors.Muted,
                            )
                        } else {
                            roleOffers.forEach { offer ->
                                Column(modifier = Modifier.padding(vertical = 6.dp)) {
                                    Text(
                                        "#${offer.workRequestId}",
                                        style = MaterialTheme.typography.titleSmall,
                                        color = VoitosColors.Text,
                                    )
                                    if (offer.clientName.isNotBlank()) {
                                        Text(offer.clientName, color = VoitosColors.Muted)
                                    }
                                    Text(offer.locality.ifBlank { "НП не указан" }, color = VoitosColors.Muted)
                                    if (offer.address.isNotBlank()) {
                                        Text(offer.address, color = VoitosColors.Muted)
                                    }
                                    if (offer.clientPhone.isNotBlank()) {
                                        Text("Тел.: ${offer.clientPhone}", color = VoitosColors.Text)
                                    }
                                    if (offer.acceptsAtHome) {
                                        Text("Роль: приём на дому", color = VoitosColors.Accent2)
                                    }
                                    Text(offer.description, color = VoitosColors.Text)
                                    Spacer(modifier = Modifier.height(8.dp))
                                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                        Button(
                                            onClick = {
                                                scope.launch {
                                                    busyId = offer.offerId
                                                    error = null
                                                    try {
                                                        val res = client.respondExecutorOffer(
                                                            offer.offerId,
                                                            accept = true,
                                                        )
                                                        message = res.message
                                                        reload()
                                                    } catch (e: Exception) {
                                                        error = friendlyNetworkError(e)
                                                    } finally {
                                                        busyId = null
                                                    }
                                                }
                                            },
                                            enabled = busyId != offer.offerId,
                                            colors = voitosPrimaryButtonColors(),
                                        ) { Text("Беру") }
                                        Button(
                                            onClick = {
                                                scope.launch {
                                                    busyId = offer.offerId
                                                    error = null
                                                    try {
                                                        val res = client.respondExecutorOffer(
                                                            offer.offerId,
                                                            accept = false,
                                                        )
                                                        message = res.message
                                                        reload()
                                                    } catch (e: Exception) {
                                                        error = friendlyNetworkError(e)
                                                    } finally {
                                                        busyId = null
                                                    }
                                                }
                                            },
                                            enabled = busyId != offer.offerId,
                                            colors = voitosSecondaryButtonColors(),
                                        ) { Text("Отказ", color = VoitosColors.Danger) }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
