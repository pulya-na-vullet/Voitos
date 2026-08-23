package ru.voitos.app.ui

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.util.Base64
import android.widget.Toast
import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
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
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import coil.compose.AsyncImage
import coil.request.ImageRequest
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import ru.voitos.app.VoitosApi
import ru.voitos.app.api.VoitosApiClient
import ru.voitos.app.model.AppNotification
import ru.voitos.app.model.CollectionBrief
import ru.voitos.app.model.OnboardingProgress
import ru.voitos.app.model.OnboardingStep
import ru.voitos.app.model.WorkRequestBrief
import ru.voitos.app.model.WorkRequestDetail
import ru.voitos.app.nav.DeepLinks
import ru.voitos.app.ui.theme.VoitosColors
import ru.voitos.app.ui.theme.voitosPrimaryButtonColors
import ru.voitos.app.ui.theme.voitosSecondaryButtonColors
import ru.voitos.app.ui.theme.voitosAccent2ButtonColors
import kotlin.math.roundToInt

/** Понятный текст вместо Socket timeout / ConnectException. */
fun friendlyNetworkError(e: Throwable, fallback: String = "Ошибка сети"): String {
    val msg = (e.message ?: "").lowercase()
    val cause = (e.cause?.message ?: "").lowercase()
    val className = (e::class.simpleName ?: "").lowercase()
    val causeClass = (e.cause?.let { it::class.simpleName } ?: "").lowercase()
    val all = "$msg $cause $className $causeClass"
    return when {
        isServerUnavailable(all) -> "Проводятся технические работы"
        "timeout" in all || "timed out" in all || "sockettimeout" in all ->
            "Проводятся технические работы"
        "notransformationfound" in all || "expected response body" in all ->
            "Сервер вернул неожиданный ответ. Обновите приложение и бэкенд."
        // Понятные сообщения от API / подготовки аватара
        e.message.orEmpty().let { m ->
            m.isNotBlank() && m.length < 200 && (
                "аватар" in m.lowercase() ||
                    "изображен" in m.lowercase() ||
                    "фото" in m.lowercase() ||
                    "обновите бэкенд" in m.lowercase() ||
                    "пришлите" in m.lowercase()
                )
        } -> e.message ?: fallback
        msg.isNotBlank() && msg.length < 160 &&
            "http" !in msg && "exception" !in msg && "error" !in msg ->
            e.message ?: fallback
        else -> fallback
    }
}

fun isServerUnavailableError(e: Throwable): Boolean {
    val msg = (e.message ?: "").lowercase()
    val cause = (e.cause?.message ?: "").lowercase()
    val className = (e::class.simpleName ?: "").lowercase()
    val causeClass = (e.cause?.let { it::class.simpleName } ?: "").lowercase()
    return isServerUnavailable("$msg $cause $className $causeClass")
}

@Composable
fun NetworkErrorText(error: String, modifier: Modifier = Modifier) {
    if (error == "Проводятся технические работы") {
        VoitosMaintenanceMessage(modifier = modifier)
    } else {
        Text(error, color = VoitosColors.Danger, modifier = modifier)
    }
}

private fun isServerUnavailable(all: String): Boolean =
    "failed to connect" in all ||
        "connection refused" in all ||
        "connectexception" in all ||
        "connection reset" in all ||
        "network is unreachable" in all ||
        "no address associated" in all ||
        "unable to resolve" in all ||
        "unknownhost" in all ||
        "unreachable" in all ||
        "software caused connection abort" in all ||
        "cleartext" in all && "not permitted" in all

@Composable
fun InboxScreen(
    client: VoitosApiClient,
    onOpenDeepLink: (String) -> Unit,
    onOpenCollections: () -> Unit,
    onOpenWorkRequests: () -> Unit,
    onOpenSubscription: () -> Unit,
    onNewWorkRequest: () -> Unit,
    onOpenOnboarding: () -> Unit,
    onLogout: () -> Unit,
) {
    var notifications by remember { mutableStateOf<List<AppNotification>>(emptyList()) }
    var error by remember { mutableStateOf<String?>(null) }
    var loading by remember { mutableStateOf(true) }

    LaunchedEffect(Unit) {
        loading = true
        error = null
        try {
            notifications = client.notifications().items
        } catch (e: Exception) {
            error = friendlyNetworkError(e)
        } finally {
            loading = false
        }
    }

    Column(
        modifier = Modifier.fillMaxSize().padding(16.dp),
        verticalArrangement = Arrangement.Top,
    ) {
        Text("Уведомления", style = MaterialTheme.typography.headlineSmall, color = VoitosColors.Text)
        Spacer(modifier = Modifier.height(8.dp))
        TextButton(onClick = onOpenCollections) { Text("Сборы", color = VoitosColors.Accent) }
        TextButton(onClick = onOpenWorkRequests) { Text("Заявки", color = VoitosColors.Accent) }
        TextButton(onClick = onNewWorkRequest) { Text("Вызвать мастера", color = VoitosColors.Accent) }
        TextButton(onClick = onOpenSubscription) { Text("Подписка", color = VoitosColors.Accent) }
        TextButton(onClick = onOpenOnboarding) { Text("Обучение", color = VoitosColors.Accent) }
        TextButton(onClick = onLogout) { Text("Выйти", color = VoitosColors.Danger) }
        Spacer(modifier = Modifier.height(8.dp))
        if (loading) {
            VoitosListSkeleton(rows = 3)
        }
        error?.let { NetworkErrorText(it) }
        if (!loading) {
        LazyColumn(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            items(notifications, key = { it.id }) { n ->
                Card(
                    colors = androidx.compose.material3.CardDefaults.cardColors(
                        containerColor = VoitosColors.Panel,
                        contentColor = VoitosColors.Text,
                    ),
                    modifier = Modifier
                        .fillMaxWidth()
                        .clickable {
                            val route = DeepLinks.routeForType(n.type, n.entityId)
                            val fallback = when (route) {
                                is DeepLinks.Route.Subscription -> "voitos://app/subscription"
                                is DeepLinks.Route.Collection -> "voitos://app/collections/${n.entityId}"
                                is DeepLinks.Route.WorkRequest ->
                                    if (route.action == "confirm") {
                                        "voitos://app/work-requests/${n.entityId}/confirm"
                                    } else {
                                        "voitos://app/work-requests/${n.entityId}"
                                    }
                                else -> "voitos://app/home"
                            }
                            onOpenDeepLink(n.deepLink.ifBlank { fallback })
                        },
                ) {
                    Column(modifier = Modifier.padding(12.dp)) {
                        Text(n.title, style = MaterialTheme.typography.titleMedium, color = VoitosColors.Text)
                        if (n.body.isNotBlank()) {
                            Text(n.body, style = MaterialTheme.typography.bodySmall, color = VoitosColors.Muted)
                        }
                        Text(n.type, style = MaterialTheme.typography.labelSmall, color = VoitosColors.Muted)
                    }
                }
            }
        }
        }
    }
}

@Composable
fun WorkRequestsScreen(
    client: VoitosApiClient,
    onConfirm: (Int) -> Unit,
    onRate: (Int) -> Unit = {},
    onOpen: (Int) -> Unit,
    onBack: (() -> Unit)? = null,
) {
    var clientItems by remember { mutableStateOf<List<WorkRequestBrief>>(emptyList()) }
    var error by remember { mutableStateOf<String?>(null) }
    var message by remember { mutableStateOf<String?>(null) }
    var loading by remember { mutableStateOf(true) }
    var loadingId by remember { mutableStateOf<Int?>(null) }
    var archiveOpen by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()

    fun reload() {
        scope.launch {
            loading = true
            error = null
            clientItems = runCatching { client.workRequests().items }.getOrElse {
                error = friendlyNetworkError(it)
                emptyList()
            }
            loading = false
        }
    }

    LaunchedEffect(Unit) { reload() }

    val activeStatuses = setOf(
        "draft", "pending", "offering", "scheduling", "in_progress",
        "awaiting_client", "awaiting_commission",
    )
    val cancellable = setOf(
        "draft", "pending", "offering", "scheduling", "in_progress", "awaiting_client",
    )
    val activeItems = clientItems.filter { it.status in activeStatuses }
    val archiveItems = clientItems.filter { it.status !in activeStatuses }

    LazyColumn(
        modifier = Modifier.fillMaxSize().padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        item {
            if (onBack != null) {
                VoitosBackButton(onClick = onBack)
            }
            Text("Заявки", style = MaterialTheme.typography.headlineSmall, color = VoitosColors.Text)
            Text(
                "Нажмите на заявку, чтобы открыть подробности и фото.",
                color = VoitosColors.Muted,
                style = MaterialTheme.typography.bodySmall,
            )
            error?.let { NetworkErrorText(it) }
            message?.let { Text(it, color = VoitosColors.Ok) }
            if (loading) {
                VoitosListSkeleton(rows = 3)
            }
        }

        if (!loading && clientItems.isEmpty() && error == null) {
            item {
                Text("Пока нет ваших заявок", color = VoitosColors.Muted)
            }
        }

        if (!loading && activeItems.isNotEmpty()) {
            item {
                Text(
                    "Активные",
                    style = MaterialTheme.typography.titleMedium,
                    color = VoitosColors.Accent2,
                )
            }
            items(activeItems, key = { "wr-a-${it.id}" }) { wr ->
                ClientWorkRequestCard(
                    wr = wr,
                    cancellable = cancellable,
                    loadingId = loadingId,
                    onOpen = onOpen,
                    onConfirm = onConfirm,
                    onRate = onRate,
                    onCancel = {
                        scope.launch {
                            loadingId = wr.id
                            error = null
                            try {
                                client.cancelWorkRequest(wr.id)
                                message = "Заявка #${wr.id} отменена"
                                reload()
                            } catch (e: Exception) {
                                error = friendlyNetworkError(e)
                            } finally {
                                loadingId = null
                            }
                        }
                    },
                    onConfirmSlot = { slot ->
                        scope.launch {
                            loadingId = wr.id
                            error = null
                            try {
                                val res = client.confirmWorkRequestSlot(wr.id, slot)
                                message = res.message.ifBlank {
                                    "Время подтверждено — заявка в работе"
                                }
                                reload()
                            } catch (e: Exception) {
                                error = friendlyNetworkError(e)
                            } finally {
                                loadingId = null
                            }
                        }
                    },
                )
            }
        }

        if (!loading && archiveItems.isNotEmpty()) {
            item {
                TextButton(onClick = { archiveOpen = !archiveOpen }) {
                    Text(
                        if (archiveOpen) {
                            "▾ Архив (${archiveItems.size})"
                        } else {
                            "▸ Архив (${archiveItems.size})"
                        },
                        color = VoitosColors.Muted,
                    )
                }
            }
            if (archiveOpen) {
                items(archiveItems, key = { "wr-z-${it.id}" }) { wr ->
                    ClientWorkRequestCard(
                        wr = wr,
                        cancellable = emptySet(),
                        loadingId = loadingId,
                        onOpen = onOpen,
                        onConfirm = onConfirm,
                        onRate = onRate,
                        onCancel = {},
                        onConfirmSlot = {},
                        compact = true,
                    )
                }
            }
        }
    }
}

@Composable
private fun ClientWorkRequestCard(
    wr: WorkRequestBrief,
    cancellable: Set<String>,
    loadingId: Int?,
    onOpen: (Int) -> Unit,
    onConfirm: (Int) -> Unit,
    onRate: (Int) -> Unit,
    onCancel: () -> Unit,
    onConfirmSlot: (String) -> Unit,
    compact: Boolean = false,
) {
    PanelCard(
        modifier = Modifier.clickable { onOpen(wr.id) },
    ) {
        Column(modifier = Modifier.fillMaxWidth()) {
            Text(
                "#${wr.id} ${wr.roleName}",
                style = MaterialTheme.typography.titleMedium,
                color = VoitosColors.Text,
            )
            Spacer(modifier = Modifier.height(4.dp))
            Text(
                wr.statusLabel.ifBlank { workRequestStatusLabel(wr.status) },
                color = workRequestStatusColor(wr.status),
                style = MaterialTheme.typography.labelLarge,
            )
            if (!compact) {
                wr.assignedExecutorName?.let {
                    Text("Мастер: $it", color = VoitosColors.Muted)
                }
                wr.assignedPhone?.takeIf { it.isNotBlank() }?.let {
                    Text("Тел. мастера: $it", color = VoitosColors.Text)
                }
                if (wr.agreedSlot.isNotBlank()) {
                    Text("Время: ${wr.agreedSlot}", color = VoitosColors.Muted)
                }
            }
            Text(wr.description, style = MaterialTheme.typography.bodySmall, color = VoitosColors.Text)
            Text(
                "Открыть подробности →",
                color = VoitosColors.Accent,
                style = MaterialTheme.typography.labelMedium,
                modifier = Modifier.padding(top = 6.dp),
            )

            if (!compact && wr.status == "scheduling") {
                Spacer(modifier = Modifier.height(8.dp))
                Text(
                    "Согласуйте время с мастером",
                    color = VoitosColors.Accent2,
                    style = MaterialTheme.typography.labelLarge,
                )
                if (wr.proposedSlots.isNotEmpty()) {
                    wr.proposedSlots.forEach { slot ->
                        TextButton(
                            onClick = { onConfirmSlot(slot) },
                            enabled = loadingId != wr.id,
                        ) { Text("Выбрать: $slot", color = VoitosColors.Accent2) }
                    }
                } else {
                    Text(
                        "Мастер ещё не прислал окна. Откройте заявку — там будут контакты мастера.",
                        color = VoitosColors.Muted,
                        style = MaterialTheme.typography.bodySmall,
                    )
                }
            }

            if (!compact && (wr.needsConfirmAmount || wr.status == "awaiting_client")) {
                Spacer(modifier = Modifier.height(6.dp))
                Button(
                    onClick = { onConfirm(wr.id) },
                    colors = voitosPrimaryButtonColors(),
                ) { Text("Указать оплату по заказу") }
            }

            if (!compact && wr.needsRating) {
                Spacer(modifier = Modifier.height(6.dp))
                Button(
                    onClick = { onRate(wr.id) },
                    colors = voitosPrimaryButtonColors(),
                ) { Text("Оценить мастера") }
            }

            if (!compact && wr.status in cancellable) {
                TextButton(
                    onClick = onCancel,
                    enabled = loadingId != wr.id,
                ) { Text("Отменить заявку", color = VoitosColors.Danger) }
            }
        }
    }
}

@Composable
fun WorkRequestDetailScreen(
    client: VoitosApiClient,
    workRequestId: Int,
    onBack: () -> Unit,
    onConfirmAmount: (Int) -> Unit,
    onRate: (Int) -> Unit = {},
) {
    BackHandler(enabled = true) { onBack() }
    var detail by remember { mutableStateOf<WorkRequestDetail?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    var message by remember { mutableStateOf<String?>(null) }
    var loading by remember { mutableStateOf(true) }
    var confirming by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()

    fun reload() {
        scope.launch {
            loading = true
            error = null
            detail = runCatching { client.workRequest(workRequestId) }.getOrElse {
                error = friendlyNetworkError(it)
                null
            }
            loading = false
        }
    }

    LaunchedEffect(workRequestId) { reload() }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(16.dp),
    ) {
        VoitosBackButton(onClick = onBack)
        Text("Заявка", style = MaterialTheme.typography.headlineSmall, color = VoitosColors.Text)
        Spacer(modifier = Modifier.height(8.dp))
        error?.let { NetworkErrorText(it) }
        message?.let { Text(it, color = VoitosColors.Ok) }
        if (loading && detail == null) {
            VoitosDetailSkeleton()
        }
        detail?.let { wr ->
            if (wr.photoUrls.isNotEmpty()) {
                CollectionPhotoCarousel(
                    photos = wr.photoUrls,
                    contentDescription = "Заявка #${wr.id}",
                    height = 220.dp,
                )
                Spacer(modifier = Modifier.height(12.dp))
            } else {
                Box(
                    modifier = Modifier
                        .fillMaxWidth()
                        .height(100.dp)
                        .background(VoitosColors.BgSoft, androidx.compose.foundation.shape.RoundedCornerShape(14.dp)),
                    contentAlignment = Alignment.Center,
                ) {
                    Text(
                        if (wr.needsPhotos) "Фото ещё не добавлены" else "Фото не прикладывались",
                        color = VoitosColors.Muted,
                    )
                }
                Spacer(modifier = Modifier.height(12.dp))
            }

            PanelCard {
                Text(
                    "#${wr.id} ${wr.roleName}",
                    style = MaterialTheme.typography.titleLarge,
                    color = VoitosColors.Text,
                )
                Spacer(modifier = Modifier.height(6.dp))
                Text(
                    wr.statusLabel.ifBlank { workRequestStatusLabel(wr.status) },
                    color = workRequestStatusColor(wr.status),
                    style = MaterialTheme.typography.labelLarge,
                )
                if (wr.createdAt.isNotBlank()) {
                    Text(
                        "Создана: ${formatIsoDateTime(wr.createdAt)}",
                        color = VoitosColors.Muted,
                        style = MaterialTheme.typography.bodySmall,
                    )
                }
            }

            Spacer(modifier = Modifier.height(10.dp))
            PanelCard {
                Text("Описание", style = MaterialTheme.typography.titleMedium, color = VoitosColors.Text)
                Spacer(modifier = Modifier.height(6.dp))
                Text(
                    wr.description.ifBlank { "Без описания" },
                    color = VoitosColors.Text,
                    style = MaterialTheme.typography.bodyMedium,
                )
            }

            Spacer(modifier = Modifier.height(10.dp))
            PanelCard {
                Text("Детали", style = MaterialTheme.typography.titleMedium, color = VoitosColors.Text)
                Spacer(modifier = Modifier.height(6.dp))
                DetailLine("Населённый пункт", wr.clientLocality)
                DetailLine("Адрес", wr.clientAddress)
                val isExecutorView = wr.viewer.equals("executor", ignoreCase = true)
                if (isExecutorView) {
                    DetailLine("Клиент", wr.clientName.orEmpty())
                    DetailLine("Телефон клиента", wr.clientPhone.orEmpty())
                } else {
                    val masterName = wr.assignedExecutorName ?: wr.assignedName
                    DetailLine("Мастер", masterName.orEmpty())
                    DetailLine("Телефон мастера", wr.assignedPhone.orEmpty())
                    DetailLine("MAX мастера", wr.assignedMaxUsername?.let { "@$it" }.orEmpty())
                    if (wr.assignedContacts.isNotEmpty()) {
                        DetailLine("Контакты", wr.assignedContacts.joinToString("\n"))
                    }
                }
                DetailLine("Адрес приёма", wr.masterAddress)
                DetailLine("Согласованное время", wr.agreedSlot)
                wr.reportedAmount?.let {
                    DetailLine("Сумма по отчёту", "${it.roundToInt()} ₽")
                }
                wr.confirmedAmount?.let {
                    DetailLine("Подтверждённая сумма", "${it.roundToInt()} ₽")
                }
                if (wr.payMethod.isNotBlank()) {
                    DetailLine("Способ оплаты", wr.payMethod)
                }
                if (wr.photoCount > 0) {
                    DetailLine("Фото", "${wr.photoCount}")
                }
            }

            if (wr.canConfirmSlot || (wr.status == "scheduling" && wr.viewer != "executor")) {
                Spacer(modifier = Modifier.height(12.dp))
                PanelCard {
                    Text(
                        "Выбор времени",
                        style = MaterialTheme.typography.titleMedium,
                        color = VoitosColors.Text,
                    )
                    Spacer(modifier = Modifier.height(6.dp))
                    if (wr.proposedSlots.isEmpty()) {
                        Text(
                            "Мастер ещё не предложил окна. Напишите ему по контактам выше или подождите уведомление.",
                            color = VoitosColors.Muted,
                        )
                    } else {
                        Text(
                            "Выберите удобное окно:",
                            color = VoitosColors.Muted,
                        )
                        Spacer(modifier = Modifier.height(6.dp))
                        wr.proposedSlots.forEach { slot ->
                            Button(
                                onClick = {
                                    scope.launch {
                                        confirming = true
                                        error = null
                                        try {
                                            val res = client.confirmWorkRequestSlot(wr.id, slot)
                                            message = res.message.ifBlank {
                                                "Время подтверждено — заявка в работе"
                                            }
                                            reload()
                                        } catch (e: Exception) {
                                            error = friendlyNetworkError(e)
                                        } finally {
                                            confirming = false
                                        }
                                    }
                                },
                                modifier = Modifier.fillMaxWidth(),
                                enabled = !confirming && wr.canConfirmSlot,
                                colors = voitosPrimaryButtonColors(),
                            ) { Text(slot) }
                            Spacer(modifier = Modifier.height(6.dp))
                        }
                    }
                }
            }

            if (wr.needsConfirmAmount) {
                Spacer(modifier = Modifier.height(12.dp))
                Button(
                    onClick = { onConfirmAmount(wr.id) },
                    modifier = Modifier.fillMaxWidth(),
                    colors = voitosPrimaryButtonColors(),
                ) { Text("Указать оплату по заказу") }
            }
            if (wr.needsRating) {
                Spacer(modifier = Modifier.height(12.dp))
                Button(
                    onClick = { onRate(wr.id) },
                    modifier = Modifier.fillMaxWidth(),
                    colors = voitosPrimaryButtonColors(),
                ) { Text("Оценить работу мастера") }
            }

            if (wr.canMarkDone) {
                Spacer(modifier = Modifier.height(12.dp))
                Button(
                    onClick = {
                        scope.launch {
                            confirming = true
                            error = null
                            try {
                                val res = client.markWorkRequestDone(wr.id)
                                message = res.message.ifBlank { "Отметили выполнениеку выполненной" }
                                reload()
                            } catch (e: Exception) {
                                error = friendlyNetworkError(e)
                            } finally {
                                confirming = false
                            }
                        }
                    },
                    modifier = Modifier.fillMaxWidth(),
                    enabled = !confirming,
                    colors = voitosPrimaryButtonColors(),
                ) { Text("Работа выполнена") }
            } else if (wr.status == "in_progress" || (wr.status == "scheduling" && wr.agreedSlot.isNotBlank())) {
                Spacer(modifier = Modifier.height(8.dp))
                val waitText = when {
                    wr.viewer == "executor" && wr.executorMarkedDone && !wr.clientMarkedDone ->
                        "Ждём отметку клиента «Работа выполнена»"
                    wr.viewer != "executor" && wr.clientMarkedDone && !wr.executorMarkedDone ->
                        "Ждём отметку мастера «Работа выполнена»"
                    else -> ""
                }
                if (waitText.isNotBlank()) {
                    Text(waitText, color = VoitosColors.Muted, style = MaterialTheme.typography.bodySmall)
                }
            }

            if (wr.needsExecutorPaymentReport) {
                Spacer(modifier = Modifier.height(12.dp))
                ExecutorPaymentReportCard(
                    enabled = !confirming,
                    onSubmit = { method, amount ->
                        scope.launch {
                            confirming = true
                            error = null
                            try {
                                val res = client.reportWorkRequestPayment(wr.id, method, amount)
                                message = res.message.ifBlank { "Отчёт по оплате принят" }
                                reload()
                            } catch (e: Exception) {
                                error = friendlyNetworkError(e)
                            } finally {
                                confirming = false
                            }
                        }
                    },
                )
            }

            if (wr.canCancel) {
                Spacer(modifier = Modifier.height(8.dp))
                var cancelComment by remember { mutableStateOf("") }
                var showCancel by remember { mutableStateOf(false) }
                if (!showCancel) {
                    TextButton(
                        onClick = { showCancel = true },
                        modifier = Modifier.fillMaxWidth(),
                    ) { Text("Отменить заявку", color = VoitosColors.Danger) }
                } else {
                    PanelCard {
                        Text("Комментарий к отмене", color = VoitosColors.Text)
                        Spacer(modifier = Modifier.height(6.dp))
                        androidx.compose.material3.OutlinedTextField(
                            value = cancelComment,
                            onValueChange = { cancelComment = it },
                            modifier = Modifier.fillMaxWidth(),
                            placeholder = { Text("Почему отменяете?") },
                        )
                        Spacer(modifier = Modifier.height(8.dp))
                        Button(
                            onClick = {
                                scope.launch {
                                    confirming = true
                                    error = null
                                    try {
                                        client.cancelWorkRequest(wr.id, cancelComment.trim())
                                        message = "Заявка отменена"
                                        reload()
                                    } catch (e: Exception) {
                                        error = friendlyNetworkError(e)
                                    } finally {
                                        confirming = false
                                    }
                                }
                            },
                            modifier = Modifier.fillMaxWidth(),
                            enabled = !confirming && cancelComment.trim().isNotEmpty(),
                            colors = voitosPrimaryButtonColors(),
                        ) { Text("Подтвердить отмену") }
                        TextButton(onClick = { showCancel = false }) {
                            Text("Назад", color = VoitosColors.Muted)
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun ExecutorPaymentReportCard(
    enabled: Boolean,
    onSubmit: (payMethod: String, amount: Double) -> Unit,
) {
    var method by remember { mutableStateOf("cash") }
    var amountText by remember { mutableStateOf("") }
    PanelCard {
        Text("Как рассчитались?", style = MaterialTheme.typography.titleMedium, color = VoitosColors.Text)
        Spacer(modifier = Modifier.height(6.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            TextButton(onClick = { method = "cash" }, enabled = enabled) {
                Text(
                    "Наличка",
                    color = if (method == "cash") VoitosColors.Accent2 else VoitosColors.Muted,
                )
            }
            TextButton(onClick = { method = "transfer" }, enabled = enabled) {
                Text(
                    "Перевод",
                    color = if (method == "transfer") VoitosColors.Accent2 else VoitosColors.Muted,
                )
            }
        }
        Spacer(modifier = Modifier.height(6.dp))
        androidx.compose.material3.OutlinedTextField(
            value = amountText,
            onValueChange = { amountText = it.filter { ch -> ch.isDigit() || ch == '.' || ch == ',' } },
            modifier = Modifier.fillMaxWidth(),
            placeholder = { Text("Сумма, ₽") },
            enabled = enabled,
        )
        Spacer(modifier = Modifier.height(8.dp))
        Button(
            onClick = {
                val amount = amountText.replace(',', '.').toDoubleOrNull()
                if (amount != null && amount > 0) onSubmit(method, amount)
            },
            modifier = Modifier.fillMaxWidth(),
            enabled = enabled && amountText.isNotBlank(),
            colors = voitosPrimaryButtonColors(),
        ) { Text("Отправить отчёт по оплате") }
    }
}

@Composable
private fun DetailLine(label: String, value: String) {
    if (value.isBlank()) return
    Column(modifier = Modifier.padding(vertical = 4.dp)) {
        Text(label, style = MaterialTheme.typography.labelSmall, color = VoitosColors.Muted)
        Text(value, style = MaterialTheme.typography.bodyLarge, color = VoitosColors.Text)
    }
}

private fun formatIsoDateTime(raw: String): String {
    val cleaned = raw.replace('T', ' ').take(16)
    return cleaned.ifBlank { raw }
}

private fun workRequestStatusLabel(status: String): String = when (status) {
    "draft" -> "Черновик"
    "pending" -> "Новая"
    "offering" -> "Ищем исполнителя"
    "scheduling" -> "Согласование времени"
    "in_progress" -> "В работе"
    "awaiting_client" -> "Ждём подтверждения"
    "awaiting_commission" -> "Ждём комиссию от мастера в 10%"
    "done" -> "Выполнена"
    "cancelled" -> "Отменена"
    else -> status
}

private fun workRequestStatusColor(status: String): Color = when (status) {
    "offering", "pending" -> VoitosColors.Accent2
    "scheduling" -> VoitosColors.Warn
    "in_progress" -> VoitosColors.Ok
    "cancelled" -> VoitosColors.Muted
    "awaiting_client", "awaiting_commission" -> VoitosColors.Gold
    else -> VoitosColors.Muted
}

@Composable
fun ConfirmAmountScreen(
    client: VoitosApiClient,
    workRequestId: Int,
    onDone: (needsRating: Boolean) -> Unit,
    onBack: () -> Unit,
) {
    BackHandler(enabled = true) { onBack() }
    var amount by remember { mutableStateOf("") }
    var payMethod by remember { mutableStateOf("cash") }
    var message by remember { mutableStateOf<String?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    var loading by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()

    Column(modifier = Modifier.fillMaxSize().padding(16.dp)) {
        VoitosBackButton(onClick = onBack)
        Text("Оплата по заказу", style = MaterialTheme.typography.headlineSmall, color = VoitosColors.Text)
        Text("Заявка #$workRequestId", color = VoitosColors.Muted)
        Spacer(modifier = Modifier.height(8.dp))
        Text("Как рассчитались с мастером?", color = VoitosColors.Text)
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            TextButton(onClick = { payMethod = "cash" }, enabled = !loading) {
                Text("Наличка", color = if (payMethod == "cash") VoitosColors.Accent2 else VoitosColors.Muted)
            }
            TextButton(onClick = { payMethod = "transfer" }, enabled = !loading) {
                Text("Перевод", color = if (payMethod == "transfer") VoitosColors.Accent2 else VoitosColors.Muted)
            }
        }
        Spacer(modifier = Modifier.height(12.dp))
        Button(
            onClick = {
                scope.launch {
                    loading = true
                    error = null
                    try {
                        val res = client.confirmAmount(
                            workRequestId,
                            confirmed = true,
                            payMethod = payMethod,
                        )
                        message = res.message.ifBlank { "Сумма подтверждена" }
                        onDone(res.needsRating)
                    } catch (e: Exception) {
                        error = friendlyNetworkError(e)
                    } finally {
                        loading = false
                    }
                }
            },
            modifier = Modifier.fillMaxWidth(),
            enabled = !loading,
            colors = voitosPrimaryButtonColors(),
        ) { Text("Оплатил мастеру") }
        Spacer(modifier = Modifier.height(8.dp))
        OutlinedTextField(
            value = amount,
            onValueChange = { amount = it },
            label = { Text("Своя сумма, ₽", color = VoitosColors.Muted) },
            modifier = Modifier.fillMaxWidth(),
            colors = ru.voitos.app.ui.theme.voitosOutlinedFieldColors(),
        )
        Button(
            onClick = {
                scope.launch {
                    loading = true
                    error = null
                    try {
                        val v = amount.replace(',', '.').toDoubleOrNull()
                        if (v == null) {
                            error = "Нужно число"
                            return@launch
                        }
                        val res = client.confirmAmount(
                            workRequestId,
                            confirmed = false,
                            amount = v,
                            payMethod = payMethod,
                        )
                        message = res.message.ifBlank { "Сохранено: $v ₽" }
                        onDone(res.needsRating)
                    } catch (e: Exception) {
                        error = friendlyNetworkError(e)
                    } finally {
                        loading = false
                    }
                }
            },
            modifier = Modifier.fillMaxWidth(),
            enabled = !loading,
            colors = voitosSecondaryButtonColors(),
        ) { Text("Отправить свою сумму") }
        message?.let { Text(it, color = VoitosColors.Ok) }
        error?.let { Text(it, color = VoitosColors.Danger) }
    }
}

@Composable
fun RateMasterScreen(
    client: VoitosApiClient,
    workRequestId: Int,
    onDone: () -> Unit,
    onBack: () -> Unit,
) {
    BackHandler(enabled = true) { onBack() }
    var score by remember { mutableIntStateOf(0) }
    var comment by remember { mutableStateOf("") }
    var executorName by remember { mutableStateOf("") }
    var alreadyRated by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    var message by remember { mutableStateOf<String?>(null) }
    var loading by remember { mutableStateOf(false) }
    var loadingMeta by remember { mutableStateOf(true) }
    val scope = rememberCoroutineScope()

    LaunchedEffect(workRequestId) {
        loadingMeta = true
        runCatching { client.workRequest(workRequestId) }
            .onSuccess { wr ->
                executorName = (wr.assignedExecutorName ?: wr.assignedName).orEmpty()
                alreadyRated = !wr.needsRating
                if (alreadyRated) {
                    message = "Эта заявка уже оценена или оценка пока не нужна"
                }
            }
            .onFailure { error = friendlyNetworkError(it) }
        loadingMeta = false
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(16.dp),
    ) {
        VoitosBackButton(onClick = onBack)
        Text("Оценка мастера", style = MaterialTheme.typography.headlineSmall, color = VoitosColors.Text)
        Text("Заявка #$workRequestId", color = VoitosColors.Muted)
        if (executorName.isNotBlank()) {
            Spacer(modifier = Modifier.height(4.dp))
            Text(
                "Оцените работу $executorName",
                color = VoitosColors.Text,
                style = MaterialTheme.typography.bodyMedium,
            )
        }
        Spacer(modifier = Modifier.height(12.dp))
        if (loadingMeta) {
            CircularProgressIndicator(color = VoitosColors.Accent)
        } else {
            Text("Оценка от 1 до 5", color = VoitosColors.Text)
            Spacer(modifier = Modifier.height(8.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                (1..5).forEach { n ->
                    val selected = score == n
                    Button(
                        onClick = { score = n },
                        enabled = !alreadyRated,
                        colors = if (selected) voitosPrimaryButtonColors() else voitosSecondaryButtonColors(),
                    ) { Text("$n") }
                }
            }
            Spacer(modifier = Modifier.height(12.dp))
                        androidx.compose.material3.OutlinedTextField(
                value = comment,
                onValueChange = { comment = it.take(2000) },
                label = { Text("Комментарий (необязательно)", color = VoitosColors.Muted) },
                modifier = Modifier.fillMaxWidth(),
                minLines = 3,
                enabled = !alreadyRated,
                colors = ru.voitos.app.ui.theme.voitosOutlinedFieldColors(),
            )
            Spacer(modifier = Modifier.height(12.dp))
            error?.let { Text(it, color = VoitosColors.Danger) }
            message?.let { Text(it, color = VoitosColors.Ok) }
            Spacer(modifier = Modifier.height(8.dp))
            if (!alreadyRated) {
                Button(
                    onClick = {
                        scope.launch {
                            if (score !in 1..5) {
                                error = "Выберите оценку от 1 до 5"
                                return@launch
                            }
                            loading = true
                            error = null
                            try {
                                val res = client.rateWorkRequest(
                                    workRequestId,
                                    score = score,
                                    comment = comment.trim(),
                                )
                                message = res.message.ifBlank { "Спасибо за оценку!" }
                                onDone()
                            } catch (e: Exception) {
                                error = friendlyNetworkError(e)
                            } finally {
                                loading = false
                            }
                        }
                    },
                    modifier = Modifier.fillMaxWidth(),
                    enabled = !loading && score in 1..5,
                    colors = voitosPrimaryButtonColors(),
                ) { Text(if (loading) "Отправка…" else "Отправить оценку") }
            }
            TextButton(onClick = onBack, modifier = Modifier.fillMaxWidth()) {
                Text(if (alreadyRated) "Назад" else "Позже", color = VoitosColors.Muted)
            }
        }
    }
}

@Composable
fun SubscriptionScreen(
    client: VoitosApiClient,
    onBack: () -> Unit,
    /** Подписка закрыта: нельзя уйти в приложение без оплаты. */
    paymentRequired: Boolean = false,
    /** Доп. текст над реквизитами (например, после тапа на сборы/мастера). */
    gateMessage: String = "",
    onAccessRestored: () -> Unit = {},
    onLogout: (() -> Unit)? = null,
) {
    BackHandler(enabled = true) {
        if (paymentRequired) {
            onLogout?.invoke()
        } else {
            onBack()
        }
    }
    var info by remember { mutableStateOf<ru.voitos.app.model.SubscriptionInfo?>(null) }
    var receipts by remember { mutableStateOf<List<ru.voitos.app.model.ReceiptBrief>>(emptyList()) }
    var error by remember { mutableStateOf<String?>(null) }
    var message by remember { mutableStateOf<String?>(null) }
    var loading by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()
    val context = LocalContext.current

    fun copyPhone(phone: String) {
        val cm = context.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
        cm.setPrimaryClip(ClipData.newPlainText("Телефон для оплаты", phone))
        Toast.makeText(context, "Номер скопирован", Toast.LENGTH_SHORT).show()
    }

    fun reload() {
        scope.launch {
            try {
                val sub = client.subscription()
                info = sub
                receipts = client.receipts().items
                if (paymentRequired && sub.state == "active") {
                    onAccessRestored()
                }
            } catch (e: Exception) {
                error = friendlyNetworkError(e)
            }
        }
    }

    LaunchedEffect(Unit) { reload() }

    // Пока ждём проверку чека — периодически обновляем статус.
    LaunchedEffect(paymentRequired) {
        if (!paymentRequired) return@LaunchedEffect
        while (true) {
            delay(12_000)
            runCatching {
                val sub = client.subscription()
                info = sub
                receipts = runCatching { client.receipts().items }.getOrDefault(receipts)
                if (sub.state == "active") {
                    onAccessRestored()
                    return@LaunchedEffect
                }
            }
        }
    }

    val picker = rememberLauncherForActivityResult(ActivityResultContracts.GetContent()) { uri ->
        if (uri == null) return@rememberLauncherForActivityResult
        scope.launch {
            loading = true
            error = null
            try {
                val bytes = context.contentResolver.openInputStream(uri)?.use { it.readBytes() }
                    ?: throw IllegalStateException("Не удалось прочитать файл")
                val b64 = Base64.encodeToString(bytes, Base64.NO_WRAP)
                val name = uri.lastPathSegment?.substringAfterLast('/') ?: "receipt.jpg"
                val res = client.uploadReceipt(b64, name)
                message = "Чек #${res.id} отправлен на проверку. Доступ откроется после одобрения."
                reload()
            } catch (e: Exception) {
                error = friendlyNetworkError(e)
            } finally {
                loading = false
            }
        }
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(16.dp),
    ) {
        if (!paymentRequired) {
            VoitosBackButton(onClick = onBack)
        }
        Text(
            if (paymentRequired) "Оплата подписки" else "Подписка",
            style = MaterialTheme.typography.headlineSmall,
            color = VoitosColors.Text,
        )
        Spacer(modifier = Modifier.height(8.dp))
        val topHint = gateMessage.ifBlank {
            if (paymentRequired) "Оплатите подписку для доступа к услугам" else ""
        }
        if (topHint.isNotBlank()) {
            Text(
                topHint,
                color = VoitosColors.Warn,
                style = MaterialTheme.typography.bodyMedium,
            )
            Spacer(modifier = Modifier.height(8.dp))
        }
        if (paymentRequired) {
            Text(
                "Переведите оплату по реквизитам ниже и загрузите чек. " +
                    "После проверки администратором доступ к сборам и вызову мастера откроется.",
                color = VoitosColors.Muted,
                style = MaterialTheme.typography.bodyMedium,
            )
            Spacer(modifier = Modifier.height(12.dp))
        }
        error?.let { Text(it, color = VoitosColors.Danger) }
        message?.let { Text(it, color = VoitosColors.Ok) }

        info?.let { s ->
            // Реквизиты самозанятого — всегда сверху.
            if (s.paymentPhone.isNotBlank() || s.paymentName.isNotBlank()) {
                PanelCard {
                    Text(
                        "Реквизиты для оплаты",
                        style = MaterialTheme.typography.titleMedium,
                        color = VoitosColors.Accent2,
                    )
                    Spacer(modifier = Modifier.height(8.dp))
                    if (s.paymentName.isNotBlank()) {
                        Text("Получатель: ${s.paymentName}", color = VoitosColors.Text)
                        Spacer(modifier = Modifier.height(4.dp))
                    }
                    if (s.paymentPhone.isNotBlank()) {
                        Text("Телефон: ${s.paymentPhone}", color = VoitosColors.Text)
                        Spacer(modifier = Modifier.height(8.dp))
                        Button(
                            onClick = { copyPhone(s.paymentPhone) },
                            modifier = Modifier.fillMaxWidth(),
                            colors = voitosPrimaryButtonColors(),
                        ) {
                            Text("Скопировать номер телефона")
                        }
                    }
                    Spacer(modifier = Modifier.height(8.dp))
                    Text("Цена: ${s.priceRub} ₽/мес", color = VoitosColors.Text)
                }
                Spacer(modifier = Modifier.height(12.dp))
            }

            PanelCard {
                Text(
                    s.label.ifBlank {
                        when (s.state) {
                            "blocked" -> "Доступ закрыт — нужна оплата"
                            "grace" -> "Льготный период — оплатите заранее"
                            "active" -> "Подписка активна"
                            else -> s.state
                        }
                    },
                    style = MaterialTheme.typography.titleMedium,
                    color = when (s.state) {
                        "blocked" -> VoitosColors.Danger
                        "grace" -> VoitosColors.Warn
                        else -> VoitosColors.Accent2
                    },
                )
                Spacer(modifier = Modifier.height(8.dp))
                Text("Статус: ${s.state}", color = VoitosColors.Text)
                s.subscriptionUntil?.let { Text("Была до: $it", color = VoitosColors.Text) }
                s.graceUntil?.let { Text("Grace до: $it", color = VoitosColors.Muted) }
                if (s.pendingReceipts > 0) {
                    Spacer(modifier = Modifier.height(6.dp))
                    Text("Чеков на проверке: ${s.pendingReceipts}", color = VoitosColors.Warn)
                }
                val fam = s.family
                if (fam.isPayer && fam.members.isNotEmpty()) {
                    Spacer(modifier = Modifier.height(8.dp))
                    Text("К подписке подключены:", color = VoitosColors.Muted)
                    fam.members.forEach { m ->
                        Text("• ${m.name.ifBlank { m.phone }}", color = VoitosColors.Text)
                    }
                } else if (fam.payerName.isNotBlank()) {
                    Spacer(modifier = Modifier.height(8.dp))
                    Text("Подписка через: ${fam.payerName}", color = VoitosColors.Text)
                }
            }
            Spacer(modifier = Modifier.height(12.dp))
            Button(
                onClick = { picker.launch("*/*") },
                modifier = Modifier.fillMaxWidth(),
                enabled = !loading,
                colors = voitosPrimaryButtonColors(),
            ) {
                Text(if (loading) "Отправка…" else "Загрузить чек")
            }
            Text(
                "Фото перевода или PDF чека",
                color = VoitosColors.Muted,
                style = MaterialTheme.typography.bodySmall,
                modifier = Modifier.padding(top = 6.dp),
            )
            if (paymentRequired && onLogout != null) {
                Spacer(modifier = Modifier.height(8.dp))
                TextButton(
                    onClick = onLogout,
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    Text("Выйти из аккаунта", color = VoitosColors.Danger)
                }
            }
        }
        if (receipts.isNotEmpty()) {
            Spacer(modifier = Modifier.height(16.dp))
            Text("История подписки", style = MaterialTheme.typography.titleMedium, color = VoitosColors.Accent2)
            Spacer(modifier = Modifier.height(8.dp))
            receipts.forEach { r ->
                PanelCard {
                    Text("#${r.id} · ${r.status}", color = VoitosColors.Text)
                    r.amount?.let { Text("$it ₽ · ${r.period}", color = VoitosColors.Muted) }
                    Text(r.createdAt, style = MaterialTheme.typography.bodySmall, color = VoitosColors.Muted)
                }
                Spacer(modifier = Modifier.height(8.dp))
            }
        }
    }
}

@Composable
fun NewWorkRequestScreen(
    client: VoitosApiClient,
    onCreated: (id: Int, needsPhotos: Boolean) -> Unit,
    onBack: (() -> Unit)? = null,
) {
    var roles by remember { mutableStateOf<List<ru.voitos.app.model.ExecutorRole>>(emptyList()) }
    var selectedId by remember { mutableStateOf<Int?>(null) }
    var description by remember { mutableStateOf("") }
    var error by remember { mutableStateOf<String?>(null) }
    var message by remember { mutableStateOf<String?>(null) }
    var loading by remember { mutableStateOf(false) }
    var masters by remember { mutableStateOf<List<ru.voitos.app.model.RoleMasterBrief>>(emptyList()) }
    var mastersLoading by remember { mutableStateOf(false) }
    var selectedMasterId by remember { mutableStateOf<Int?>(null) }
    var freeSlots by remember { mutableStateOf<List<ru.voitos.app.model.FreeSlotBrief>>(emptyList()) }
    var slotsLoading by remember { mutableStateOf(false) }
    var selectedSlot by remember { mutableStateOf<String?>(null) }
    var slotsExpanded by remember { mutableStateOf(false) }
    var masterBlockMessage by remember { mutableStateOf<String?>(null) }
    val scope = rememberCoroutineScope()

    val selectedRole = roles.firstOrNull { it.id == selectedId }
    val booksMaster = selectedRole?.clientBooksMaster == true

    LaunchedEffect(Unit) {
        try {
            roles = client.executorRoles(forCall = true).items
        } catch (e: Exception) {
            error = friendlyNetworkError(e)
        }
    }

    LaunchedEffect(selectedId) {
        selectedMasterId = null
        selectedSlot = null
        slotsExpanded = false
        freeSlots = emptyList()
        masterBlockMessage = null
        masters = emptyList()
        val role = roles.firstOrNull { it.id == selectedId }
        if (role == null || !role.clientBooksMaster) return@LaunchedEffect
        mastersLoading = true
        error = null
        try {
            masters = client.roleMasters(role.id).items
        } catch (e: Exception) {
            error = friendlyNetworkError(e)
        } finally {
            mastersLoading = false
        }
    }

    LaunchedEffect(selectedMasterId) {
        selectedSlot = null
        slotsExpanded = false
        freeSlots = emptyList()
        masterBlockMessage = null
        val mid = selectedMasterId ?: return@LaunchedEffect
        val brief = masters.firstOrNull { it.contractorId == mid }
        if (brief != null && !brief.canAccept) {
            masterBlockMessage = brief.blockedMessage.ifBlank {
                "Мастер пока не может принять ваш заказ."
            }
            return@LaunchedEffect
        }
        slotsLoading = true
        try {
            val res = client.contractorFreeSlots(mid, days = 2)
            if (!res.canAccept) {
                masterBlockMessage = res.blockedMessage.ifBlank {
                    "Мастер пока не может принять ваш заказ — сначала нужно оплатить комиссию по прошлому заказу."
                }
                freeSlots = emptyList()
            } else {
                freeSlots = res.items
                // Пока слот не выбран — сразу открываем список на 2 дня.
                slotsExpanded = freeSlots.isNotEmpty()
            }
        } catch (e: Exception) {
            error = friendlyNetworkError(e)
        } finally {
            slotsLoading = false
        }
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(16.dp),
    ) {
        if (onBack != null) {
            VoitosBackButton(onClick = onBack)
        }
        Text("Вызов мастера", style = MaterialTheme.typography.headlineSmall, color = VoitosColors.Text)
        Text("Выбрать роли доступные в вашем регионе", color = VoitosColors.Muted)
        Spacer(modifier = Modifier.height(12.dp))

        if (roles.isEmpty() && error == null) {
            Text(
                "В вашем населённом пункте пока нет доступных мастеров",
                color = VoitosColors.Muted,
            )
            Spacer(modifier = Modifier.height(12.dp))
        }

        RoleTileGrid(
            roles = roles,
            selectedId = selectedId,
            onSelect = { selectedId = it },
        )

        if (booksMaster) {
            Spacer(modifier = Modifier.height(16.dp))
            Text("Мастер", style = MaterialTheme.typography.titleMedium, color = VoitosColors.Text)
            Text("Выберите доступного мастера и свободное время", color = VoitosColors.Muted)
            Spacer(modifier = Modifier.height(8.dp))
            when {
                mastersLoading -> VoitosListSkeleton(rows = 2)
                masters.isEmpty() -> Text(
                    "В вашем населённом пункте пока нет мастеров этой роли",
                    color = VoitosColors.Muted,
                )
                else -> {
                    masters.forEach { m ->
                        val selected = selectedMasterId == m.contractorId
                        Column(
                            modifier = Modifier
                                .fillMaxWidth()
                                .padding(vertical = 4.dp)
                                .background(
                                    if (selected) VoitosColors.Accent2.copy(alpha = 0.18f)
                                    else VoitosColors.Panel.copy(alpha = 0.86f),
                                    androidx.compose.foundation.shape.RoundedCornerShape(12.dp),
                                )
                                .clickable { selectedMasterId = m.contractorId }
                                .padding(12.dp),
                        ) {
                            Text(m.name, color = VoitosColors.Text, style = MaterialTheme.typography.titleSmall)
                            if (m.locality.isNotBlank()) {
                                Text(m.locality, color = VoitosColors.Muted)
                            }
                            if (!m.canAccept) {
                                Text(
                                    m.blockedMessage.ifBlank { "Сейчас недоступен" },
                                    color = VoitosColors.Danger,
                                    style = MaterialTheme.typography.bodySmall,
                                )
                            } else {
                                Text(
                                    "Свободных окон: ${m.freeSlotsPreview}",
                                    color = VoitosColors.Ok,
                                    style = MaterialTheme.typography.bodySmall,
                                )
                            }
                        }
                    }
                }
            }

            masterBlockMessage?.let {
                Spacer(modifier = Modifier.height(8.dp))
                Text(it, color = VoitosColors.Danger)
            }

            if (selectedMasterId != null && masterBlockMessage == null) {
                Spacer(modifier = Modifier.height(12.dp))
                Text(
                    "Время",
                    style = MaterialTheme.typography.titleMedium,
                    color = VoitosColors.Text,
                    modifier = Modifier
                        .fillMaxWidth()
                        .clickable(enabled = freeSlots.isNotEmpty() && !slotsLoading) {
                            slotsExpanded = !slotsExpanded
                        },
                )
                Spacer(modifier = Modifier.height(6.dp))
                when {
                    slotsLoading -> VoitosListSkeleton(rows = 2)
                    freeSlots.isEmpty() -> Text("Нет свободных окон на ближайшие 2 дня", color = VoitosColors.Muted)
                    slotsExpanded -> {
                        freeSlots.groupBy { it.day }.forEach { (day, daySlots) ->
                            Text(
                                day,
                                color = VoitosColors.Muted,
                                style = MaterialTheme.typography.labelLarge,
                                modifier = Modifier.padding(top = 8.dp, bottom = 4.dp),
                            )
                            daySlots.forEach { slot ->
                                val on = selectedSlot == slot.label
                                Button(
                                    onClick = {
                                        selectedSlot = slot.label
                                        slotsExpanded = false
                                    },
                                    modifier = Modifier.fillMaxWidth(),
                                    colors = if (on) voitosPrimaryButtonColors() else voitosSecondaryButtonColors(),
                                ) { Text(slot.label) }
                                Spacer(modifier = Modifier.height(4.dp))
                            }
                        }
                        Spacer(modifier = Modifier.height(8.dp))
                        TextButton(
                            onClick = { slotsExpanded = false },
                            modifier = Modifier.fillMaxWidth(),
                        ) { Text("Свернуть список") }
                    }
                    else -> {
                        if (!selectedSlot.isNullOrBlank()) {
                            Text(
                                "Вы выбрали:\n$selectedSlot",
                                color = VoitosColors.Ok,
                                style = MaterialTheme.typography.bodyLarge,
                            )
                            Spacer(modifier = Modifier.height(4.dp))
                            TextButton(
                                onClick = { slotsExpanded = true },
                                modifier = Modifier.fillMaxWidth(),
                            ) { Text("Изменить время") }
                        } else {
                            TextButton(
                                onClick = { slotsExpanded = true },
                                modifier = Modifier.fillMaxWidth(),
                            ) { Text("Выбрать время") }
                        }
                    }
                }
            }
        }

        Spacer(modifier = Modifier.height(16.dp))
                        androidx.compose.material3.OutlinedTextField(
            value = description,
            onValueChange = { description = it },
            label = { Text("Что случилось", color = VoitosColors.Muted) },
            modifier = Modifier.fillMaxWidth(),
            minLines = 3,
            colors = ru.voitos.app.ui.theme.voitosOutlinedFieldColors(),
        )
        Spacer(modifier = Modifier.height(8.dp))
        val canSubmit = !loading && description.length >= 5 && selectedId != null && (
            !booksMaster || (selectedMasterId != null && !selectedSlot.isNullOrBlank() && masterBlockMessage == null)
            )
        Button(
            onClick = {
                val rid = selectedId
                if (rid == null) {
                    error = "Выберите роль"
                    return@Button
                }
                if (booksMaster && (selectedMasterId == null || selectedSlot.isNullOrBlank())) {
                    error = "Выберите мастера и время"
                    return@Button
                }
                scope.launch {
                    loading = true
                    error = null
                    try {
                        val created = client.createWorkRequest(
                            roleId = rid,
                            description = description,
                            contractorId = if (booksMaster) selectedMasterId else null,
                            slot = if (booksMaster) selectedSlot else null,
                        )
                        message = when {
                            created.needsPhotos -> "Заявка #${created.id}: добавьте фото"
                            created.clientPrebooked -> "Заявка #${created.id}: ждём подтверждения мастера"
                            else -> "Заявка #${created.id} отправлена мастерам"
                        }
                        onCreated(created.id, created.needsPhotos)
                    } catch (e: Exception) {
                        error = friendlyNetworkError(e)
                    } finally {
                        loading = false
                    }
                }
            },
            modifier = Modifier.fillMaxWidth(),
            enabled = canSubmit,
            colors = voitosPrimaryButtonColors(),
        ) {
            Text(if (booksMaster) "Записаться" else "Создать заявку")
        }
        message?.let { Text(it, color = VoitosColors.Ok) }
        error?.let { Text(it, color = VoitosColors.Danger) }
    }
}

@Composable
fun WorkRequestPhotosScreen(
    client: VoitosApiClient,
    workRequestId: Int,
    onSubmitted: () -> Unit,
    onBack: () -> Unit,
) {
    BackHandler(enabled = true) { onBack() }
    var photoCount by remember { mutableIntStateOf(0) }
    var error by remember { mutableStateOf<String?>(null) }
    var message by remember { mutableStateOf<String?>(null) }
    var loading by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()
    val context = LocalContext.current

    val picker = rememberLauncherForActivityResult(ActivityResultContracts.GetContent()) { uri ->
        if (uri == null) return@rememberLauncherForActivityResult
        scope.launch {
            loading = true
            error = null
            try {
                val (b64, name) = prepareWorkPhotoJpegBase64(context, uri)
                val res = client.addWorkRequestPhoto(workRequestId, b64, name)
                photoCount = res.photoCount
                message = "Приложено фото: $photoCount"
            } catch (e: Exception) {
                error = friendlyNetworkError(e)
            } finally {
                loading = false
            }
        }
    }

    Column(modifier = Modifier.fillMaxSize().padding(16.dp)) {
        VoitosBackButton(onClick = onBack)
        Text("Фото заявки #$workRequestId", style = MaterialTheme.typography.headlineSmall, color = VoitosColors.Text)
        Text("Нужно хотя бы одно фото места работ.", color = VoitosColors.Muted)
        Spacer(modifier = Modifier.height(12.dp))
        Button(
            onClick = { picker.launch("image/*") },
            modifier = Modifier.fillMaxWidth(),
            enabled = !loading,
            colors = voitosPrimaryButtonColors(),
        ) { Text("Выбрать фото") }
        Spacer(modifier = Modifier.height(8.dp))
        Text("Сейчас приложено: $photoCount", color = VoitosColors.Text)
        Button(
            onClick = {
                scope.launch {
                    loading = true
                    error = null
                    try {
                        val res = client.submitWorkRequest(workRequestId)
                        message = "Заявка отправлена (фото: ${res.photoCount})"
                        onSubmitted()
                    } catch (e: Exception) {
                        error = e.message
                    } finally {
                        loading = false
                    }
                }
            },
            modifier = Modifier.fillMaxWidth(),
            enabled = !loading && photoCount > 0,
            colors = voitosPrimaryButtonColors(),
        ) { Text("Готово — отправить") }
        if (loading) CircularProgressIndicator(color = VoitosColors.Accent)
        message?.let { Text(it, color = VoitosColors.Ok) }
        error?.let { Text(it, color = VoitosColors.Danger) }
    }
}

@Composable
fun OnboardingScreen(
    client: VoitosApiClient,
    apiBaseUrl: String = VoitosApi.DEFAULT_BASE_URL,
    onBack: () -> Unit,
    /** Если true — нельзя закрыть, пока бэкенд не отметил обучение пройденным. */
    requireCompletion: Boolean = false,
    onFinished: (() -> Unit)? = null,
) {
    BackHandler(enabled = !requireCompletion) { onBack() }
    var progress by remember { mutableStateOf<OnboardingProgress?>(null) }
    var index by remember { mutableIntStateOf(0) }
    var error by remember { mutableStateOf<String?>(null) }
    var banner by remember { mutableStateOf<String?>(null) }
    var loading by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()
    val context = LocalContext.current

    fun isDone(p: OnboardingProgress?): Boolean =
        p != null && !p.requiresOnboarding()

    fun finish() {
        (onFinished ?: onBack).invoke()
    }

    fun reload() {
        scope.launch {
            loading = true
            error = null
            try {
                val p = client.onboarding()
                progress = p
                index = 0
                if (isDone(p)) {
                    if (requireCompletion) {
                        finish()
                        return@launch
                    }
                    banner = "Обучение уже пройдено. Можно просто полистать комиксы."
                }
            } catch (e: Exception) {
                error = e.message ?: "Не удалось загрузить обучение"
                // При обязательном онбординге не пропускаем вход из‑за сети.
            } finally {
                loading = false
            }
        }
    }

    LaunchedEffect(Unit) {
        reload()
    }

    val steps = progress?.steps.orEmpty()
    val step = steps.getOrNull(index)
    val total = (progress?.total?.takeIf { it > 0 } ?: steps.size).coerceAtLeast(1)
    val canClose = !requireCompletion || isDone(progress)

    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(16.dp)
            .verticalScroll(rememberScrollState()),
    ) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text("Обучение", style = MaterialTheme.typography.headlineSmall, color = VoitosColors.Text)
            if (canClose) {
                VoitosBackButton(onClick = { finish() }, label = "Закрыть")
            }
        }
        progress?.let {
            Text(
                "Комикс ${index + 1} из $total · пройдено ${it.doneCount}/$total",
                color = VoitosColors.Muted,
            )
        }
        banner?.let {
            Spacer(modifier = Modifier.height(8.dp))
            Text(it, color = VoitosColors.Accent2)
        }
        Spacer(modifier = Modifier.height(12.dp))
        error?.let { Text(it, color = VoitosColors.Danger) }
        if (step == null) {
            if (error != null) {
                Spacer(modifier = Modifier.height(12.dp))
                Button(
                    onClick = { reload() },
                    modifier = Modifier.fillMaxWidth(),
                    colors = voitosPrimaryButtonColors(),
                    enabled = !loading,
                ) { Text("Повторить загрузку") }
            } else if (error == null) {
                CircularProgressIndicator(
                    modifier = Modifier.align(Alignment.CenterHorizontally),
                    color = VoitosColors.Accent,
                )
                Text("Загрузка…", color = VoitosColors.Muted)
            }
        } else {
            Text(step.title, style = MaterialTheme.typography.titleLarge, color = VoitosColors.Text)
            Text(step.caption, color = VoitosColors.Text)
            Spacer(modifier = Modifier.height(8.dp))
            AsyncImage(
                model = onboardingImageModel(context, step, apiBaseUrl),
                contentDescription = step.title,
                modifier = Modifier
                    .fillMaxWidth()
                    .heightIn(min = 200.dp, max = 420.dp),
                contentScale = ContentScale.Fit,
            )
            if (step.done) {
                Text("✓ просмотрено", color = VoitosColors.Ok)
            }
            Spacer(modifier = Modifier.height(12.dp))
            Button(
                onClick = {
                    val alreadyDone = step.done && isDone(progress)
                    if (alreadyDone && index >= steps.lastIndex) {
                        finish()
                        return@Button
                    }
                    scope.launch {
                        loading = true
                        error = null
                        try {
                            val wasComplete = isDone(progress)
                            val res = client.completeOnboardingStep(step.code)
                            progress = res
                            if (res.rewardJustGranted || (!wasComplete && isDone(res))) {
                                banner =
                                    "Готово! Вам автоматически начислен месяц подписки. Спасибо за обучение."
                            }
                            val last = (res.steps.size - 1).coerceAtLeast(0)
                            if (index < last) {
                                index += 1
                            } else if (isDone(res)) {
                                if (banner.isNullOrBlank()) {
                                    banner = "Обучение пройдено (${res.doneCount}/${res.total})."
                                }
                                if (requireCompletion) {
                                    delay(600)
                                    finish()
                                }
                            }
                        } catch (e: Exception) {
                            error = friendlyNetworkError(e)
                        } finally {
                            loading = false
                        }
                    }
                },
                modifier = Modifier.fillMaxWidth(),
                enabled = !loading,
                colors = voitosPrimaryButtonColors(),
            ) {
                Text(
                    when {
                        loading -> "…"
                        !step.done -> "Понял · далее"
                        index < steps.lastIndex -> "Далее"
                        isDone(progress) -> if (requireCompletion) "Продолжить" else "Закрыть"
                        else -> "Готово"
                    },
                    color = VoitosColors.Text,
                )
            }
            Spacer(modifier = Modifier.height(4.dp))
            if (index > 0) {
                TextButton(
                    onClick = { index -= 1 },
                    enabled = !loading,
                    modifier = Modifier.fillMaxWidth(),
                ) { Text("← Предыдущий", color = VoitosColors.Accent) }
            }
            if (canClose && !requireCompletion) {
                VoitosBackButton(
                    onClick = { finish() },
                    modifier = Modifier.fillMaxWidth(),
                    label = "Закрыть онбординг",
                )
            }
        }
    }
}

private fun onboardingImageModel(
    context: android.content.Context,
    step: OnboardingStep,
    apiBaseUrl: String,
): ImageRequest {
    val assetName = step.asset.ifBlank {
        step.imageUrl.substringAfterLast('/').ifBlank { "${step.code}.jpg" }
    }
    val assetPath = "onboarding/$assetName"
    val fromAsset = try {
        context.assets.open(assetPath).close()
        true
    } catch (_: Exception) {
        false
    }
    val data: Any = if (fromAsset) {
        "file:///android_asset/$assetPath"
    } else {
        staticUrl(apiBaseUrl, step.imageUrl)
    }
    return ImageRequest.Builder(context).data(data).crossfade(true).build()
}

private fun staticUrl(apiBase: String, path: String): String {
    if (path.startsWith("http")) return path
    val root = apiBase.removeSuffix("/api/v1").trimEnd('/')
    return root + path
}
