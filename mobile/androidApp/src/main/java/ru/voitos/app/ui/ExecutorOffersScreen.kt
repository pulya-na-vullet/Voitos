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
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.launch
import ru.voitos.app.api.VoitosApiClient
import ru.voitos.app.model.ExecutorOfferBrief
import ru.voitos.app.ui.theme.VoitosColors

@Composable
fun ExecutorOffersScreen(
    client: VoitosApiClient,
    onBack: () -> Unit,
) {
    var items by remember { mutableStateOf<List<ExecutorOfferBrief>>(emptyList()) }
    var error by remember { mutableStateOf<String?>(null) }
    var message by remember { mutableStateOf<String?>(null) }
    var loading by remember { mutableStateOf(true) }
    var busyId by remember { mutableStateOf<Int?>(null) }
    val scope = rememberCoroutineScope()

    fun reload() {
        scope.launch {
            loading = true
            error = null
            try {
                items = client.executorOffers().items
            } catch (e: Exception) {
                error = friendlyNetworkError(e)
            } finally {
                loading = false
            }
        }
    }

    LaunchedEffect(Unit) { reload() }

    Column(modifier = Modifier.fillMaxSize().padding(16.dp)) {
        TextButton(onClick = onBack) { Text("← Назад", color = VoitosColors.Accent) }
        Text("Заявки для меня", style = MaterialTheme.typography.headlineSmall, color = VoitosColors.Text)
        Text("Открытые предложения мастеру", color = VoitosColors.Muted)
        Spacer(modifier = Modifier.height(8.dp))
        error?.let { Text(it, color = VoitosColors.Danger) }
        message?.let { Text(it, color = VoitosColors.Ok) }
        if (loading) {
            CircularProgressIndicator(modifier = Modifier.align(Alignment.CenterHorizontally))
        }
        if (!loading && items.isEmpty()) {
            Text("Сейчас нет открытых заявок для вас", color = VoitosColors.Muted)
        }
        LazyColumn(verticalArrangement = Arrangement.spacedBy(10.dp)) {
            items(items, key = { it.offerId }) { offer ->
                PanelCard {
                    Text(
                        "#${offer.workRequestId} ${offer.roleName}",
                        style = MaterialTheme.typography.titleMedium,
                        color = VoitosColors.Text,
                    )
                    Text(offer.locality.ifBlank { "НП не указан" }, color = VoitosColors.Muted)
                    if (offer.address.isNotBlank()) {
                        Text(offer.address, color = VoitosColors.Muted)
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
                                        val res = client.respondExecutorOffer(offer.offerId, accept = true)
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
                            colors = ButtonDefaults.buttonColors(containerColor = VoitosColors.Accent),
                        ) { Text("Беру") }
                        Button(
                            onClick = {
                                scope.launch {
                                    busyId = offer.offerId
                                    error = null
                                    try {
                                        val res = client.respondExecutorOffer(offer.offerId, accept = false)
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
                            colors = ButtonDefaults.buttonColors(containerColor = VoitosColors.BgSoft),
                        ) { Text("Отказ") }
                    }
                }
            }
        }
    }
}
