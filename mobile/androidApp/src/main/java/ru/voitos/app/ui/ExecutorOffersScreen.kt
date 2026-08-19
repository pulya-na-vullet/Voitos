package ru.voitos.app.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
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
import ru.voitos.app.model.ExecutorOfferBrief
import ru.voitos.app.model.ExecutorProfileBrief
import ru.voitos.app.ui.theme.VoitosColors
import ru.voitos.app.ui.theme.voitosPrimaryButtonColors
import ru.voitos.app.ui.theme.voitosSecondaryButtonColors

@Composable
fun ExecutorOffersScreen(
    client: VoitosApiClient,
    onBack: (() -> Unit)? = null,
) {
    var profiles by remember { mutableStateOf<List<ExecutorProfileBrief>>(emptyList()) }
    var offers by remember { mutableStateOf<List<ExecutorOfferBrief>>(emptyList()) }
    var error by remember { mutableStateOf<String?>(null) }
    var message by remember { mutableStateOf<String?>(null) }
    var loading by remember { mutableStateOf(true) }
    var busyId by remember { mutableStateOf<Int?>(null) }
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
                            "Нет заявок для данного типа исполнителя",
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
                                        colors = voitosPrimaryButtonColors(),
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
