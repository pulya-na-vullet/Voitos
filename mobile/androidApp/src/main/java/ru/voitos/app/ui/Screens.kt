package ru.voitos.app.ui

import android.util.Base64
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
import ru.voitos.app.nav.DeepLinks
import ru.voitos.app.ui.theme.VoitosColors
import ru.voitos.app.ui.theme.voitosPrimaryButtonColors
import ru.voitos.app.ui.theme.voitosSecondaryButtonColors
import ru.voitos.app.ui.theme.voitosAccent2ButtonColors

@Composable
fun LoginScreen(
    initialBaseUrl: String,
    initialPhone: String = "",
    initialDebugCode: String = "",
    recentBaseUrls: List<String> = emptyList(),
    restoredFromDisk: Boolean = false,
    onLoggedIn: (token: String, name: String, baseUrl: String, phone: String) -> Unit,
    onDebugPrefs: (baseUrl: String, phone: String, debugCode: String) -> Unit = { _, _, _ -> },
    onSaveServer: (baseUrl: String) -> Unit = {},
) {
    val initialHp = remember(initialBaseUrl) { ru.voitos.app.DevServerSettings.parse(initialBaseUrl) }
    var host by remember { mutableStateOf(initialHp.host) }
    var portText by remember { mutableStateOf(initialHp.port.toString()) }
    var phone by remember { mutableStateOf(initialPhone) }
    var code by remember { mutableStateOf(initialDebugCode) }
    var debugHint by remember { mutableStateOf<String?>(null) }
    var healthHint by remember { mutableStateOf<String?>(null) }
    var savedHint by remember {
        mutableStateOf(
            if (restoredFromDisk) "IP восстановлен из файла на телефоне (после переустановки)" else null,
        )
    }
    var error by remember { mutableStateOf<String?>(null) }
    var loading by remember { mutableStateOf(false) }
    var scanning by remember { mutableStateOf(false) }
    var step by remember { mutableStateOf(0) }
    var recent by remember { mutableStateOf(recentBaseUrls) }
    val scope = rememberCoroutineScope()

    fun currentBaseUrl(): String {
        val port = portText.toIntOrNull() ?: 18765
        return ru.voitos.app.DevServerSettings.HostPort(
            host = host.trim().ifBlank { "10.0.2.2" },
            port = port,
        ).toBaseUrl()
    }

    fun client(): VoitosApiClient = VoitosApiClient(baseUrl = currentBaseUrl())

    fun persistDebug(debugCode: String = "") {
        onDebugPrefs(currentBaseUrl(), phone.trim(), debugCode)
    }

    fun applySavedUrl(url: String) {
        val hp = ru.voitos.app.DevServerSettings.parse(url)
        host = hp.host
        portText = hp.port.toString()
        persistDebug()
        onSaveServer(currentBaseUrl())
        savedHint = "Сохранено: ${currentBaseUrl()}"
        recent = listOf(currentBaseUrl()) + recent.filter { it != currentBaseUrl() }
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(24.dp),
        verticalArrangement = Arrangement.Center,
    ) {
        Text("Voitos", style = MaterialTheme.typography.headlineLarge, color = MaterialTheme.colorScheme.secondary)
        Text("Вход по телефону", style = MaterialTheme.typography.bodyMedium, color = VoitosColors.Text)
        Spacer(modifier = Modifier.height(8.dp))
        VpnDebugBanner()
        Spacer(modifier = Modifier.height(8.dp))
        Text("Сервер в локальной сети", color = VoitosColors.Accent2, style = MaterialTheme.typography.titleSmall)
        OutlinedTextField(
            value = host,
            onValueChange = {
                host = it
                persistDebug()
                savedHint = null
            },
            label = { Text("IP / хост ПК", color = VoitosColors.Muted) },
            placeholder = { Text("192.168.101.9", color = VoitosColors.Muted) },
            supportingText = {
                Text("«Сохранить» пишет в Загрузки — IP останется после удаления APK. Или нажмите «Найти в Wi‑Fi».")
            },
            modifier = Modifier.fillMaxWidth(),
            singleLine = true,
            colors = ru.voitos.app.ui.theme.voitosOutlinedFieldColors(),
        )
        Spacer(modifier = Modifier.height(4.dp))
        OutlinedTextField(
            value = portText,
            onValueChange = {
                portText = it.filter { ch -> ch.isDigit() }.take(5)
                persistDebug()
                savedHint = null
            },
            label = { Text("Порт", color = VoitosColors.Muted) },
            modifier = Modifier.fillMaxWidth(),
            singleLine = true,
            colors = ru.voitos.app.ui.theme.voitosOutlinedFieldColors(),
        )
        Text(
            currentBaseUrl(),
            style = MaterialTheme.typography.bodySmall,
            color = VoitosColors.Muted,
        )
        Spacer(modifier = Modifier.height(6.dp))
        Button(
            onClick = {
                scope.launch {
                    scanning = true
                    error = null
                    healthHint = null
                    try {
                        val port = portText.toIntOrNull() ?: 18765
                        val prefer = buildList {
                            add(currentBaseUrl())
                            addAll(recent)
                        }
                        val hit = ru.voitos.app.LanServerDiscovery.findFirst(
                            port = port,
                            preferHosts = prefer,
                        )
                        if (hit == null) {
                            error = "Сервер в Wi‑Fi не найден. Проверьте, что app.py запущен и телефон в той же сети (без VPN)."
                        } else {
                            host = hit.host
                            portText = hit.port.toString()
                            persistDebug()
                            onSaveServer(hit.baseUrl)
                            savedHint = "Найден и сохранён: ${hit.baseUrl}"
                            healthHint = "Сервер отвечает ✓"
                            recent = listOf(hit.baseUrl) + recent.filter { it != hit.baseUrl }
                        }
                    } catch (e: Exception) {
                        error = friendlyNetworkError(e)
                    } finally {
                        scanning = false
                    }
                }
            },
            modifier = Modifier.fillMaxWidth(),
            enabled = !loading && !scanning,
            colors = voitosAccent2ButtonColors(),
        ) {
            Text(if (scanning) "Ищем сервер в Wi‑Fi…" else "Найти сервер в Wi‑Fi")
        }
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Button(
                onClick = {
                    val url = currentBaseUrl()
                    persistDebug()
                    onSaveServer(url)
                    savedHint = "Сохранено в Загрузки ✓ $url"
                    recent = listOf(url) + recent.filter { it != url }
                },
                modifier = Modifier.weight(1f),
                colors = voitosPrimaryButtonColors(),
            ) { Text("Сохранить адрес") }
            TextButton(
                onClick = {
                    scope.launch {
                        loading = true
                        error = null
                        try {
                            persistDebug()
                            onSaveServer(currentBaseUrl())
                            val ok = client().health()
                            healthHint = if (ok) "Сервер отвечает ✓" else "Сервер ответил без ok"
                        } catch (e: Exception) {
                            healthHint = null
                            error = friendlyNetworkError(e)
                        } finally {
                            loading = false
                        }
                    }
                },
                enabled = !loading && !scanning,
            ) { Text("Проверить", color = VoitosColors.Accent) }
        }
        savedHint?.let {
            Text(it, color = VoitosColors.Ok, style = MaterialTheme.typography.bodySmall)
        }
        healthHint?.let {
            Text(it, color = MaterialTheme.colorScheme.primary, style = MaterialTheme.typography.bodySmall)
        }
        if (recent.isNotEmpty()) {
            Spacer(modifier = Modifier.height(4.dp))
            Text("Недавние", color = VoitosColors.Muted, style = MaterialTheme.typography.labelMedium)
            recent.take(5).forEach { url ->
                val hp = ru.voitos.app.DevServerSettings.parse(url)
                TextButton(
                    onClick = { applySavedUrl(url) },
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    Text("${hp.host}:${hp.port}", color = VoitosColors.Accent2)
                }
            }
        }
        Spacer(modifier = Modifier.height(8.dp))
        OutlinedTextField(
            value = phone,
            onValueChange = {
                phone = it
                persistDebug()
            },
            label = { Text("Телефон", color = VoitosColors.Muted) },
            modifier = Modifier.fillMaxWidth(),
            singleLine = true,
            colors = ru.voitos.app.ui.theme.voitosOutlinedFieldColors(),
        )
        if (step >= 1) {
            Spacer(modifier = Modifier.height(8.dp))
            OutlinedTextField(
                value = code,
                onValueChange = { code = it },
                label = { Text("Код из SMS", color = VoitosColors.Muted) },
                modifier = Modifier.fillMaxWidth(),
                singleLine = true,
            colors = ru.voitos.app.ui.theme.voitosOutlinedFieldColors(),
        )
            debugHint?.let {
                Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.secondary)
            }
        }
        error?.let {
            Spacer(modifier = Modifier.height(8.dp))
            Text(it, color = VoitosColors.Danger)
        }
        Spacer(modifier = Modifier.height(16.dp))
        if (loading || scanning) {
            CircularProgressIndicator(
                modifier = Modifier.align(Alignment.CenterHorizontally),
                color = VoitosColors.Accent,
            )
        } else if (step == 0) {
            Button(
                onClick = {
                    scope.launch {
                        loading = true
                        error = null
                        try {
                            persistDebug()
                            onSaveServer(currentBaseUrl())
                            val res = client().phoneStart(phone)
                            val dbg = res["debug_code"].orEmpty()
                            if (dbg.isNotBlank()) {
                                code = dbg
                                debugHint = "Dev-код: $dbg"
                                persistDebug(dbg)
                            }
                            step = 1
                        } catch (e: Exception) {
                            error = friendlyNetworkError(e)
                        } finally {
                            loading = false
                        }
                    }
                },
                modifier = Modifier.fillMaxWidth(),
                colors = voitosPrimaryButtonColors(),
            ) { Text("Получить код") }
        } else {
            Button(
                onClick = {
                    scope.launch {
                        loading = true
                        error = null
                        try {
                            persistDebug(code)
                            onSaveServer(currentBaseUrl())
                            val session = client().phoneVerify(phone, code)
                            onLoggedIn(
                                session.accessToken,
                                session.displayName,
                                currentBaseUrl(),
                                phone.trim(),
                            )
                        } catch (e: Exception) {
                            error = friendlyNetworkError(e, fallback = "Неверный код")
                        } finally {
                            loading = false
                        }
                    }
                },
                modifier = Modifier.fillMaxWidth(),
                colors = voitosPrimaryButtonColors(),
            ) { Text("Войти") }
            TextButton(onClick = { step = 0 }) { Text("Изменить номер", color = VoitosColors.Accent) }
        }
    }
}

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
    onBack: (() -> Unit)? = null,
) {
    var clientItems by remember { mutableStateOf<List<WorkRequestBrief>>(emptyList()) }
    var error by remember { mutableStateOf<String?>(null) }
    var message by remember { mutableStateOf<String?>(null) }
    var loading by remember { mutableStateOf(true) }
    var loadingId by remember { mutableStateOf<Int?>(null) }
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

    val cancellable = setOf(
        "draft", "pending", "offering", "scheduling", "in_progress", "awaiting_client",
    )

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
                "Статусы обновляются автоматически: поиск → согласование времени → в работе.",
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

        if (!loading) {
            items(clientItems, key = { "wr-${it.id}" }) { wr ->
            PanelCard {
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
                    wr.assignedExecutorName?.let {
                        Text("Мастер: $it", color = VoitosColors.Muted)
                    }
                    if (wr.agreedSlot.isNotBlank()) {
                        Text("Время: ${wr.agreedSlot}", color = VoitosColors.Muted)
                    }
                    Text(wr.description, style = MaterialTheme.typography.bodySmall, color = VoitosColors.Text)

                    if (wr.status == "scheduling") {
                        Spacer(modifier = Modifier.height(8.dp))
                        Text(
                            "Согласуйте время с мастером",
                            color = VoitosColors.Accent2,
                            style = MaterialTheme.typography.labelLarge,
                        )
                        if (wr.proposedSlots.isNotEmpty()) {
                            wr.proposedSlots.forEach { slot ->
                                TextButton(
                                    onClick = {
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
                                    enabled = loadingId != wr.id,
                                ) { Text("Выбрать: $slot", color = VoitosColors.Accent2) }
                            }
                        } else {
                            Text(
                                "Мастер ещё не прислал окна — можно подтвердить, если уже договорились.",
                                color = VoitosColors.Muted,
                                style = MaterialTheme.typography.bodySmall,
                            )
                            Button(
                                onClick = {
                                    scope.launch {
                                        loadingId = wr.id
                                        error = null
                                        try {
                                            val res = client.confirmWorkRequestSlot(wr.id, "")
                                            message = res.message.ifBlank {
                                                "Заявка переведена «В работе»"
                                            }
                                            reload()
                                        } catch (e: Exception) {
                                            error = friendlyNetworkError(e)
                                        } finally {
                                            loadingId = null
                                        }
                                    }
                                },
                                enabled = loadingId != wr.id,
                                colors = voitosPrimaryButtonColors(),
                            ) { Text("Время согласовано → в работе") }
                        }
                    }

                    if (wr.needsConfirmAmount || wr.status == "awaiting_client") {
                        Spacer(modifier = Modifier.height(6.dp))
                        Button(
                            onClick = { onConfirm(wr.id) },
                            colors = voitosPrimaryButtonColors(),
                        ) { Text("Подтвердить сумму") }
                    }

                    if (wr.status in cancellable) {
                        TextButton(
                            onClick = {
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
                            enabled = loadingId != wr.id,
                        ) { Text("Отменить заявку", color = VoitosColors.Danger) }
                    }
                }
            }
            }
        }
    }
}

private fun workRequestStatusLabel(status: String): String = when (status) {
    "draft" -> "Черновик"
    "pending" -> "Новая"
    "offering" -> "Ищем исполнителя"
    "scheduling" -> "Согласование времени"
    "in_progress" -> "В работе"
    "awaiting_client" -> "Ждём подтверждения"
    "awaiting_commission" -> "Ждём комиссию"
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
    onDone: () -> Unit,
    onBack: () -> Unit,
) {
    var amount by remember { mutableStateOf("") }
    var message by remember { mutableStateOf<String?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    var loading by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()

    Column(modifier = Modifier.fillMaxSize().padding(16.dp)) {
        VoitosBackButton(onClick = onBack)
        Text("Подтвердите сумму", style = MaterialTheme.typography.headlineSmall, color = VoitosColors.Text)
        Text("Заявка #$workRequestId", color = VoitosColors.Muted)
        Spacer(modifier = Modifier.height(12.dp))
        Button(
            onClick = {
                scope.launch {
                    loading = true
                    error = null
                    try {
                        client.confirmAmount(workRequestId, confirmed = true)
                        message = "Сумма подтверждена"
                        onDone()
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
        ) { Text("Да, сумма верна") }
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
                        client.confirmAmount(workRequestId, confirmed = false, amount = v)
                        message = "Сохранено: $v ₽"
                        onDone()
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
fun SubscriptionScreen(
    client: VoitosApiClient,
    onBack: () -> Unit,
) {
    var info by remember { mutableStateOf<ru.voitos.app.model.SubscriptionInfo?>(null) }
    var receipts by remember { mutableStateOf<List<ru.voitos.app.model.ReceiptBrief>>(emptyList()) }
    var error by remember { mutableStateOf<String?>(null) }
    var message by remember { mutableStateOf<String?>(null) }
    var loading by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()
    val context = LocalContext.current

    fun reload() {
        scope.launch {
            try {
                info = client.subscription()
                receipts = client.receipts().items
            } catch (e: Exception) {
                error = friendlyNetworkError(e)
            }
        }
    }

    LaunchedEffect(Unit) { reload() }

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
                message = "Чек #${res.id} отправлен на проверку"
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
        VoitosBackButton(onClick = onBack)
        Text("Подписка", style = MaterialTheme.typography.headlineSmall, color = VoitosColors.Text)
        Spacer(modifier = Modifier.height(12.dp))
        error?.let { Text(it, color = VoitosColors.Danger) }
        message?.let { Text(it, color = VoitosColors.Ok) }
        info?.let { s ->
            PanelCard {
                Text(
                    s.label.ifBlank { s.state },
                    style = MaterialTheme.typography.titleMedium,
                    color = VoitosColors.Accent2,
                )
                Spacer(modifier = Modifier.height(8.dp))
                Text("Доступ: ${s.state}", color = VoitosColors.Text)
                s.subscriptionUntil?.let { Text("До: $it", color = VoitosColors.Text) }
                s.graceUntil?.let { Text("Grace до: $it", color = VoitosColors.Muted) }
                Spacer(modifier = Modifier.height(8.dp))
                Text("Цена: ${s.priceRub} ₽/мес", color = VoitosColors.Text)
            }
            if (s.paymentPhone.isNotBlank() || s.paymentName.isNotBlank() || s.family.members.isNotEmpty()) {
                Spacer(modifier = Modifier.height(12.dp))
                PanelCard {
                    Text("Оплата и семья", style = MaterialTheme.typography.titleMedium, color = VoitosColors.Accent2)
                    Spacer(modifier = Modifier.height(8.dp))
                    if (s.paymentPhone.isNotBlank()) {
                        Text("Телефон: ${s.paymentPhone}", color = VoitosColors.Text)
                    }
                    if (s.paymentName.isNotBlank()) {
                        Text("Получатель: ${s.paymentName}", color = VoitosColors.Text)
                    }
                    if (s.pendingReceipts > 0) {
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
            }
            Spacer(modifier = Modifier.height(12.dp))
            Button(
                onClick = { picker.launch("image/*") },
                modifier = Modifier.fillMaxWidth(),
                enabled = !loading,
                colors = voitosPrimaryButtonColors(),
            ) { Text("Загрузить чек") }
        }
        if (receipts.isNotEmpty()) {
            Spacer(modifier = Modifier.height(16.dp))
            Text("История чеков", style = MaterialTheme.typography.titleMedium, color = VoitosColors.Accent2)
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
    val scope = rememberCoroutineScope()

    LaunchedEffect(Unit) {
        try {
            roles = client.executorRoles().items
        } catch (e: Exception) {
            error = friendlyNetworkError(e)
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
        Text("Выберите роль", color = VoitosColors.Muted)
        Spacer(modifier = Modifier.height(12.dp))

        RoleTileGrid(
            roles = roles,
            selectedId = selectedId,
            onSelect = { selectedId = it },
        )

        Spacer(modifier = Modifier.height(16.dp))
        OutlinedTextField(
            value = description,
            onValueChange = { description = it },
            label = { Text("Что случилось", color = VoitosColors.Muted) },
            modifier = Modifier.fillMaxWidth(),
            minLines = 3,
            colors = ru.voitos.app.ui.theme.voitosOutlinedFieldColors(),
        )
        Spacer(modifier = Modifier.height(8.dp))
        Button(
            onClick = {
                val rid = selectedId
                if (rid == null) {
                    error = "Выберите роль"
                    return@Button
                }
                scope.launch {
                    loading = true
                    error = null
                    try {
                        val created = client.createWorkRequest(rid, description)
                        message = if (created.needsPhotos) {
                            "Заявка #${created.id}: добавьте фото"
                        } else {
                            "Заявка #${created.id} отправлена мастерам"
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
            enabled = !loading && description.length >= 5 && selectedId != null,
            colors = voitosPrimaryButtonColors(),
        ) { Text("Создать заявку") }
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
                val bytes = context.contentResolver.openInputStream(uri)?.use { it.readBytes() }
                    ?: throw IllegalStateException("Не удалось прочитать файл")
                val b64 = Base64.encodeToString(bytes, Base64.NO_WRAP)
                val mime = context.contentResolver.getType(uri).orEmpty()
                val ext = when {
                    mime.contains("png") -> "png"
                    mime.contains("webp") -> "webp"
                    mime.contains("heic") || mime.contains("heif") -> "heic"
                    else -> "jpg"
                }
                val name = "gallery_${System.currentTimeMillis()}.$ext"
                val res = client.addWorkRequestPhoto(workRequestId, b64, name)
                photoCount = res.photoCount
                message = "Приложено фото: $photoCount"
            } catch (e: Exception) {
                error = e.message
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
    var progress by remember { mutableStateOf<OnboardingProgress?>(null) }
    var index by remember { mutableIntStateOf(0) }
    var error by remember { mutableStateOf<String?>(null) }
    var banner by remember { mutableStateOf<String?>(null) }
    var loading by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()
    val context = LocalContext.current

    fun isDone(p: OnboardingProgress?): Boolean =
        p != null && (p.completed || p.rewardGranted)

    fun finish() {
        (onFinished ?: onBack).invoke()
    }

    LaunchedEffect(Unit) {
        try {
            val p = client.onboarding()
            progress = p
            index = 0
            if (isDone(p)) {
                if (requireCompletion) {
                    finish()
                    return@LaunchedEffect
                }
                banner = "Обучение уже пройдено. Можно просто полистать комиксы."
            }
        } catch (e: Exception) {
            error = e.message
            if (requireCompletion) {
                // Сеть упала — не блокируем вход навсегда.
                finish()
            }
        }
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
            if (error == null) {
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
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
            ) {
                TextButton(
                    onClick = { if (index > 0) index -= 1 },
                    enabled = index > 0 && !loading,
                ) { Text("← Предыдущий", color = VoitosColors.Accent) }
                TextButton(
                    onClick = { if (index < steps.lastIndex) index += 1 },
                    enabled = index < steps.lastIndex && !loading,
                ) { Text("Следующий →", color = VoitosColors.Accent) }
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
