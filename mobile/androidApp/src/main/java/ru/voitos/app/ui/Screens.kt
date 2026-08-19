package ru.voitos.app.ui

import android.util.Base64
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.Image
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
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
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
import androidx.compose.ui.graphics.ColorFilter
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import coil.compose.AsyncImage
import coil.request.ImageRequest
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

@Composable
fun LoginScreen(
    initialBaseUrl: String,
    initialPhone: String = "",
    initialDebugCode: String = "",
    recentBaseUrls: List<String> = emptyList(),
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
    var savedHint by remember { mutableStateOf<String?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    var loading by remember { mutableStateOf(false) }
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
            label = { Text("IP / хост ПК") },
            placeholder = { Text("192.168.101.9") },
            supportingText = { Text("Сохраняется на устройстве; при смене Wi‑Fi просто поправьте IP") },
            modifier = Modifier.fillMaxWidth(),
            singleLine = true,
        )
        Spacer(modifier = Modifier.height(4.dp))
        OutlinedTextField(
            value = portText,
            onValueChange = {
                portText = it.filter { ch -> ch.isDigit() }.take(5)
                persistDebug()
                savedHint = null
            },
            label = { Text("Порт") },
            modifier = Modifier.fillMaxWidth(),
            singleLine = true,
        )
        Text(
            currentBaseUrl(),
            style = MaterialTheme.typography.bodySmall,
            color = VoitosColors.Muted,
        )
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Button(
                onClick = {
                    val url = currentBaseUrl()
                    persistDebug()
                    onSaveServer(url)
                    savedHint = "Сохранено ✓ $url"
                    recent = listOf(url) + recent.filter { it != url }
                },
                modifier = Modifier.weight(1f),
                colors = androidx.compose.material3.ButtonDefaults.buttonColors(
                    containerColor = VoitosColors.Accent,
                ),
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
                enabled = !loading,
            ) { Text("Проверить") }
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
            label = { Text("Телефон") },
            modifier = Modifier.fillMaxWidth(),
            singleLine = true,
        )
        if (step >= 1) {
            Spacer(modifier = Modifier.height(8.dp))
            OutlinedTextField(
                value = code,
                onValueChange = { code = it },
                label = { Text("Код из SMS") },
                modifier = Modifier.fillMaxWidth(),
                singleLine = true,
            )
            debugHint?.let {
                Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.secondary)
            }
        }
        error?.let {
            Spacer(modifier = Modifier.height(8.dp))
            Text(it, color = MaterialTheme.colorScheme.error)
        }
        Spacer(modifier = Modifier.height(16.dp))
        if (loading) {
            CircularProgressIndicator(modifier = Modifier.align(Alignment.CenterHorizontally))
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
            ) { Text("Войти") }
            TextButton(onClick = { step = 0 }) { Text("Изменить номер") }
        }
    }
}

/** Понятный текст вместо Socket timeout / ConnectException. */
fun friendlyNetworkError(e: Throwable, fallback: String = "Ошибка сети"): String {
    val msg = (e.message ?: "").lowercase()
    val cause = (e.cause?.message ?: "").lowercase()
    val all = "$msg $cause"
    return when {
        "timeout" in all || "timed out" in all ->
            "Сервер не ответил вовремя. Проверьте, что бэкенд запущен и телефон в той же Wi‑Fi, " +
                "а в URL указан актуальный IP компьютера (не 10.0.2.2 на реальном телефоне)."
        "failed to connect" in all || "connection refused" in all || "connectexception" in all ->
            "Нет связи с сервером. Запущен ли Voitos на этом IP:порту? Телефон и ПК в одной сети?"
        "unable to resolve" in all || "unknownhost" in all ->
            "Не удалось найти хост. Проверьте API base URL."
        "notransformationfound" in all || "expected response body" in all ->
            "Сервер вернул неожиданный ответ (часто HTML 404). Обновите код бэкенда и перезапустите app.py."
        msg.isNotBlank() && msg.length < 220 -> e.message ?: fallback
        msg.isNotBlank() -> fallback
        else -> fallback
    }
}

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
        Text("Уведомления", style = MaterialTheme.typography.headlineSmall)
        Spacer(modifier = Modifier.height(8.dp))
        TextButton(onClick = onOpenCollections) { Text("Сборы") }
        TextButton(onClick = onOpenWorkRequests) { Text("Заявки") }
        TextButton(onClick = onNewWorkRequest) { Text("Вызвать мастера") }
        TextButton(onClick = onOpenSubscription) { Text("Подписка") }
        TextButton(onClick = onOpenOnboarding) { Text("Обучение") }
        TextButton(onClick = onLogout) { Text("Выйти") }
        Spacer(modifier = Modifier.height(8.dp))
        if (loading) {
            CircularProgressIndicator()
        }
        error?.let { Text(it, color = MaterialTheme.colorScheme.error) }
        LazyColumn(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            items(notifications, key = { it.id }) { n ->
                Card(
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
                        Text(n.title, style = MaterialTheme.typography.titleMedium)
                        if (n.body.isNotBlank()) {
                            Text(n.body, style = MaterialTheme.typography.bodySmall)
                        }
                        Text(n.type, style = MaterialTheme.typography.labelSmall)
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
    var executorProfiles by remember { mutableStateOf<List<ru.voitos.app.model.ExecutorProfileBrief>>(emptyList()) }
    var offers by remember { mutableStateOf<List<ru.voitos.app.model.ExecutorOfferBrief>>(emptyList()) }
    var error by remember { mutableStateOf<String?>(null) }
    var message by remember { mutableStateOf<String?>(null) }
    var loading by remember { mutableStateOf(true) }
    var loadingId by remember { mutableStateOf<Int?>(null) }
    var busyOfferId by remember { mutableStateOf<Int?>(null) }
    val scope = rememberCoroutineScope()

    fun reload() {
        scope.launch {
            loading = true
            error = null
            val errors = mutableListOf<String>()
            clientItems = runCatching { client.workRequests().items }.getOrElse {
                errors += friendlyNetworkError(it)
                emptyList()
            }
            val meEx = runCatching { client.executorMe() }.getOrNull()
            executorProfiles = meEx?.profiles.orEmpty()
            offers = if (meEx?.isExecutor == true) {
                runCatching { client.executorOffers().items }.getOrElse {
                    errors += friendlyNetworkError(it)
                    emptyList()
                }
            } else {
                emptyList()
            }
            error = errors.firstOrNull()
            loading = false
        }
    }

    LaunchedEffect(Unit) { reload() }

    val cancellable = setOf(
        "draft", "pending", "offering", "scheduling", "in_progress", "awaiting_client",
    )

    fun offersForProfile(profile: ru.voitos.app.model.ExecutorProfileBrief): List<ru.voitos.app.model.ExecutorOfferBrief> {
        return offers.filter { offer ->
            when {
                profile.roleId > 0 && offer.roleId > 0 -> offer.roleId == profile.roleId
                else -> offer.roleName.equals(profile.roleName, ignoreCase = true)
            }
        }
    }

    LazyColumn(
        modifier = Modifier.fillMaxSize().padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        item {
            if (onBack != null) {
                TextButton(onClick = onBack) { Text("← Назад") }
            }
            Text("Заявки", style = MaterialTheme.typography.headlineSmall, color = VoitosColors.Text)
            error?.let { Text(it, color = VoitosColors.Danger) }
            message?.let { Text(it, color = VoitosColors.Ok) }
            if (loading) {
                CircularProgressIndicator(
                    modifier = Modifier.padding(top = 8.dp),
                    color = VoitosColors.Accent,
                )
            }
        }

        if (executorProfiles.isNotEmpty()) {
            item {
                Text(
                    "Как исполнитель",
                    style = MaterialTheme.typography.titleMedium,
                    color = VoitosColors.Accent2,
                    modifier = Modifier.padding(top = 4.dp),
                )
            }
            items(executorProfiles, key = { "ex-${it.id}" }) { profile ->
                val roleOffers = offersForProfile(profile)
                PanelCard {
                    Text(profile.roleName, style = MaterialTheme.typography.titleMedium, color = VoitosColors.Text)
                    Text(
                        profile.statusLabel.ifBlank { profile.status },
                        color = VoitosColors.Muted,
                    )
                    Spacer(modifier = Modifier.height(8.dp))
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
                                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                    TextButton(
                                        onClick = {
                                            scope.launch {
                                                busyOfferId = offer.offerId
                                                error = null
                                                try {
                                                    val res = client.respondExecutorOffer(offer.offerId, accept = true)
                                                    message = res.message
                                                    reload()
                                                } catch (e: Exception) {
                                                    error = friendlyNetworkError(e)
                                                } finally {
                                                    busyOfferId = null
                                                }
                                            }
                                        },
                                        enabled = busyOfferId != offer.offerId,
                                    ) { Text("Беру", color = VoitosColors.Accent) }
                                    TextButton(
                                        onClick = {
                                            scope.launch {
                                                busyOfferId = offer.offerId
                                                error = null
                                                try {
                                                    val res = client.respondExecutorOffer(offer.offerId, accept = false)
                                                    message = res.message
                                                    reload()
                                                } catch (e: Exception) {
                                                    error = friendlyNetworkError(e)
                                                } finally {
                                                    busyOfferId = null
                                                }
                                            }
                                        },
                                        enabled = busyOfferId != offer.offerId,
                                    ) { Text("Отказ", color = VoitosColors.Danger) }
                                }
                            }
                        }
                    }
                }
            }
        }

        item {
            Text(
                if (executorProfiles.isNotEmpty()) "Как клиент" else "Мои заявки",
                style = MaterialTheme.typography.titleMedium,
                color = VoitosColors.Accent2,
                modifier = Modifier.padding(top = 8.dp),
            )
        }

        if (!loading && clientItems.isEmpty()) {
            item {
                Text("Пока нет ваших заявок", color = VoitosColors.Muted)
            }
        }

        items(clientItems, key = { "wr-${it.id}" }) { wr ->
            PanelCard {
                Column(
                    modifier = Modifier
                        .fillMaxWidth()
                        .clickable { onConfirm(wr.id) },
                ) {
                    Text("#${wr.id} ${wr.roleName}", style = MaterialTheme.typography.titleMedium, color = VoitosColors.Text)
                    Text(wr.status, color = VoitosColors.Muted)
                    wr.assignedExecutorName?.let { Text("Мастер: $it", color = VoitosColors.Muted) }
                    Text(wr.description, style = MaterialTheme.typography.bodySmall, color = VoitosColors.Text)
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

    Column(modifier = Modifier.padding(16.dp)) {
        TextButton(onClick = onBack) { Text("← Назад") }
        Text("Подтвердите сумму", style = MaterialTheme.typography.headlineSmall)
        Text("Заявка #$workRequestId")
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
                        error = e.message
                    } finally {
                        loading = false
                    }
                }
            },
            modifier = Modifier.fillMaxWidth(),
            enabled = !loading,
        ) { Text("Да, сумма верна") }
        Spacer(modifier = Modifier.height(8.dp))
        OutlinedTextField(
            value = amount,
            onValueChange = { amount = it },
            label = { Text("Своя сумма, ₽") },
            modifier = Modifier.fillMaxWidth(),
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
                        error = e.message
                    } finally {
                        loading = false
                    }
                }
            },
            modifier = Modifier.fillMaxWidth(),
            enabled = !loading,
        ) { Text("Отправить свою сумму") }
        message?.let { Text(it, color = MaterialTheme.colorScheme.primary) }
        error?.let { Text(it, color = MaterialTheme.colorScheme.error) }
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
                error = e.message
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
                error = e.message
            } finally {
                loading = false
            }
        }
    }

    Column(
        modifier = Modifier.fillMaxSize().padding(16.dp),
        verticalArrangement = Arrangement.Top,
    ) {
        TextButton(onClick = onBack) { Text("← Назад") }
        Text("Подписка", style = MaterialTheme.typography.headlineSmall)
        Spacer(modifier = Modifier.height(12.dp))
        error?.let { Text(it, color = MaterialTheme.colorScheme.error) }
        message?.let { Text(it, color = MaterialTheme.colorScheme.primary) }
        info?.let { s ->
            Text(s.label.ifBlank { s.state }, style = MaterialTheme.typography.titleMedium)
            Text("Доступ: ${s.state}")
            s.subscriptionUntil?.let { Text("До: $it") }
            s.graceUntil?.let { Text("Grace до: $it") }
            Spacer(modifier = Modifier.height(8.dp))
            Text("Цена: ${s.priceRub} ₽/мес")
            if (s.paymentPhone.isNotBlank()) {
                Text("Оплата: ${s.paymentPhone}")
                if (s.paymentName.isNotBlank()) Text(s.paymentName)
            }
            if (s.pendingReceipts > 0) {
                Text("Чеков на проверке: ${s.pendingReceipts}")
            }
            Spacer(modifier = Modifier.height(12.dp))
            Button(
                onClick = { picker.launch("image/*") },
                modifier = Modifier.fillMaxWidth(),
                enabled = !loading,
            ) { Text("Загрузить чек") }
            Button(
                onClick = {
                    scope.launch {
                        loading = true
                        error = null
                        try {
                            val tiny =
                                "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgNDRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/wAARCAABAAEDASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAn/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/8QAFQEBAQAAAAAAAAAAAAAAAAAAAAX/xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oADAMBAAIQAxAAAAGcP//EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAQUCf//EABQRAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQMBAT8Bf//EABQRAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQIBAT8Bf//Z"
                            val res = client.uploadReceipt(tiny, "dev-receipt.jpg")
                            message = "Тестовый чек #${res.id} на проверке"
                            reload()
                        } catch (e: Exception) {
                            error = e.message
                        } finally {
                            loading = false
                        }
                    }
                },
                modifier = Modifier.fillMaxWidth(),
                enabled = !loading,
            ) { Text("Тестовый чек (dev)") }
        }
        if (receipts.isNotEmpty()) {
            Spacer(modifier = Modifier.height(16.dp))
            Text("История чеков", style = MaterialTheme.typography.titleMedium)
            LazyColumn(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                items(receipts, key = { it.id }) { r ->
                    Column(modifier = Modifier.padding(vertical = 4.dp)) {
                        Text("#${r.id} · ${r.status}")
                        r.amount?.let { Text("$it ₽ · ${r.period}") }
                        Text(r.createdAt, style = MaterialTheme.typography.bodySmall)
                    }
                }
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
            TextButton(onClick = onBack) { Text("← Назад") }
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
            label = { Text("Что случилось") },
            modifier = Modifier.fillMaxWidth(),
            minLines = 3,
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
            colors = ButtonDefaults.buttonColors(containerColor = VoitosColors.Accent),
        ) { Text("Создать заявку") }
        message?.let { Text(it, color = VoitosColors.Ok) }
        error?.let { Text(it, color = VoitosColors.Danger) }
    }
}

@Composable
private fun RoleTileGrid(
    roles: List<ru.voitos.app.model.ExecutorRole>,
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
                // Fill empty cell if odd count
                if (rowRoles.size < columns) {
                    Spacer(modifier = Modifier.weight(1f))
                }
            }
        }
    }
}

@Composable
private fun RoleTileButton(
    role: ru.voitos.app.model.ExecutorRole,
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
        TextButton(onClick = onBack) { Text("← Назад") }
        Text("Фото заявки #$workRequestId", style = MaterialTheme.typography.headlineSmall)
        Text("Нужно хотя бы одно фото места работ.")
        Spacer(modifier = Modifier.height(12.dp))
        Button(
            onClick = { picker.launch("image/*") },
            modifier = Modifier.fillMaxWidth(),
            enabled = !loading,
        ) { Text("Выбрать фото") }
        Button(
            onClick = {
                scope.launch {
                    loading = true
                    error = null
                    try {
                        // Небольшой валидный JPEG (не 1×1), чтобы в админке было видно
                        val tiny =
                            "/9j/4AAQSkZJRgABAQAAAQABAAD/2wCEAAkGBxAQEBAQEBAPEBAQDw8PDw8PDw8PFRAVFREWFhUVFRUYHSggGBolGxUVITEhJSkrLi4uFx8zODMtNygtLisBCgoKDg0OGxAQGy0lHyUtLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLf/AABEIAAEAAQMBIgACEQEDEQH/xAAXAAADAQAAAAAAAAAAAAAAAAAAAQID/8QAFhEBAQEAAAAAAAAAAAAAAAAAAAER/8QAFQEBAQAAAAAAAAAAAAAAAAAAAAX/xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oADAMBAAIQAxAAAAGcP//EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAQUCf//EABQRAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQMBAT8Bf//EABQRAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQIBAT8Bf//Z"
                        val name = "test_${System.currentTimeMillis()}.jpg"
                        val res = client.addWorkRequestPhoto(workRequestId, tiny, name)
                        photoCount = res.photoCount
                        message = "Приложено фото: $photoCount"
                    } catch (e: Exception) {
                        error = e.message
                    } finally {
                        loading = false
                    }
                }
            },
            modifier = Modifier.fillMaxWidth(),
            enabled = !loading,
        ) { Text("Добавить тестовое фото") }
        Spacer(modifier = Modifier.height(8.dp))
        Text("Сейчас приложено: $photoCount")
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
        ) { Text("Готово — отправить") }
        if (loading) CircularProgressIndicator()
        message?.let { Text(it, color = MaterialTheme.colorScheme.primary) }
        error?.let { Text(it, color = MaterialTheme.colorScheme.error) }
    }
}

@Composable
fun OnboardingScreen(
    client: VoitosApiClient,
    apiBaseUrl: String = VoitosApi.DEFAULT_BASE_URL,
    onBack: () -> Unit,
) {
    var progress by remember { mutableStateOf<OnboardingProgress?>(null) }
    var index by remember { mutableIntStateOf(0) }
    var error by remember { mutableStateOf<String?>(null) }
    var banner by remember { mutableStateOf<String?>(null) }
    var loading by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()
    val context = LocalContext.current

    LaunchedEffect(Unit) {
        try {
            val p = client.onboarding()
            progress = p
            // Всегда начинаем с первого комикса — можно пересмотреть даже после MAX
            index = 0
            if (p.completed || p.rewardGranted) {
                banner = "Обучение уже пройдено. Можно просто полистать комиксы."
            }
        } catch (e: Exception) {
            error = e.message
        }
    }

    val steps = progress?.steps.orEmpty()
    val step = steps.getOrNull(index)
    val total = (progress?.total?.takeIf { it > 0 } ?: steps.size).coerceAtLeast(1)

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
            TextButton(onClick = onBack) { Text("Закрыть", color = VoitosColors.Accent) }
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
        error?.let { Text(it, color = MaterialTheme.colorScheme.error) }
        if (step == null) {
            if (error == null) {
                CircularProgressIndicator(modifier = Modifier.align(Alignment.CenterHorizontally))
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
                Text("✓ просмотрено", color = MaterialTheme.colorScheme.primary)
            }
            Spacer(modifier = Modifier.height(12.dp))
            Button(
                onClick = {
                    val alreadyDone = step.done && (progress?.completed == true || progress?.rewardGranted == true)
                    if (alreadyDone && index >= steps.lastIndex) {
                        onBack()
                        return@Button
                    }
                    scope.launch {
                        loading = true
                        error = null
                        try {
                            val wasComplete = progress?.completed == true || progress?.rewardGranted == true
                            val res = client.completeOnboardingStep(step.code)
                            progress = res
                            if (res.rewardJustGranted || (!wasComplete && res.rewardGranted && res.completed)) {
                                banner =
                                    "Готово! Вам автоматически начислен месяц подписки. Спасибо за обучение."
                            }
                            val last = (res.steps.size - 1).coerceAtLeast(0)
                            if (index < last) {
                                index += 1
                            } else if (res.completed || res.rewardGranted) {
                                if (banner.isNullOrBlank()) {
                                    banner = "Обучение пройдено (${res.doneCount}/${res.total})."
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
            ) {
                Text(
                    when {
                        loading -> "…"
                        !step.done -> "Понял · далее"
                        index < steps.lastIndex -> "Далее"
                        progress?.completed == true || progress?.rewardGranted == true -> "Закрыть"
                        else -> "Готово"
                    },
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
                ) { Text("← Предыдущий") }
                TextButton(
                    onClick = { if (index < steps.lastIndex) index += 1 },
                    enabled = index < steps.lastIndex && !loading,
                ) { Text("Следующий →") }
            }
            TextButton(
                onClick = onBack,
                modifier = Modifier.fillMaxWidth(),
            ) { Text("Закрыть онбординг") }
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
