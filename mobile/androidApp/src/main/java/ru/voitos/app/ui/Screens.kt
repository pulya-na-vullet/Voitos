package ru.voitos.app.ui

import android.util.Base64
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
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
import androidx.compose.ui.Modifier.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.launch
import androidx.compose.foundation.layout.heightIn
import androidx.compose.ui.layout.ContentScale
import coil.compose.AsyncImage
import ru.voitos.app.VoitosApi
import ru.voitos.app.api.VoitosApiClient
import ru.voitos.app.model.AppNotification
import ru.voitos.app.model.CollectionBrief
import ru.voitos.app.model.OnboardingProgress
import ru.voitos.app.model.WorkRequestBrief
import ru.voitos.app.nav.DeepLinks

@Composable
fun LoginScreen(
    client: VoitosApiClient,
    onLoggedIn: (token: String, name: String) -> Unit,
) {
    var phone by remember { mutableStateOf("") }
    var code by remember { mutableStateOf("") }
    var debugHint by remember { mutableStateOf<String?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    var loading by remember { mutableStateOf(false) }
    var step by remember { mutableStateOf(0) }
    val scope = rememberCoroutineScope()

    Column(
        modifier = Modifier.fillMaxSize().padding(24.dp),
        verticalArrangement = Arrangement.Center,
    ) {
        Text("Voitos", style = MaterialTheme.typography.headlineLarge)
        Text("Вход по телефону", style = MaterialTheme.typography.bodyMedium)
        Spacer(Modifier.height(16.dp))
        OutlinedTextField(
            value = phone,
            onValueChange = { phone = it },
            label = { Text("Телефон") },
            modifier = Modifier.fillMaxWidth(),
            singleLine = true,
        )
        if (step >= 1) {
            Spacer(Modifier.height(8.dp))
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
            Spacer(Modifier.height(8.dp))
            Text(it, color = MaterialTheme.colorScheme.error)
        }
        Spacer(Modifier.height(16.dp))
        if (loading) {
            CircularProgressIndicator(modifier = Modifier.align(Alignment.CenterHorizontally))
        } else if (step == 0) {
            Button(
                onClick = {
                    scope.launch {
                        loading = true
                        error = null
                        try {
                            val res = client.phoneStart(phone)
                            debugHint = res["debug_code"]?.let { "Dev-код: $it" }
                            step = 1
                        } catch (e: Exception) {
                            error = e.message ?: "Ошибка"
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
                            val session = client.phoneVerify(phone, code)
                            onLoggedIn(session.accessToken, session.displayName)
                        } catch (e: Exception) {
                            error = e.message ?: "Неверный код"
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
    var items by remember { mutableStateOf<List<AppNotification>>(emptyList()) }
    var error by remember { mutableStateOf<String?>(null) }
    var loading by remember { mutableStateOf(true) }
    val scope = rememberCoroutineScope()

    fun reload() {
        scope.launch {
            loading = true
            error = null
            try {
                items = client.notifications().items
            } catch (e: Exception) {
                error = e.message
            } finally {
                loading = false
            }
        }
    }

    LaunchedEffect(Unit) { reload() }

    Column(Modifier = Modifier.fillMaxSize().padding(16.dp)) {
        Text("Уведомления", style = MaterialTheme.typography.headlineSmall)
        Spacer(Modifier.height(8.dp))
        TextButton(onClick = onOpenCollections) { Text("Сборы") }
        TextButton(onClick = onOpenWorkRequests) { Text("Заявки") }
        TextButton(onClick = onNewWorkRequest) { Text("Вызвать мастера") }
        TextButton(onClick = onOpenSubscription) { Text("Подписка") }
        TextButton(onClick = onOpenOnboarding) { Text("Обучение") }
        TextButton(onClick = onLogout) { Text("Выйти") }
        Spacer(Modifier.height(8.dp))
        if (loading) CircularProgressIndicator()
        error?.let { Text(it, color = MaterialTheme.colorScheme.error) }
        LazyColumn(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            items(items, key = { it.id }) { n ->
                Card(
                    modifier = Modifier
                        .fillMaxWidth()
                        .clickable {
                            val fallback = when (DeepLinks.routeForType(n.type, n.entityId)) {
                                is DeepLinks.Route.Subscription -> "voitos://app/subscription"
                                is DeepLinks.Route.Collection -> "voitos://app/collections/${n.entityId}"
                                is DeepLinks.Route.WorkRequest -> {
                                    val action = (DeepLinks.routeForType(n.type, n.entityId) as DeepLinks.Route.WorkRequest).action
                                    if (action == "confirm") {
                                        "voitos://app/work-requests/${n.entityId}/confirm"
                                    } else {
                                        "voitos://app/work-requests/${n.entityId}"
                                    }
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
fun CollectionsScreen(
    client: VoitosApiClient,
    onOpen: (Int) -> Unit = {},
    onBack: () -> Unit,
) {
    var items by remember { mutableStateOf<List<CollectionBrief>>(emptyList()) }
    var error by remember { mutableStateOf<String?>(null) }
    LaunchedEffect(Unit) {
        try {
            items = client.collections().items
        } catch (e: Exception) {
            error = e.message
        }
    }
    Column(modifier = Modifier.padding(16.dp)) {
        TextButton(onClick = onBack) { Text("← Назад") }
        Text("Сборы", style = MaterialTheme.typography.headlineSmall)
        error?.let { Text(it, color = MaterialTheme.colorScheme.error) }
        LazyColumn(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            items(items, key = { it.id }) { c ->
                Card(
                    modifier = Modifier
                        .fillMaxWidth()
                        .clickable { onOpen(c.id) },
                ) {
                    Column(modifier = Modifier.padding(12.dp)) {
                        Text(c.title, style = MaterialTheme.typography.titleMedium)
                        Text("${c.category} · ${c.amountDue} ₽ · ${c.status}")
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
    onBack: () -> Unit,
) {
    var items by remember { mutableStateOf<List<WorkRequestBrief>>(emptyList()) }
    var error by remember { mutableStateOf<String?>(null) }
    LaunchedEffect(Unit) {
        try {
            items = client.workRequests().items
        } catch (e: Exception) {
            error = e.message
        }
    }
    Column(modifier = Modifier.padding(16.dp)) {
        TextButton(onClick = onBack) { Text("← Назад") }
        Text("Заявки", style = MaterialTheme.typography.headlineSmall)
        error?.let { Text(it, color = MaterialTheme.colorScheme.error) }
        LazyColumn(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            items(items, key = { it.id }) { wr ->
                Card(
                    modifier = Modifier
                        .fillMaxWidth()
                        .clickable { onConfirm(wr.id) },
                ) {
                    Column(modifier = Modifier.padding(12.dp)) {
                        Text("#${wr.id} ${wr.roleName}", style = MaterialTheme.typography.titleMedium)
                        Text(wr.status)
                        wr.assignedExecutorName?.let { Text("Мастер: $it") }
                        Text(wr.description, style = MaterialTheme.typography.bodySmall)
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
        Spacer(Modifier.height(12.dp))
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
        Spacer(Modifier.height(8.dp))
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

    Column(modifier = Modifier.fillMaxSize().padding(16.dp)) {
        TextButton(onClick = onBack) { Text("← Назад") }
        Text("Подписка", style = MaterialTheme.typography.headlineSmall)
        Spacer(Modifier.height(12.dp))
        error?.let { Text(it, color = MaterialTheme.colorScheme.error) }
        message?.let { Text(it, color = MaterialTheme.colorScheme.primary) }
        info?.let { s ->
            Text(s.label.ifBlank { s.state }, style = MaterialTheme.typography.titleMedium)
            Text("Доступ: ${s.state}")
            s.subscriptionUntil?.let { Text("До: $it") }
            s.graceUntil?.let { Text("Grace до: $it") }
            Spacer(Modifier.height(8.dp))
            Text("Цена: ${s.priceRub} ₽/мес")
            if (s.paymentPhone.isNotBlank()) {
                Text("Оплата: ${s.paymentPhone}")
                if (s.paymentName.isNotBlank()) Text(s.paymentName)
            }
            if (s.pendingReceipts > 0) {
                Text("Чеков на проверке: ${s.pendingReceipts}")
            }
            Spacer(Modifier.height(12.dp))
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
            Spacer(Modifier.height(16.dp))
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
    onBack: () -> Unit,
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
            selectedId = roles.firstOrNull()?.id
        } catch (e: Exception) {
            error = e.message
        }
    }

    Column(modifier = Modifier.fillMaxSize().padding(16.dp)) {
        TextButton(onClick = onBack) { Text("← Назад") }
        Text("Вызов мастера", style = MaterialTheme.typography.headlineSmall)
        Spacer(Modifier.height(8.dp))
        Text("Роль", style = MaterialTheme.typography.labelLarge)
        roles.forEach { role ->
            TextButton(
                onClick = { selectedId = role.id },
                modifier = Modifier.fillMaxWidth(),
            ) {
                Text(
                    buildString {
                        append(if (selectedId == role.id) "✓ " else "")
                        append(role.name)
                        if (!role.requiresWorkPhotos) append(" · без фото")
                    },
                )
            }
        }
        OutlinedTextField(
            value = description,
            onValueChange = { description = it },
            label = { Text("Что случилось") },
            modifier = Modifier.fillMaxWidth(),
            minLines = 3,
        )
        Spacer(Modifier.height(8.dp))
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
                        error = e.message
                    } finally {
                        loading = false
                    }
                }
            },
            modifier = Modifier.fillMaxWidth(),
            enabled = !loading && description.length >= 5,
        ) { Text("Создать заявку") }
        message?.let { Text(it, color = MaterialTheme.colorScheme.primary) }
        error?.let { Text(it, color = MaterialTheme.colorScheme.error) }
    }
}

@Composable
fun CollectionDetailScreen(
    client: VoitosApiClient,
    collectionId: Int,
    onBack: () -> Unit,
) {
    var detail by remember { mutableStateOf<ru.voitos.app.model.CollectionDetail?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    LaunchedEffect(collectionId) {
        try {
            detail = client.collection(collectionId)
        } catch (e: Exception) {
            error = e.message
        }
    }
    Column(modifier = Modifier.fillMaxSize().padding(16.dp)) {
        TextButton(onClick = onBack) { Text("← Назад") }
        Text("Сбор", style = MaterialTheme.typography.headlineSmall)
        error?.let { Text(it, color = MaterialTheme.colorScheme.error) }
        detail?.let { c ->
            Text(c.title, style = MaterialTheme.typography.titleLarge)
            Text("${c.category} · ${c.status}")
            Text("К оплате: ${c.amountDue} ₽")
            if (c.amountPaid > 0) Text("Оплачено: ${c.amountPaid} ₽")
            c.eventAt?.let { Text("Событие: $it") }
            Text("Оплатили: ${c.paidCount} из ${c.inviteCount}")
            if (c.description.isNotBlank()) {
                Spacer(Modifier.height(8.dp))
                Text(c.description)
            }
        }
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
                val name = uri.lastPathSegment?.substringAfterLast('/') ?: "photo.jpg"
                val res = client.addWorkRequestPhoto(workRequestId, b64, name)
                photoCount = res.photoCount
                message = "Фото добавлено ($photoCount)"
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
        Spacer(Modifier.height(12.dp))
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
                        // 1×1 JPEG для быстрой проверки API без галереи
                        val tiny =
                            "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgNDRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/wAARCAABAAEDASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAn/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/8QAFQEBAQAAAAAAAAAAAAAAAAAAAAX/xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oADAMBAAIQAxAAAAGcP//EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAQUCf//EABQRAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQMBAT8Bf//EABQRAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQIBAT8Bf//Z"
                        val res = client.addWorkRequestPhoto(workRequestId, tiny, "dev.jpg")
                        photoCount = res.photoCount
                        message = "Dev-фото добавлено ($photoCount)"
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
        Spacer(Modifier.height(8.dp))
        Button(
            onClick = {
                scope.launch {
                    loading = true
                    error = null
                    try {
                        client.submitWorkRequest(workRequestId)
                        message = "Заявка отправлена мастерам"
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
    var loading by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()

    LaunchedEffect(Unit) {
        try {
            progress = client.onboarding()
            val firstTodo = progress?.steps?.indexOfFirst { !it.done } ?: 0
            index = if (firstTodo >= 0) firstTodo else 0
        } catch (e: Exception) {
            error = e.message
        }
    }

    val steps = progress?.steps.orEmpty()
    val step = steps.getOrNull(index)

    Column(modifier = Modifier.fillMaxSize().padding(16.dp)) {
        TextButton(onClick = onBack) { Text("← Назад") }
        Text("Обучение", style = MaterialTheme.typography.headlineSmall)
        progress?.let {
            Text("${it.doneCount} / ${it.total}")
            if (it.rewardGranted) {
                Text("Подарок: месяц подписки начислен", color = MaterialTheme.colorScheme.primary)
            }
        }
        Spacer(Modifier.height(12.dp))
        error?.let { Text(it, color = MaterialTheme.colorScheme.error) }
        if (step == null) {
            Text("Загрузка…")
        } else {
            Text(step.title, style = MaterialTheme.typography.titleLarge)
            Text(step.caption)
            Spacer(Modifier.height(8.dp))
            AsyncImage(
                model = staticUrl(apiBaseUrl, step.imageUrl),
                contentDescription = step.title,
                modifier = Modifier
                    .fillMaxWidth()
                    .heightIn(min = 160.dp, max = 320.dp),
                contentScale = ContentScale.Fit,
            )
            if (step.done) {
                Text("✓ просмотрено", color = MaterialTheme.colorScheme.primary)
            }
            Spacer(Modifier.height(12.dp))
            Button(
                onClick = {
                    scope.launch {
                        loading = true
                        error = null
                        try {
                            progress = client.completeOnboardingStep(step.code)
                            val next = (index + 1).coerceAtMost((progress?.steps?.size ?: 1) - 1)
                            index = next
                        } catch (e: Exception) {
                            error = e.message
                        } finally {
                            loading = false
                        }
                    }
                },
                modifier = Modifier.fillMaxWidth(),
                enabled = !loading && !step.done,
            ) { Text(if (step.done) "Уже пройдено" else "Понял · далее") }
            RowNav(
                canPrev = index > 0,
                canNext = index < steps.lastIndex,
                onPrev = { index -= 1 },
                onNext = { index += 1 },
            )
        }
    }
}

@Composable
private fun RowNav(
    canPrev: Boolean,
    canNext: Boolean,
    onPrev: () -> Unit,
    onNext: () -> Unit,
) {
    Column {
        if (canPrev) TextButton(onClick = onPrev) { Text("← Предыдущий") }
        if (canNext) TextButton(onClick = onNext) { Text("Следующий →") }
    }
}

private fun staticUrl(apiBase: String, path: String): String {
    if (path.startsWith("http")) return path
    val root = apiBase.removeSuffix("/api/v1").trimEnd('/')
    return root + path
}
