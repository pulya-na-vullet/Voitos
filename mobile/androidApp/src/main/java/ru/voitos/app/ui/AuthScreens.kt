package ru.voitos.app.ui

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
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
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.launch
import ru.voitos.app.api.VoitosApiClient
import ru.voitos.app.model.AuthSession
import ru.voitos.app.model.WishGroupOption
import ru.voitos.app.model.WishItem
import ru.voitos.app.ui.theme.VoitosColors
import ru.voitos.app.ui.theme.voitosOutlinedFieldColors
import ru.voitos.app.ui.theme.voitosPrimaryButtonColors
import ru.voitos.app.ui.theme.voitosSecondaryButtonColors

/**
 * Результат успешной аутентификации.
 * needsPinSetup — нужен экран задания постоянного PIN.
 */
data class LoginSuccess(
    val token: String,
    val name: String,
    val baseUrl: String,
    val phone: String,
    val needsPinSetup: Boolean,
    val hasPin: Boolean,
)

@Composable
fun LoginScreen(
    initialBaseUrl: String,
    initialPhone: String = "",
    initialDebugCode: String = "",
    recentBaseUrls: List<String> = emptyList(),
    restoredFromDisk: Boolean = false,
    preferPinLogin: Boolean = false,
    onLoggedIn: (LoginSuccess) -> Unit,
    onDebugPrefs: (baseUrl: String, phone: String, debugCode: String) -> Unit = { _, _, _ -> },
    onSaveServer: (baseUrl: String) -> Unit = {},
) {
    val initialHp = remember(initialBaseUrl) { ru.voitos.app.DevServerSettings.parse(initialBaseUrl) }
    var host by remember { mutableStateOf(initialHp.host) }
    var portText by remember { mutableStateOf(initialHp.port.toString()) }
    var phone by remember { mutableStateOf(initialPhone) }
    var code by remember { mutableStateOf("") }
    var pin by remember { mutableStateOf("") }
    var step by remember { mutableStateOf(0) } // 0 phone, 1 code from Max, 2 optional PIN return
    var showPin by remember { mutableStateOf(preferPinLogin && initialPhone.isNotBlank()) }
    var debugHint by remember { mutableStateOf<String?>(null) }
    var healthHint by remember { mutableStateOf<String?>(null) }
    var regHint by remember {
        mutableStateOf("Регистрация — в боте Max: укажите телефон, мы привяжем Max ID.")
    }
    var savedHint by remember {
        mutableStateOf(
            if (restoredFromDisk) "IP восстановлен из файла на телефоне" else null,
        )
    }
    var error by remember { mutableStateOf<String?>(null) }
    var loading by remember { mutableStateOf(false) }
    var scanning by remember { mutableStateOf(false) }
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

    fun applyFound(hit: ru.voitos.app.LanServerDiscovery.Found) {
        host = hit.host
        portText = hit.port.toString()
        persistDebug()
        onSaveServer(hit.baseUrl)
        savedHint = "Найден сервер: ${hit.host}:${hit.port}"
        recent = listOf(hit.baseUrl) + recent.filter { it != hit.baseUrl }
        healthHint = "Сервер отвечает ✓"
    }

    fun finish(session: AuthSession) {
        val p = session.phone.ifBlank { phone.trim() }
        onLoggedIn(
            LoginSuccess(
                token = session.accessToken,
                name = session.displayName,
                baseUrl = currentBaseUrl(),
                phone = p,
                needsPinSetup = session.needsPinSetup,
                hasPin = session.hasPin || !session.needsPinSetup,
            ),
        )
    }

    fun scanWifi(auto: Boolean = false) {
        scope.launch {
            scanning = true
            error = null
            if (!auto) healthHint = null
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
                if (hit != null) {
                    applyFound(hit)
                } else if (!auto) {
                    healthHint = null
                    error = "Сервер в Wi‑Fi не найден. Проверьте, что бэкенд запущен на порту $port."
                }
            } catch (e: Exception) {
                if (!auto) error = friendlyNetworkError(e)
            } finally {
                scanning = false
            }
        }
    }

    LaunchedEffect(Unit) {
        runCatching {
            val cfg = client().authConfig()
            if (cfg.registrationHint.isNotBlank()) regHint = cfg.registrationHint
        }
        // Автопоиск IP в сети (как кнопка «Найти сервер в Wi‑Fi»).
        scanWifi(auto = true)
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
            label = { Text("IP / host") },
            modifier = Modifier.fillMaxWidth(),
            singleLine = true,
            colors = voitosOutlinedFieldColors(),
        )
        OutlinedTextField(
            value = portText,
            onValueChange = {
                portText = it.filter { ch -> ch.isDigit() }.take(5)
                persistDebug()
            },
            label = { Text("Порт") },
            modifier = Modifier.fillMaxWidth(),
            singleLine = true,
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
            colors = voitosOutlinedFieldColors(),
        )
        Spacer(modifier = Modifier.height(8.dp))
        Button(
            onClick = { scanWifi(auto = false) },
            enabled = !loading && !scanning,
            modifier = Modifier.fillMaxWidth(),
            colors = voitosSecondaryButtonColors(),
        ) {
            Text(if (scanning) "Ищем сервер в Wi‑Fi…" else "Найти сервер в Wi‑Fi")
        }
        savedHint?.let {
            Text(it, color = VoitosColors.Ok, style = MaterialTheme.typography.bodySmall)
        }
        healthHint?.let {
            Text(it, color = VoitosColors.Accent2, style = MaterialTheme.typography.bodySmall)
        }
        if (recent.isNotEmpty()) {
            Text("Недавние:", color = VoitosColors.Muted, style = MaterialTheme.typography.labelSmall)
            recent.take(3).forEach { url ->
                val hp = ru.voitos.app.DevServerSettings.parse(url)
                TextButton(onClick = {
                    host = hp.host
                    portText = hp.port.toString()
                    persistDebug()
                    onSaveServer(currentBaseUrl())
                    savedHint = "Сохранено: ${currentBaseUrl()}"
                }) { Text("${hp.host}:${hp.port}", color = VoitosColors.Accent) }
            }
        }

        Spacer(modifier = Modifier.height(16.dp))
        Text(regHint, color = VoitosColors.Muted, style = MaterialTheme.typography.bodySmall)
        Spacer(modifier = Modifier.height(8.dp))

        if (showPin) {
            Text("Быстрый вход по PIN", style = MaterialTheme.typography.titleMedium, color = VoitosColors.Text)
            OutlinedTextField(
                value = phone,
                onValueChange = {
                    phone = it.filter { ch -> ch.isDigit() }.take(11)
                    persistDebug()
                },
                label = { Text("Телефон") },
                modifier = Modifier.fillMaxWidth(),
                singleLine = true,
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Phone),
                colors = voitosOutlinedFieldColors(),
            )
            OutlinedTextField(
                value = pin,
                onValueChange = { pin = it.filter { ch -> ch.isDigit() }.take(4) },
                label = { Text("PIN") },
                modifier = Modifier.fillMaxWidth(),
                singleLine = true,
                visualTransformation = PasswordVisualTransformation(),
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.NumberPassword),
                colors = voitosOutlinedFieldColors(),
            )
            error?.let { Text(it, color = VoitosColors.Danger) }
            Spacer(modifier = Modifier.height(8.dp))
            if (loading || scanning) {
                CircularProgressIndicator(modifier = Modifier.align(Alignment.CenterHorizontally), color = VoitosColors.Accent)
            } else {
                Button(
                    onClick = {
                        scope.launch {
                            loading = true
                            error = null
                            try {
                                persistDebug()
                                onSaveServer(currentBaseUrl())
                                finish(client().pinLogin(phone, pin))
                            } catch (e: Exception) {
                                error = friendlyNetworkError(e, fallback = "Неверный PIN")
                            } finally {
                                loading = false
                            }
                        }
                    },
                    modifier = Modifier.fillMaxWidth(),
                    colors = voitosPrimaryButtonColors(),
                ) { Text("Войти по PIN") }
                TextButton(onClick = { showPin = false; error = null }) {
                    Text("Войти через код в Max", color = VoitosColors.Accent)
                }
            }
            return@Column
        }

        OutlinedTextField(
            value = phone,
            onValueChange = {
                phone = it.filter { ch -> ch.isDigit() }.take(11)
                persistDebug()
            },
            label = { Text("Телефон") },
            modifier = Modifier.fillMaxWidth(),
            singleLine = true,
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Phone),
            colors = voitosOutlinedFieldColors(),
        )
        if (step >= 1) {
            Spacer(modifier = Modifier.height(8.dp))
            OutlinedTextField(
                value = code,
                onValueChange = { code = it.filter { ch -> ch.isDigit() }.take(4) },
                label = { Text("Код из Max") },
                modifier = Modifier.fillMaxWidth(),
                singleLine = true,
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.NumberPassword),
                colors = voitosOutlinedFieldColors(),
            )
            debugHint?.let {
                Text(it, style = MaterialTheme.typography.bodySmall, color = VoitosColors.Accent2)
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
                        debugHint = null
                        try {
                            persistDebug()
                            onSaveServer(currentBaseUrl())
                            val res = client().phoneLoginRequest(phone)
                            if (!res.debugCode.isNullOrBlank()) {
                                code = res.debugCode.orEmpty()
                                debugHint = "Dev-код: ${res.debugCode}"
                                persistDebug(res.debugCode.orEmpty())
                            } else {
                                debugHint = res.message.ifBlank { "Код отправлен в Max" }
                            }
                            step = 1
                        } catch (e: Exception) {
                            error = e.message?.takeIf { it.isNotBlank() }
                                ?: friendlyNetworkError(e)
                        } finally {
                            loading = false
                        }
                    }
                },
                modifier = Modifier.fillMaxWidth(),
                colors = voitosPrimaryButtonColors(),
            ) { Text("Войти") }
            if (preferPinLogin && initialPhone.isNotBlank()) {
                TextButton(onClick = { showPin = true }) {
                    Text("Войти по PIN", color = VoitosColors.Muted)
                }
            }
        } else {
            Button(
                onClick = {
                    scope.launch {
                        loading = true
                        error = null
                        try {
                            persistDebug(code)
                            onSaveServer(currentBaseUrl())
                            finish(client().phoneLoginVerify(phone, code))
                        } catch (e: Exception) {
                            error = e.message?.takeIf { it.isNotBlank() }
                                ?: friendlyNetworkError(e, fallback = "Неверный код")
                        } finally {
                            loading = false
                        }
                    }
                },
                modifier = Modifier.fillMaxWidth(),
                colors = voitosPrimaryButtonColors(),
            ) { Text("Подтвердить код") }
            TextButton(onClick = { step = 0; code = ""; debugHint = null }) {
                Text("Изменить номер", color = VoitosColors.Accent)
            }
        }
    }
}

@Composable
fun SetPinScreen(
    client: VoitosApiClient,
    onDone: () -> Unit,
) {
    var pin by remember { mutableStateOf("") }
    var pin2 by remember { mutableStateOf("") }
    var error by remember { mutableStateOf<String?>(null) }
    var loading by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()

    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(24.dp),
        verticalArrangement = Arrangement.Center,
    ) {
        Text("Задайте PIN", style = MaterialTheme.typography.headlineSmall, color = VoitosColors.Text)
        Text(
            "4 цифры для быстрого входа в следующий раз.",
            color = VoitosColors.Muted,
            style = MaterialTheme.typography.bodySmall,
        )
        Spacer(modifier = Modifier.height(12.dp))
        OutlinedTextField(
            value = pin,
            onValueChange = { pin = it.filter { ch -> ch.isDigit() }.take(4) },
            label = { Text("PIN") },
            modifier = Modifier.fillMaxWidth(),
            visualTransformation = PasswordVisualTransformation(),
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.NumberPassword),
            colors = voitosOutlinedFieldColors(),
        )
        OutlinedTextField(
            value = pin2,
            onValueChange = { pin2 = it.filter { ch -> ch.isDigit() }.take(4) },
            label = { Text("Ещё раз") },
            modifier = Modifier.fillMaxWidth(),
            visualTransformation = PasswordVisualTransformation(),
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.NumberPassword),
            colors = voitosOutlinedFieldColors(),
        )
        error?.let { Text(it, color = VoitosColors.Danger) }
        Spacer(modifier = Modifier.height(12.dp))
        Button(
            onClick = {
                if (pin.length != 4) {
                    error = "PIN — ровно 4 цифры"
                    return@Button
                }
                if (pin != pin2) {
                    error = "PIN не совпадает"
                    return@Button
                }
                scope.launch {
                    loading = true
                    error = null
                    try {
                        client.pinSet(pin)
                        onDone()
                    } catch (e: Exception) {
                        error = friendlyNetworkError(e)
                    } finally {
                        loading = false
                    }
                }
            },
            enabled = !loading,
            modifier = Modifier.fillMaxWidth(),
            colors = voitosPrimaryButtonColors(),
        ) { Text(if (loading) "…" else "Сохранить PIN") }
    }
}

@Composable
fun ChangePinScreen(
    client: VoitosApiClient,
    onBack: () -> Unit,
) {
    BackHandler(enabled = true) { onBack() }
    var code by remember { mutableStateOf("") }
    var pin by remember { mutableStateOf("") }
    var pin2 by remember { mutableStateOf("") }
    var hint by remember { mutableStateOf<String?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    var loading by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()

    Column(
        modifier = Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(16.dp),
    ) {
        VoitosBackButton(onClick = onBack)
        Text("Сменить PIN", style = MaterialTheme.typography.headlineSmall, color = VoitosColors.Text)
        Text(
            "Код подтверждения придёт в Max.",
            color = VoitosColors.Muted,
            style = MaterialTheme.typography.bodySmall,
        )
        Spacer(modifier = Modifier.height(12.dp))
        Button(
            onClick = {
                scope.launch {
                    loading = true
                    error = null
                    try {
                        val res = client.pinChangeRequest()
                        hint = if (!res.debugCode.isNullOrBlank()) {
                            "Dev-код: ${res.debugCode}"
                        } else {
                            "Код отправлен в Max"
                        }
                    } catch (e: Exception) {
                        error = friendlyNetworkError(e)
                    } finally {
                        loading = false
                    }
                }
            },
            colors = voitosSecondaryButtonColors(),
        ) { Text("Получить код в Max") }
        hint?.let { Text(it, color = VoitosColors.Ok) }
        OutlinedTextField(
            value = code,
            onValueChange = { code = it.filter { ch -> ch.isDigit() }.take(4) },
            label = { Text("Код из Max") },
            modifier = Modifier.fillMaxWidth(),
            colors = voitosOutlinedFieldColors(),
        )
        OutlinedTextField(
            value = pin,
            onValueChange = { pin = it.filter { ch -> ch.isDigit() }.take(4) },
            label = { Text("Новый PIN") },
            modifier = Modifier.fillMaxWidth(),
            visualTransformation = PasswordVisualTransformation(),
            colors = voitosOutlinedFieldColors(),
        )
        OutlinedTextField(
            value = pin2,
            onValueChange = { pin2 = it.filter { ch -> ch.isDigit() }.take(4) },
            label = { Text("Повтор PIN") },
            modifier = Modifier.fillMaxWidth(),
            visualTransformation = PasswordVisualTransformation(),
            colors = voitosOutlinedFieldColors(),
        )
        error?.let { Text(it, color = VoitosColors.Danger) }
        Spacer(modifier = Modifier.height(12.dp))
        Button(
            onClick = {
                if (pin.length != 4 || pin != pin2) {
                    error = "Проверьте PIN (4 цифры, совпадение)"
                    return@Button
                }
                scope.launch {
                    loading = true
                    error = null
                    try {
                        client.pinChangeConfirm(code, pin)
                        onBack()
                    } catch (e: Exception) {
                        error = friendlyNetworkError(e)
                    } finally {
                        loading = false
                    }
                }
            },
            enabled = !loading,
            modifier = Modifier.fillMaxWidth(),
            colors = voitosPrimaryButtonColors(),
        ) { Text("Сохранить") }
    }
}

@Composable
fun WishScreen(
    client: VoitosApiClient,
    onBack: () -> Unit,
) {
    BackHandler(enabled = true) { onBack() }
    var items by remember { mutableStateOf<List<WishItem>>(emptyList()) }
    var groups by remember { mutableStateOf<List<WishGroupOption>>(emptyList()) }
    var text by remember { mutableStateOf("") }
    var groupId by remember { mutableStateOf<Int?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    var message by remember { mutableStateOf<String?>(null) }
    var loading by remember { mutableStateOf(true) }
    val scope = rememberCoroutineScope()

    fun reload() {
        scope.launch {
            loading = true
            error = null
            runCatching { client.wishes() }
                .onSuccess {
                    items = it.items
                    groups = it.groups
                    if (groupId == null && groups.size == 1) groupId = groups.first().id
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
        Text("Пожелания", style = MaterialTheme.typography.headlineSmall, color = VoitosColors.Text)
        Text(
            "Идеи для двора — как в Max, попадают в ту же ленту группы.",
            color = VoitosColors.Muted,
            style = MaterialTheme.typography.bodySmall,
        )
        Spacer(modifier = Modifier.height(10.dp))
        error?.let { NetworkErrorText(it) }
        message?.let { Text(it, color = VoitosColors.Ok) }

        if (groups.size > 1) {
            Text("Группа", color = VoitosColors.Muted)
            groups.forEach { g ->
                TextButton(onClick = { groupId = g.id }) {
                    Text(
                        if (groupId == g.id) "✓ ${g.name}" else g.name,
                        color = VoitosColors.Accent,
                    )
                }
            }
        }

        OutlinedTextField(
            value = text,
            onValueChange = { text = it },
            label = { Text("Текст пожелания") },
            modifier = Modifier.fillMaxWidth().height(120.dp),
            colors = voitosOutlinedFieldColors(),
        )
        Spacer(modifier = Modifier.height(8.dp))
        Button(
            onClick = {
                scope.launch {
                    loading = true
                    error = null
                    message = null
                    try {
                        val created = client.createWish(text.trim(), groupId)
                        message = "Принято: ${created.topicLabel}"
                        text = ""
                        reload()
                    } catch (e: Exception) {
                        error = friendlyNetworkError(e)
                    } finally {
                        loading = false
                    }
                }
            },
            enabled = text.isNotBlank() && !loading,
            modifier = Modifier.fillMaxWidth(),
            colors = voitosPrimaryButtonColors(),
        ) { Text("Отправить") }

        Spacer(modifier = Modifier.height(16.dp))
        Text("Мои пожелания", style = MaterialTheme.typography.titleMedium, color = VoitosColors.Text)
        if (loading && items.isEmpty()) {
            VoitosListSkeleton(rows = 2)
        }
        items.forEach { w ->
            Spacer(modifier = Modifier.height(8.dp))
            PanelCard {
                Text(w.topicLabel.ifBlank { w.topic }, color = VoitosColors.Accent2)
                Text(w.text, color = VoitosColors.Text)
                if (w.groupName.isNotBlank()) {
                    Text(w.groupName, color = VoitosColors.Muted, style = MaterialTheme.typography.bodySmall)
                }
            }
        }
    }
}
