package ru.voitos.app.ui

import android.content.Intent
import android.net.Uri
import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.Image
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.launch
import ru.voitos.app.AppVersion
import ru.voitos.app.R
import ru.voitos.app.api.VoitosApiClient
import ru.voitos.app.model.ApiException
import ru.voitos.app.model.AuthSession
import ru.voitos.app.model.WishGroupOption
import ru.voitos.app.model.WishItem
import ru.voitos.app.update.ApkUpdater
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
    val needsOnboarding: Boolean = false,
    /** Подписка закончилась — после входа ведём на загрузку чека. */
    val needsPayment: Boolean = false,
)

/** ДД.ММ.ГГГГ → YYYY-MM-DD или null. */
private fun birthDateToIso(raw: String): String? {
    val parts = raw.trim().split(".")
    if (parts.size != 3) return null
    val d = parts[0].toIntOrNull() ?: return null
    val m = parts[1].toIntOrNull() ?: return null
    val y = parts[2].toIntOrNull() ?: return null
    if (parts[2].length != 4) return null
    if (d !in 1..31 || m !in 1..12 || y !in 1900..2100) return null
    return "%04d-%02d-%02d".format(y, m, d)
}

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
    val context = LocalContext.current
    val initialHp = remember(initialBaseUrl) { ru.voitos.app.DevServerSettings.parse(initialBaseUrl) }
    var baseUrl by remember {
        mutableStateOf(
            ru.voitos.app.DevServerSettings.HostPort(initialHp.host, initialHp.port).toBaseUrl(),
        )
    }
    var serverReady by remember { mutableStateOf(false) }
    var updateRequired by remember { mutableStateOf(false) }
    var updateMessage by remember { mutableStateOf("") }
    var updateApkUrl by remember { mutableStateOf("") }
    var latestVersionName by remember { mutableStateOf("") }
    var phone by remember { mutableStateOf(initialPhone) }
    var code by remember { mutableStateOf("") }
    var pin by remember { mutableStateOf("") }
    var step by remember { mutableStateOf(0) } // 0 phone, 1 code from Max
    var showPin by remember { mutableStateOf(preferPinLogin && initialPhone.isNotBlank()) }
    var showRegister by remember { mutableStateOf(false) }
    var regStep by remember { mutableStateOf(0) } // 0 form, 1 max code
    var realName by remember { mutableStateOf("") }
    var gender by remember { mutableStateOf("") } // male | female | other
    var birthDate by remember { mutableStateOf("") } // DD.MM.YYYY
    var address by remember { mutableStateOf("") }
    var locality by remember { mutableStateOf("") }
    var maxBotUrl by remember { mutableStateOf("https://max.ru/se13602985_1_bot") }
    var canRegister by remember { mutableStateOf(false) }
    var debugHint by remember { mutableStateOf<String?>(null) }
    var serverStatus by remember {
        mutableStateOf(
            if (restoredFromDisk) "Проверяем сохранённый адрес…" else "Ищем сервер в Wi‑Fi…",
        )
    }
    var regHint by remember {
        mutableStateOf(
            "Если номера нет — «Регистрация»: ФИО, пол, дата рождения, затем код из бота Max.",
        )
    }
    var error by remember { mutableStateOf<String?>(null) }
    var loading by remember { mutableStateOf(false) }
    var scanning by remember { mutableStateOf(false) }
    var showManualHost by remember { mutableStateOf(false) }
    var manualHost by remember { mutableStateOf("") }
    val scope = rememberCoroutineScope()

    fun client(): VoitosApiClient = VoitosApiClient(baseUrl = baseUrl).also {
        it.appVersionCode = AppVersion.code
    }

    fun persistDebug(debugCode: String = "") {
        onDebugPrefs(baseUrl, phone.trim(), debugCode)
    }

    fun applyVersionGate(minCode: Int, message: String, apkUrl: String, latestName: String) {
        if (minCode > 0 && AppVersion.code < minCode) {
            updateRequired = true
            updateMessage = message.ifBlank {
                "Доступна новая версия приложения. Обновите Voitos, чтобы продолжить."
            }
            updateApkUrl = apkUrl
            latestVersionName = latestName
        } else {
            updateRequired = false
            updateMessage = ""
            updateApkUrl = ""
            latestVersionName = latestName
        }
    }

    fun applyFound(hit: ru.voitos.app.LanServerDiscovery.Found) {
        baseUrl = hit.baseUrl
        serverReady = true
        showManualHost = false
        persistDebug()
        onSaveServer(hit.baseUrl)
        serverStatus = "Подключено: ${hit.host}:${hit.port}"
    }

    fun finish(session: AuthSession) {
        val needsUpdate = session.updateRequired ||
            (session.minAppVersionCode > 0 && AppVersion.code < session.minAppVersionCode)
        if (needsUpdate) {
            applyVersionGate(
                minCode = session.minAppVersionCode,
                message = session.updateMessage,
                apkUrl = session.apkUrl,
                latestName = session.latestAppVersionName,
            )
            return
        }
        val p = session.phone.ifBlank { phone.trim() }
        onLoggedIn(
            LoginSuccess(
                token = session.accessToken,
                name = session.displayName,
                baseUrl = baseUrl,
                phone = p,
                needsPinSetup = session.needsPinSetup,
                hasPin = session.hasPin || !session.needsPinSetup,
                needsOnboarding = session.needsOnboarding,
                needsPayment = session.needsPayment || session.access?.state == "blocked",
            ),
        )
    }

    fun tryManualHost() {
        scope.launch {
            val host = manualHost.trim().substringBefore(':').trim()
            if (host.isBlank()) {
                error = "Укажите IP компьютера с бэкендом (например 192.168.1.10)"
                return@launch
            }
            loading = true
            error = null
            serverStatus = "Подключаемся к $host…"
            try {
                val hit = ru.voitos.app.LanServerDiscovery.findFirst(
                    port = 18765,
                    preferHosts = listOf(host),
                    context = context,
                )
                if (hit != null) {
                    applyFound(hit)
                    val health = runCatching { client().healthCheck(AppVersion.code) }.getOrNull()
                    if (health != null) {
                        applyVersionGate(
                            minCode = health.minAppVersionCode,
                            message = health.updateMessage,
                            apkUrl = health.apkUrl,
                            latestName = health.latestAppVersionName,
                        )
                    }
                } else {
                    error = "Нет ответа на $host:18765. Проверьте, что бэкенд запущен и телефон в той же Wi‑Fi."
                    serverStatus = "Сервер не найден"
                }
            } catch (e: Exception) {
                error = friendlyNetworkError(e)
                serverStatus = "Не удалось подключиться"
            } finally {
                loading = false
            }
        }
    }

    fun scanWifi(auto: Boolean = false) {
        scope.launch {
            scanning = true
            serverReady = false
            updateRequired = false
            if (!auto) error = null
            val net = ru.voitos.app.LanServerDiscovery.localNet(context)
            serverStatus = if (net.phoneIp != null) {
                "Ищем сервер в Wi‑Fi (ваш IP ${net.phoneIp})…"
            } else {
                "Ищем сервер в Wi‑Fi…"
            }
            try {
                val prefer = buildList {
                    add(baseUrl)
                    addAll(recentBaseUrls)
                    add(initialBaseUrl)
                }.distinct().filter { it.isNotBlank() }
                val hit = ru.voitos.app.LanServerDiscovery.findFirst(
                    port = 18765,
                    preferHosts = prefer,
                    context = context,
                )
                if (hit != null) {
                    applyFound(hit)
                    val health = runCatching {
                        client().healthCheck(AppVersion.code)
                    }.getOrNull()
                    if (health != null) {
                        applyVersionGate(
                            minCode = health.minAppVersionCode,
                            message = health.updateMessage,
                            apkUrl = health.apkUrl,
                            latestName = health.latestAppVersionName,
                        )
                        if (health.updateRequired) {
                            serverStatus = "Нужно обновить приложение"
                        }
                    }
                } else {
                    showManualHost = true
                    serverStatus = if (net.phoneIp == null) {
                        "Не удалось определить Wi‑Fi IP — укажите адрес сервера вручную"
                    } else {
                        "Сервер в Wi‑Fi не найден (подсеть ${net.prefix}.x)"
                    }
                    if (!auto) {
                        error = "Проверьте, что бэкенд запущен на порту 18765 и телефон в той же сети Wi‑Fi (не гостевая)."
                    }
                }
            } catch (e: Exception) {
                showManualHost = true
                serverStatus = "Не удалось найти сервер"
                if (!auto) error = friendlyNetworkError(e)
            } finally {
                scanning = false
            }
        }
    }

    LaunchedEffect(Unit) {
        scanWifi(auto = true)
    }

    LaunchedEffect(serverReady, baseUrl) {
        if (!serverReady) return@LaunchedEffect
        runCatching {
            val cfg = client().authConfig()
            if (cfg.registrationHint.isNotBlank()) regHint = cfg.registrationHint
            if (cfg.maxBotOpenUrl.isNotBlank()) maxBotUrl = cfg.maxBotOpenUrl
            applyVersionGate(
                minCode = cfg.minAppVersionCode,
                message = cfg.updateMessage,
                apkUrl = cfg.apkUrl,
                latestName = cfg.latestAppVersionName,
            )
        }
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(24.dp),
        verticalArrangement = Arrangement.Center,
    ) {
        if (updateRequired) {
            ForceUpdateScreen(apkUrl = updateApkUrl)
            return@Column
        }
        Text("Voitos", style = MaterialTheme.typography.headlineLarge, color = MaterialTheme.colorScheme.secondary)
        Text("Вход по телефону", style = MaterialTheme.typography.bodyMedium, color = VoitosColors.Text)
        Text(
            "Версия ${AppVersion.name} (${AppVersion.code})",
            style = MaterialTheme.typography.bodySmall,
            color = VoitosColors.Muted,
        )
        Spacer(modifier = Modifier.height(6.dp))
        Text(
            "Если подписка закончилась — всё равно войдите и загрузите чек. " +
                "Доступ откроется после проверки.",
            style = MaterialTheme.typography.bodySmall,
            color = VoitosColors.Muted,
        )
        Spacer(modifier = Modifier.height(8.dp))
        VpnDebugBanner()
        Spacer(modifier = Modifier.height(12.dp))

        if (scanning) {
            CircularProgressIndicator(
                modifier = Modifier.align(Alignment.CenterHorizontally),
                color = VoitosColors.Accent,
            )
            Spacer(modifier = Modifier.height(8.dp))
        }
        Text(
            serverStatus,
            color = when {
                updateRequired -> VoitosColors.Warn
                serverReady -> VoitosColors.Ok
                else -> VoitosColors.Muted
            },
            style = MaterialTheme.typography.bodyMedium,
            modifier = Modifier.align(Alignment.CenterHorizontally),
        )
        if (!scanning && !serverReady) {
            TextButton(
                onClick = { scanWifi(auto = false) },
                modifier = Modifier.align(Alignment.CenterHorizontally),
            ) {
                Text("Повторить поиск", color = VoitosColors.Accent)
            }
        }
        if (!serverReady && showManualHost) {
            Spacer(modifier = Modifier.height(8.dp))
            Text(
                "IP компьютера с бэкендом (порт 18765)",
                color = VoitosColors.Muted,
                style = MaterialTheme.typography.bodySmall,
            )
            OutlinedTextField(
                value = manualHost,
                onValueChange = { manualHost = it.filter { ch -> ch.isDigit() || ch == '.' }.take(15) },
                label = { Text("Например 192.168.1.10") },
                modifier = Modifier.fillMaxWidth(),
                singleLine = true,
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                colors = voitosOutlinedFieldColors(),
            )
            Button(
                onClick = { tryManualHost() },
                enabled = !loading && !scanning && manualHost.isNotBlank(),
                modifier = Modifier.fillMaxWidth(),
                colors = voitosSecondaryButtonColors(),
            ) { Text("Подключить по IP") }
        }

        if (showRegister) {
            Text("Регистрация", style = MaterialTheme.typography.titleMedium, color = VoitosColors.Text)
            Spacer(modifier = Modifier.height(8.dp))
            if (regStep == 0) {
                OutlinedTextField(
                    value = realName,
                    onValueChange = { realName = it.take(120) },
                    label = { Text("ФИО") },
                    modifier = Modifier.fillMaxWidth(),
                    singleLine = true,
                    colors = voitosOutlinedFieldColors(),
                )
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
                Spacer(modifier = Modifier.height(8.dp))
                Text("Пол", color = VoitosColors.Muted, style = MaterialTheme.typography.bodySmall)
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    listOf(
                        "male" to "Мужской",
                        "female" to "Женский",
                        "other" to "Другой",
                    ).forEach { (value, label) ->
                        val selected = gender == value
                        OutlinedButton(
                            onClick = { gender = value },
                            modifier = Modifier.weight(1f),
                            colors = ButtonDefaults.outlinedButtonColors(
                                containerColor = if (selected) {
                                    VoitosColors.BgSoft
                                } else {
                                    Color.Transparent
                                },
                                contentColor = VoitosColors.Text,
                            ),
                            border = BorderStroke(
                                width = if (selected) 2.dp else 1.dp,
                                color = if (selected) VoitosColors.Accent2 else Color(0xFF3A4656),
                            ),
                        ) {
                            Text(
                                label,
                                color = VoitosColors.Text,
                                style = MaterialTheme.typography.labelSmall.copy(
                                    color = VoitosColors.Text,
                                ),
                            )
                        }
                    }
                }
                OutlinedTextField(
                    value = birthDate,
                    onValueChange = { raw ->
                        birthDate = raw.filter { it.isDigit() || it == '.' }.take(10)
                    },
                    label = { Text("Дата рождения (ДД.ММ.ГГГГ)") },
                    modifier = Modifier.fillMaxWidth(),
                    singleLine = true,
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                    colors = voitosOutlinedFieldColors(),
                )
                OutlinedTextField(
                    value = locality,
                    onValueChange = { locality = it.take(120) },
                    label = { Text("Населённый пункт") },
                    modifier = Modifier.fillMaxWidth(),
                    singleLine = true,
                    colors = voitosOutlinedFieldColors(),
                )
                OutlinedTextField(
                    value = address,
                    onValueChange = { address = it.take(500) },
                    label = { Text("Адрес (улица, дом, квартира)") },
                    modifier = Modifier.fillMaxWidth(),
                    minLines = 2,
                    colors = voitosOutlinedFieldColors(),
                )
                error?.let {
                    Spacer(modifier = Modifier.height(8.dp))
                    Text(it, color = VoitosColors.Danger)
                }
                Spacer(modifier = Modifier.height(16.dp))
                if (loading) {
                    CircularProgressIndicator(
                        modifier = Modifier.align(Alignment.CenterHorizontally),
                        color = VoitosColors.Accent,
                    )
                } else {
                    Button(
                        onClick = {
                            scope.launch {
                                if (!serverReady) {
                                    error = "Дождитесь поиска сервера в Wi‑Fi"
                                    return@launch
                                }
                                val iso = birthDateToIso(birthDate)
                                if (iso == null) {
                                    error = "Укажите дату рождения в формате ДД.ММ.ГГГГ"
                                    return@launch
                                }
                                if (realName.trim().length < 2) {
                                    error = "Укажите ФИО"
                                    return@launch
                                }
                                if (gender.isBlank()) {
                                    error = "Укажите пол"
                                    return@launch
                                }
                                if (locality.trim().length < 2) {
                                    error = "Укажите населённый пункт"
                                    return@launch
                                }
                                if (address.trim().length < 3) {
                                    error = "Укажите адрес"
                                    return@launch
                                }
                                loading = true
                                error = null
                                try {
                                    persistDebug()
                                    onSaveServer(baseUrl)
                                    val resp = client().registerStart(
                                        phone = phone,
                                        realName = realName.trim(),
                                        gender = gender,
                                        birthDate = iso,
                                        address = address.trim(),
                                        locality = locality.trim(),
                                    )
                                    if (resp.maxBotOpenUrl.isNotBlank()) {
                                        maxBotUrl = resp.maxBotOpenUrl
                                    }
                                    debugHint = resp.message.ifBlank {
                                        "Нажмите «Перейти в Max» — бот пришлёт код. Если кода нет, напишите в боте «код»."
                                    }
                                    code = ""
                                    regStep = 1
                                } catch (e: Exception) {
                                    error = e.message?.takeIf { it.isNotBlank() }
                                        ?: friendlyNetworkError(e)
                                } finally {
                                    loading = false
                                }
                            }
                        },
                        enabled = serverReady && !scanning,
                        modifier = Modifier.fillMaxWidth(),
                        colors = voitosPrimaryButtonColors(),
                    ) { Text("Продолжить") }
                    TextButton(
                        onClick = {
                            showRegister = false
                            regStep = 0
                            error = null
                            debugHint = null
                        },
                    ) {
                        Text("Назад ко входу", color = VoitosColors.Muted)
                    }
                }
            } else {
                Text(
                    debugHint
                        ?: "Нажмите «Перейти в Max» — бот пришлёт код. Если кода нет, напишите в боте «код» и введите его ниже.",
                    color = VoitosColors.Muted,
                    style = MaterialTheme.typography.bodyMedium,
                )
                Spacer(modifier = Modifier.height(8.dp))
                val botUrl = maxBotUrl.ifBlank { "https://max.ru/se13602985_1_bot" }
                Button(
                    onClick = {
                        runCatching {
                            context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(botUrl)))
                        }
                    },
                    modifier = Modifier.fillMaxWidth(),
                    colors = voitosSecondaryButtonColors(),
                ) { Text("Перейти в Max") }
                Spacer(modifier = Modifier.height(8.dp))
                OutlinedTextField(
                    value = code,
                    onValueChange = { code = it.filter { ch -> ch.isDigit() }.take(4) },
                    label = { Text("Код из бота Max") },
                    modifier = Modifier.fillMaxWidth(),
                    singleLine = true,
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.NumberPassword),
                    colors = voitosOutlinedFieldColors(),
                )
                error?.let {
                    Spacer(modifier = Modifier.height(8.dp))
                    Text(it, color = VoitosColors.Danger)
                }
                Spacer(modifier = Modifier.height(16.dp))
                if (loading) {
                    CircularProgressIndicator(
                        modifier = Modifier.align(Alignment.CenterHorizontally),
                        color = VoitosColors.Accent,
                    )
                } else {
                    Button(
                        onClick = {
                            scope.launch {
                                loading = true
                                error = null
                                try {
                                    persistDebug(code)
                                    onSaveServer(baseUrl)
                                    finish(client().registerConfirm(phone, code))
                                } catch (e: Exception) {
                                    error = e.message?.takeIf { it.isNotBlank() }
                                        ?: friendlyNetworkError(e, fallback = "Неверный код")
                                } finally {
                                    loading = false
                                }
                            }
                        },
                        enabled = serverReady && code.length == 4,
                        modifier = Modifier.fillMaxWidth(),
                        colors = voitosPrimaryButtonColors(),
                    ) { Text("Завершить регистрацию") }
                    TextButton(onClick = { regStep = 0; code = ""; error = null }) {
                        Text("Изменить анкету", color = VoitosColors.Accent)
                    }
                }
            }
            return@Column
        }

        Spacer(modifier = Modifier.height(16.dp))
        Text(regHint, color = VoitosColors.Muted, style = MaterialTheme.typography.bodySmall)
        Spacer(modifier = Modifier.height(8.dp))

        if (showPin) {
            Text("Быстрый вход по PIN", style = MaterialTheme.typography.titleMedium, color = VoitosColors.Text)
            Text(
                "Даже если подписка закончилась — войдите и загрузите чек.",
                color = VoitosColors.Muted,
                style = MaterialTheme.typography.bodySmall,
            )
            Spacer(modifier = Modifier.height(8.dp))
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
                            if (!serverReady) {
                                error = "Дождитесь поиска сервера в Wi‑Fi"
                                return@launch
                            }
                            loading = true
                            error = null
                            try {
                                persistDebug()
                                onSaveServer(baseUrl)
                                finish(client().pinLogin(phone, pin))
                            } catch (e: ApiException) {
                                if (e.code == "update_required") {
                                    val health = runCatching {
                                        client().healthCheck(AppVersion.code)
                                    }.getOrNull()
                                    applyVersionGate(
                                        minCode = health?.minAppVersionCode ?: (AppVersion.code + 1),
                                        message = health?.updateMessage ?: e.message.orEmpty(),
                                        apkUrl = health?.apkUrl.orEmpty(),
                                        latestName = health?.latestAppVersionName.orEmpty(),
                                    )
                                } else {
                                    error = friendlyNetworkError(e, fallback = "Неверный PIN")
                                }
                            } catch (e: Exception) {
                                error = friendlyNetworkError(e, fallback = "Неверный PIN")
                            } finally {
                                loading = false
                            }
                        }
                    },
                    enabled = serverReady,
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
                canRegister = false
                persistDebug()
            },
            label = { Text("Телефон") },
            modifier = Modifier.fillMaxWidth(),
            singleLine = true,
            enabled = serverReady || !scanning,
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
            Text(
                "Код придёт в чат бота Max — введите его вручную.",
                color = VoitosColors.Muted,
                style = MaterialTheme.typography.bodySmall,
            )
        }
        error?.let {
            Spacer(modifier = Modifier.height(8.dp))
            Text(it, color = VoitosColors.Danger)
        }
        Spacer(modifier = Modifier.height(16.dp))
        if (loading) {
            CircularProgressIndicator(
                modifier = Modifier.align(Alignment.CenterHorizontally),
                color = VoitosColors.Accent,
            )
        } else if (step == 0) {
            Button(
                onClick = {
                    scope.launch {
                        if (!serverReady) {
                            error = "Дождитесь поиска сервера в Wi‑Fi"
                            return@launch
                        }
                        loading = true
                        error = null
                        debugHint = null
                        try {
                            persistDebug()
                            onSaveServer(baseUrl)
                            client().phoneLoginRequest(phone)
                            // Код только из бота Max — поле не заполняем автоматически.
                            debugHint = "Код отправлен в Max"
                            canRegister = false
                            step = 1
                        } catch (e: ApiException) {
                            error = e.message
                            canRegister = e.code == "not_registered"
                        } catch (e: Exception) {
                            error = e.message?.takeIf { it.isNotBlank() }
                                ?: friendlyNetworkError(e)
                            canRegister = false
                        } finally {
                            loading = false
                        }
                    }
                },
                enabled = serverReady && !scanning,
                modifier = Modifier.fillMaxWidth(),
                colors = voitosPrimaryButtonColors(),
            ) { Text("Войти") }
            Spacer(modifier = Modifier.height(8.dp))
            Text(
                "Подписка закончилась? Войдите как обычно — откроется загрузка чека.",
                color = VoitosColors.Muted,
                style = MaterialTheme.typography.bodySmall,
            )
            if (canRegister) {
                Spacer(modifier = Modifier.height(8.dp))
                Button(
                    onClick = {
                        showRegister = true
                        regStep = 0
                        error = null
                        debugHint = null
                        code = ""
                    },
                    enabled = serverReady,
                    modifier = Modifier.fillMaxWidth(),
                    colors = voitosSecondaryButtonColors(),
                ) { Text("Регистрация") }
            }
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
                            onSaveServer(baseUrl)
                            finish(client().phoneLoginVerify(phone, code))
                        } catch (e: Exception) {
                            error = e.message?.takeIf { it.isNotBlank() }
                                ?: friendlyNetworkError(e, fallback = "Неверный код")
                        } finally {
                            loading = false
                        }
                    }
                },
                enabled = serverReady,
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
fun ForceUpdateScreen(
    apkUrl: String = "",
) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    BackHandler(enabled = true) { /* hard update — только обновить */ }

    var phase by remember { mutableStateOf(UpdatePhase.Idle) }
    var progress by remember { mutableFloatStateOf(0f) }
    var downloadedBytes by remember { mutableLongStateOf(0L) }
    var totalBytes by remember { mutableLongStateOf(-1L) }
    var error by remember { mutableStateOf<String?>(null) }

    fun beginDownload() {
        if (apkUrl.isBlank()) {
            error = "Ссылка на обновление не задана. Обратитесь к администратору."
            phase = UpdatePhase.Failed
            return
        }
        error = null
        progress = 0f
        downloadedBytes = 0L
        totalBytes = -1L
        phase = UpdatePhase.Downloading
        scope.launch {
            try {
                val file = ApkUpdater.download(context, apkUrl) { done, total ->
                    downloadedBytes = done
                    totalBytes = total
                    progress = if (total > 0L) {
                        (done.toFloat() / total.toFloat()).coerceIn(0f, 1f)
                    } else {
                        // без Content-Length — «живой» индикатор по объёму
                        ((done % (8L * 1024L * 1024L)).toFloat() / (8f * 1024f * 1024f))
                            .coerceIn(0.05f, 0.95f)
                    }
                }
                phase = UpdatePhase.Installing
                progress = 1f
                ApkUpdater.startInstall(context, file)
            } catch (e: Exception) {
                error = e.message?.takeIf { it.isNotBlank() }
                    ?: "Не удалось скачать или установить обновление"
                phase = UpdatePhase.Failed
            }
        }
    }

    val permissionLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.StartActivityForResult(),
    ) {
        if (ApkUpdater.canInstallPackages(context)) {
            beginDownload()
        } else {
            error = "Разрешите установку из этого источника и нажмите «Обновить» ещё раз."
            phase = UpdatePhase.Failed
        }
    }

    fun onUpdateClick() {
        if (!ApkUpdater.canInstallPackages(context)) {
            phase = UpdatePhase.NeedPermission
            runCatching {
                permissionLauncher.launch(ApkUpdater.installPermissionSettingsIntent(context))
            }.onFailure {
                error = "Откройте настройки и разрешите установку приложений для Voitos."
                phase = UpdatePhase.Failed
            }
            return
        }
        beginDownload()
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(32.dp),
        verticalArrangement = Arrangement.Center,
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Image(
            painter = painterResource(R.drawable.voitos_logo_mark),
            contentDescription = "Voitos",
            modifier = Modifier
                .fillMaxWidth(0.55f)
                .heightIn(max = 160.dp),
            contentScale = ContentScale.Fit,
        )
        Spacer(modifier = Modifier.height(28.dp))
        Text(
            "Просим обновить приложение",
            style = MaterialTheme.typography.headlineSmall,
            color = VoitosColors.Text,
            textAlign = TextAlign.Center,
        )
        Spacer(modifier = Modifier.height(8.dp))
        Text(
            "Сейчас: ${AppVersion.name} (${AppVersion.code})",
            style = MaterialTheme.typography.bodyMedium,
            color = VoitosColors.Muted,
            textAlign = TextAlign.Center,
        )

        when (phase) {
            UpdatePhase.Idle, UpdatePhase.NeedPermission, UpdatePhase.Failed -> {
                if (apkUrl.isNotBlank()) {
                    Spacer(modifier = Modifier.height(24.dp))
                    Button(
                        onClick = { onUpdateClick() },
                        modifier = Modifier.fillMaxWidth(),
                        colors = voitosPrimaryButtonColors(),
                    ) {
                        Text(
                            when (phase) {
                                UpdatePhase.Failed -> "Повторить"
                                UpdatePhase.NeedPermission -> "Разрешить и обновить"
                                else -> "Обновить"
                            },
                        )
                    }
                } else {
                    Spacer(modifier = Modifier.height(16.dp))
                    Text(
                        "Ссылка на APK не настроена на сервере.",
                        color = VoitosColors.Muted,
                        textAlign = TextAlign.Center,
                    )
                }
                error?.let {
                    Spacer(modifier = Modifier.height(12.dp))
                    Text(it, color = VoitosColors.Danger, textAlign = TextAlign.Center)
                }
                if (apkUrl.isNotBlank()) {
                    Spacer(modifier = Modifier.height(8.dp))
                    TextButton(
                        onClick = {
                            runCatching {
                                context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(apkUrl)))
                            }
                        },
                    ) {
                        Text("Открыть ссылку в браузере", color = VoitosColors.Muted)
                    }
                }
            }

            UpdatePhase.Downloading -> {
                Spacer(modifier = Modifier.height(28.dp))
                Text(
                    "Скачивание обновления…",
                    style = MaterialTheme.typography.titleMedium,
                    color = VoitosColors.Text,
                )
                Spacer(modifier = Modifier.height(12.dp))
                LinearProgressIndicator(
                    progress = { progress },
                    modifier = Modifier.fillMaxWidth(),
                    color = VoitosColors.Accent2,
                    trackColor = VoitosColors.Line,
                )
                Spacer(modifier = Modifier.height(8.dp))
                val pct = if (totalBytes > 0L) {
                    "${(progress * 100).toInt()}%"
                } else {
                    ApkUpdater.formatBytes(downloadedBytes)
                }
                Text(
                    if (totalBytes > 0L) {
                        "$pct · ${ApkUpdater.formatBytes(downloadedBytes)} / ${ApkUpdater.formatBytes(totalBytes)}"
                    } else {
                        "Загружено: $pct"
                    },
                    color = VoitosColors.Muted,
                    style = MaterialTheme.typography.bodyMedium,
                )
            }

            UpdatePhase.Installing -> {
                Spacer(modifier = Modifier.height(28.dp))
                CircularProgressIndicator(color = VoitosColors.Accent2)
                Spacer(modifier = Modifier.height(16.dp))
                Text(
                    "Установка…",
                    style = MaterialTheme.typography.titleMedium,
                    color = VoitosColors.Text,
                )
                Spacer(modifier = Modifier.height(8.dp))
                Text(
                    "Подтвердите установку в системном окне Android.",
                    color = VoitosColors.Muted,
                    textAlign = TextAlign.Center,
                    style = MaterialTheme.typography.bodyMedium,
                )
                Spacer(modifier = Modifier.height(16.dp))
                Button(
                    onClick = {
                        runCatching {
                            ApkUpdater.startInstall(context, ApkUpdater.apkFile(context))
                        }.onFailure {
                            error = it.message ?: "Не удалось открыть установщик"
                            phase = UpdatePhase.Failed
                        }
                    },
                    modifier = Modifier.fillMaxWidth(),
                    colors = voitosPrimaryButtonColors(),
                ) { Text("Открыть установщик снова") }
            }
        }
    }
}

private enum class UpdatePhase {
    Idle,
    NeedPermission,
    Downloading,
    Installing,
    Failed,
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
                        client.pinChangeRequest()
                        hint = "Код отправлен в Max — введите его вручную."
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
