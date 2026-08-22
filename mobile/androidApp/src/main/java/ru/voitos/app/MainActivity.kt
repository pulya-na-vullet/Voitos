package ru.voitos.app

import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.SystemClock
import androidx.activity.ComponentActivity
import androidx.activity.OnBackPressedCallback
import androidx.activity.compose.BackHandler
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import java.util.concurrent.atomic.AtomicLong
import kotlinx.coroutines.channels.BufferOverflow
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.launch
import ru.voitos.app.AppVersion
import ru.voitos.app.api.VoitosApiClient
import ru.voitos.app.debug.CrashFileLogger
import ru.voitos.app.model.ApiException
import ru.voitos.app.nav.DeepLinks
import ru.voitos.app.push.DevPushTokenProvider
import ru.voitos.app.ui.CabinetScreen
import ru.voitos.app.ui.ChangePinScreen
import ru.voitos.app.ui.CollectionDetailScreen
import ru.voitos.app.ui.CollectionsScreen
import ru.voitos.app.ui.RateMasterScreen
import ru.voitos.app.ui.ConfirmAmountScreen
import ru.voitos.app.ui.ExecutorOffersScreen
import ru.voitos.app.ui.ExecutorRegisterScreen
import ru.voitos.app.ui.FeedbackScreen
import ru.voitos.app.ui.ForceUpdateScreen
import ru.voitos.app.ui.GroupChatScreen
import ru.voitos.app.ui.LoginScreen
import ru.voitos.app.ui.MainShell
import ru.voitos.app.ui.MainTab
import ru.voitos.app.ui.NewWorkRequestScreen
import ru.voitos.app.ui.OnboardingScreen
import ru.voitos.app.ui.SetPinScreen
import ru.voitos.app.ui.SubscriptionScreen
import ru.voitos.app.ui.VoitosBackground
import ru.voitos.app.ui.WishScreen
import ru.voitos.app.ui.WorkRequestDetailScreen
import ru.voitos.app.ui.WorkRequestPhotosScreen
import ru.voitos.app.ui.WorkRequestsScreen
import ru.voitos.app.ui.theme.VoitosColors
import ru.voitos.app.ui.theme.VoitosTheme

class MainActivity : ComponentActivity() {
    private lateinit var session: SessionStore
    private var client: VoitosApiClient = VoitosApiClient()

    /** Последнее касание / жест — для авто-разлогина по простою. */
    private val lastUserInteractionMs = AtomicLong(SystemClock.elapsedRealtime())

    /** Бэкенд вернул 401 (пользователь удалён / токен отозван) — разлогинить в Compose. */
    private val sessionExpired = MutableSharedFlow<Unit>(
        extraBufferCapacity = 1,
        onBufferOverflow = BufferOverflow.DROP_OLDEST,
    )

    /**
     * Актуальный обработчик «назад» из Compose. Activity-callback нужен, потому что
     * жестовый свайп при predictive back часто не доходит до Compose BackHandler.
     */
    private var composeBackHandler: (() -> Unit)? = null

    companion object {
        private const val IDLE_LOGOUT_MS = 15 * 60 * 1000L
        private const val IDLE_CHECK_EVERY_MS = 15_000L
        private const val VERSION_POLL_EVERY_MS = 30_000L
    }

    private fun newApiClient(baseUrl: String, token: String?): VoitosApiClient =
        VoitosApiClient(baseUrl = baseUrl).also { api ->
            api.appVersionCode = AppVersion.code
            api.accessToken = token
            api.onUnauthorized = { sessionExpired.tryEmit(Unit) }
        }

    private sealed class Screen {
        data object Login : Screen()
        data object ForceUpdate : Screen()
        data object SetPin : Screen()
        data object ChangePin : Screen()
        data object Wish : Screen()
        /** Проверка онбординга перед сплэшем / главной. */
        data object Bootstrapping : Screen()
        /** Обязательный онбординг (ещё не пройден на бэкенде). */
        data object RequiredOnboarding : Screen()
        data object Main : Screen()
        data class CollectionDetail(val id: Int) : Screen()
        data class WorkRequestPhotos(val id: Int) : Screen()
        data class WorkRequestDetail(val id: Int) : Screen()
        data object Subscription : Screen()
        /** Подписка закрыта — только загрузка чека. */
        data object Paywall : Screen()
        data object Onboarding : Screen()
        data object Feedback : Screen()
        data object GroupChat : Screen()
        data object ExecutorRegister : Screen()
        data class Confirm(val id: Int) : Screen()
        data class Rate(val id: Int) : Screen()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        CrashFileLogger.install(this)

        // Activity-level callback: жест «назад» идёт через OnBackPressedDispatcher.
        // enableOnBackInvokedCallback=false в манифесте отключает predictive back,
        // иначе система может сразу сворачивать Activity, минуя Compose BackHandler.
        onBackPressedDispatcher.addCallback(
            this,
            object : OnBackPressedCallback(true) {
                override fun handleOnBackPressed() {
                    composeBackHandler?.invoke() ?: moveTaskToBack(true)
                }
            },
        )

        session = SessionStore(this)
        val restoredServerUrl = DevServerSettings.restoreIfNeeded(this, session)
        client = newApiClient(session.baseUrl, session.accessToken)

        val lastCrash = CrashFileLogger.consumeLastCrash(this)
        val deepLinkScreen = resolveDeepLink(intent?.data)
        val initial = when {
            session.isLoggedIn() -> Screen.Bootstrapping
            else -> Screen.Login
        }

        try {
            setContent {
                var screen by remember { mutableStateOf<Screen>(initial) }
                var tab by remember { mutableStateOf(MainTab.Collections) }
                var collectionsRefresh by remember { mutableStateOf(0) }
                var chatUnread by remember { mutableStateOf(0) }
                var isExecutor by remember { mutableStateOf(false) }
                var playSplash by remember { mutableStateOf(false) }
                var pendingAfterBootstrap by remember { mutableStateOf(deepLinkScreen) }
                var crashText by remember { mutableStateOf(lastCrash) }
                var updateApkUrl by remember { mutableStateOf("") }
                val scope = rememberCoroutineScope()

                fun enterMainWithSplash(splash: Boolean) {
                    playSplash = splash
                    screen = Screen.Main
                }

                fun goForceUpdate(apkUrl: String = "") {
                    updateApkUrl = apkUrl
                    screen = Screen.ForceUpdate
                }

                fun appNeedsUpdate(health: ru.voitos.app.model.HealthResponse?): Boolean {
                    if (health == null) return false
                    if (health.updateRequired) return true
                    return health.minAppVersionCode > 0 && AppVersion.code < health.minAppVersionCode
                }

                /** После онбординга — hard update, paywall или главный экран. */
                fun proceedAfterOnboarding() {
                    scope.launch {
                        val health = runCatching {
                            client.healthCheck(AppVersion.code)
                        }.getOrNull()
                        if (appNeedsUpdate(health)) {
                            goForceUpdate(apkUrl = health?.apkUrl.orEmpty())
                            return@launch
                        }
                        val access = runCatching { client.access() }.getOrNull()
                        val needsPay = access?.needsPayment == true || access?.state == "blocked"
                        if (needsPay) {
                            playSplash = false
                            screen = Screen.Paywall
                            return@launch
                        }
                        val next = pendingAfterBootstrap
                        pendingAfterBootstrap = null
                        if (next != null && next !is Screen.Main) {
                            playSplash = false
                            screen = next
                        } else {
                            enterMainWithSplash(splash = true)
                        }
                    }
                }

                fun logoutToLogin() {
                    session.clearSession()
                    client.accessToken = null
                    client.onUnauthorized = null
                    isExecutor = false
                    playSplash = false
                    lastUserInteractionMs.set(SystemClock.elapsedRealtime())
                    screen = Screen.Login
                }

                fun goMain(targetTab: MainTab = tab, refreshCollections: Boolean = false) {
                    tab = targetTab
                    if (refreshCollections || targetTab == MainTab.Collections) {
                        collectionsRefresh += 1
                    }
                    screen = Screen.Main
                }

                // Пользователя удалили в админке / токен отозвали → принудительный выход.
                LaunchedEffect(Unit) {
                    sessionExpired.collect { logoutToLogin() }
                }

                // Пока сессия жива — периодически сверяем min version с бэком (не только при старте).
                LaunchedEffect(screen, client.accessToken) {
                    val activeSession = !client.accessToken.isNullOrBlank() &&
                        screen !is Screen.Login &&
                        screen !is Screen.ForceUpdate
                    if (!activeSession) return@LaunchedEffect
                    while (true) {
                        val health = runCatching {
                            client.healthCheck(AppVersion.code)
                        }.getOrNull()
                        if (appNeedsUpdate(health)) {
                            goForceUpdate(apkUrl = health?.apkUrl.orEmpty())
                            break
                        }
                        // Подписка могла закончиться, пока пользователь в приложении.
                        if (screen !is Screen.Paywall && screen !is Screen.Bootstrapping) {
                            val access = runCatching { client.access() }.getOrNull()
                            if (access?.needsPayment == true || access?.state == "blocked") {
                                playSplash = false
                                screen = Screen.Paywall
                                break
                            }
                        }
                        delay(VERSION_POLL_EVERY_MS)
                    }
                }

                // 15 минут без касаний → разлогин (ForceUpdate / Login не трогаем).
                LaunchedEffect(screen, client.accessToken) {
                    val watchIdle = !client.accessToken.isNullOrBlank() &&
                        screen !is Screen.Login &&
                        screen !is Screen.ForceUpdate
                    if (!watchIdle) return@LaunchedEffect
                    while (true) {
                        delay(IDLE_CHECK_EVERY_MS)
                        val idleFor = SystemClock.elapsedRealtime() - lastUserInteractionMs.get()
                        if (idleFor >= IDLE_LOGOUT_MS) {
                            logoutToLogin()
                            break
                        }
                    }
                }

                fun handleSystemBack() {
                    when (val current = screen) {
                        Screen.Login, Screen.Bootstrapping, Screen.RequiredOnboarding, Screen.SetPin, Screen.ForceUpdate, Screen.Main, Screen.Paywall ->
                            moveTaskToBack(true)
                        is Screen.CollectionDetail, Screen.GroupChat ->
                            goMain(MainTab.Collections, refreshCollections = true)
                        Screen.Subscription, Screen.Onboarding, Screen.Feedback, Screen.ExecutorRegister, Screen.ChangePin, Screen.Wish ->
                            goMain(MainTab.Cabinet)
                        is Screen.WorkRequestPhotos -> goMain(MainTab.CallMaster)
                        is Screen.WorkRequestDetail, is Screen.Confirm, is Screen.Rate -> goMain(MainTab.WorkRequests)
                    }
                }

                fun onBackPressedUnified() {
                    if (crashText != null) {
                        crashText = null
                    } else {
                        handleSystemBack()
                    }
                }

                // Держим Activity-callback в синхроне с актуальным screen/crashText.
                androidx.compose.runtime.SideEffect {
                    composeBackHandler = { onBackPressedUnified() }
                }
                DisposableEffect(Unit) {
                    onDispose { composeBackHandler = null }
                }

                // Compose BackHandler: на вложенных экранах (чат, деталь) их свои
                // BackHandler регистрируются выше в стеке и срабатывают первыми.
                // Activity-callback ниже — запасной путь для жеста/кнопки.
                BackHandler(enabled = true) {
                    onBackPressedUnified()
                }

                VoitosTheme {
                    VoitosBackground {
                        if (crashText != null) {
                            AlertDialog(
                                onDismissRequest = { crashText = null },
                                title = { Text("Прошлый краш (без logcat)", color = VoitosColors.Text) },
                                text = {
                                    Column(
                                        modifier = Modifier
                                            .fillMaxWidth()
                                            .heightIn(max = 360.dp)
                                            .verticalScroll(rememberScrollState()),
                                    ) {
                                        Text(
                                            text = crashText.orEmpty(),
                                            fontSize = 11.sp,
                                            color = VoitosColors.Muted,
                                        )
                                    }
                                },
                                confirmButton = {
                                    TextButton(onClick = { crashText = null }) {
                                        Text("Закрыть", color = VoitosColors.Accent)
                                    }
                                },
                                containerColor = VoitosColors.Panel,
                            )
                        }

                        when (val s = screen) {
                            Screen.Login -> LoginScreen(
                                initialBaseUrl = session.baseUrl,
                                initialPhone = session.lastPhone,
                                initialDebugCode = session.lastDebugCode,
                                recentBaseUrls = session.recentBaseUrls,
                                restoredFromDisk = restoredServerUrl != null,
                                preferPinLogin = session.hasPinSetup && session.lastPhone.isNotBlank(),
                                onLoggedIn = { result ->
                                    DevServerSettings.save(this@MainActivity, session, result.baseUrl)
                                    session.lastPhone = result.phone
                                    session.accessToken = result.token
                                    session.displayName = result.name
                                    if (result.hasPin || !result.needsPinSetup) {
                                        session.hasPinSetup = true
                                    }
                                    session.needsOnboarding = result.needsOnboarding
                                    client = newApiClient(result.baseUrl, result.token)
                                    scope.launch { registerDevPushToken() }
                                    pendingAfterBootstrap = null
                                    tab = MainTab.Collections
                                    lastUserInteractionMs.set(SystemClock.elapsedRealtime())
                                    screen = if (result.needsPinSetup) Screen.SetPin else Screen.Bootstrapping
                                },
                                onDebugPrefs = { url, phone, dbg ->
                                    session.baseUrl = url
                                    session.lastPhone = phone
                                    if (dbg.isNotBlank()) session.lastDebugCode = dbg
                                },
                                onSaveServer = { url ->
                                    DevServerSettings.save(this@MainActivity, session, url)
                                },
                            )

                            Screen.ForceUpdate -> ForceUpdateScreen(
                                apkUrl = updateApkUrl,
                            )

                            Screen.SetPin -> SetPinScreen(
                                client = client,
                                onDone = {
                                    session.hasPinSetup = true
                                    screen = Screen.Bootstrapping
                                },
                            )

                            Screen.Bootstrapping -> {
                                Box(
                                    modifier = Modifier.fillMaxSize(),
                                    contentAlignment = Alignment.Center,
                                ) {
                                    CircularProgressIndicator(color = VoitosColors.Accent2)
                                }
                                LaunchedEffect(Unit) {
                                    // Сначала hard update — до онбординга и PIN-сессии.
                                    var health: ru.voitos.app.model.HealthResponse? = null
                                    repeat(3) {
                                        health = runCatching {
                                            client.healthCheck(AppVersion.code)
                                        }.getOrNull()
                                        if (health != null) return@repeat
                                        delay(400)
                                    }
                                    if (health == null) {
                                        val cfg = runCatching { client.authConfig() }.getOrNull()
                                        if (cfg != null && cfg.minAppVersionCode > AppVersion.code) {
                                            goForceUpdate(apkUrl = cfg.apkUrl)
                                            return@LaunchedEffect
                                        }
                                    } else if (appNeedsUpdate(health)) {
                                        goForceUpdate(apkUrl = health?.apkUrl.orEmpty())
                                        return@LaunchedEffect
                                    }

                                    val needOnboarding = try {
                                        client.onboarding().requiresOnboarding()
                                    } catch (e: ApiException) {
                                        if (e.code == "unauthorized") {
                                            logoutToLogin()
                                            return@LaunchedEffect
                                        }
                                        session.needsOnboarding
                                    } catch (_: Exception) {
                                        session.needsOnboarding
                                    }
                                    if (needOnboarding) {
                                        session.needsOnboarding = true
                                        screen = Screen.RequiredOnboarding
                                    } else {
                                        session.needsOnboarding = false
                                        proceedAfterOnboarding()
                                    }
                                }
                            }

                            Screen.RequiredOnboarding -> OnboardingScreen(
                                client = client,
                                apiBaseUrl = session.baseUrl,
                                onBack = { proceedAfterOnboarding() },
                                requireCompletion = true,
                                onFinished = {
                                    session.needsOnboarding = false
                                    proceedAfterOnboarding()
                                },
                            )

                            Screen.Main -> {
                                LaunchedEffect(client.accessToken, screen) {
                                    isExecutor = runCatching {
                                        client.executorMe().isExecutor
                                    }.getOrDefault(false)
                                    if (!isExecutor && tab == MainTab.Work) {
                                        tab = MainTab.Cabinet
                                    }
                                }
                                // Бейдж чата обновляем на всём Main, не только на вкладке Сборы.
                                LaunchedEffect(client.accessToken, screen) {
                                    while (true) {
                                        val n = runCatching {
                                            client.groups().unreadTotal
                                        }.getOrNull()
                                        if (n != null) chatUnread = n
                                        delay(5_000)
                                    }
                                }
                                MainShell(
                                    selected = tab,
                                    onSelect = {
                                        if (it == MainTab.Collections) {
                                            collectionsRefresh += 1
                                        }
                                        tab = it
                                    },
                                    playLogoSplash = playSplash,
                                    showWorkTab = isExecutor,
                                    onSplashFinished = {
                                        playSplash = false
                                        if (!session.hasLaunchedBefore) {
                                            session.hasLaunchedBefore = true
                                        }
                                    },
                                ) {
                                    when (tab) {
                                        MainTab.Collections -> CollectionsScreen(
                                            client = client,
                                            onOpen = { id -> screen = Screen.CollectionDetail(id) },
                                            onOpenChat = { screen = Screen.GroupChat },
                                            onBack = null,
                                            refreshKey = collectionsRefresh,
                                            chatUnread = chatUnread,
                                            onChatUnreadChange = { chatUnread = it },
                                        )
                                        MainTab.WorkRequests -> WorkRequestsScreen(
                                            client = client,
                                            onConfirm = { id -> screen = Screen.Confirm(id) },
                                            onRate = { id -> screen = Screen.Rate(id) },
                                            onOpen = { id -> screen = Screen.WorkRequestDetail(id) },
                                            onBack = null,
                                        )
                                        MainTab.CallMaster -> NewWorkRequestScreen(
                                            client = client,
                                            onCreated = { id, needsPhotos ->
                                                screen = if (needsPhotos) {
                                                    Screen.WorkRequestPhotos(id)
                                                } else {
                                                    tab = MainTab.WorkRequests
                                                    Screen.Main
                                                }
                                            },
                                            onBack = null,
                                        )
                                        MainTab.Cabinet -> CabinetScreen(
                                            client = client,
                                            onOpenSubscription = { screen = Screen.Subscription },
                                            onOpenOnboarding = { screen = Screen.Onboarding },
                                            onRegisterExecutor = { screen = Screen.ExecutorRegister },
                                            onOpenFeedback = { screen = Screen.Feedback },
                                            onOpenWishes = { screen = Screen.Wish },
                                            onChangePin = { screen = Screen.ChangePin },
                                            onLogout = { logoutToLogin() },
                                        )
                                        MainTab.Work -> ExecutorOffersScreen(
                                            client = client,
                                            onBack = null,
                                        )
                                    }
                                }
                            }

                            Screen.ExecutorRegister -> ExecutorRegisterScreen(
                                client = client,
                                onDone = {
                                    isExecutor = true
                                    tab = MainTab.Work
                                    screen = Screen.Main
                                },
                                onBack = {
                                    tab = MainTab.Cabinet
                                    screen = Screen.Main
                                },
                            )

                            is Screen.CollectionDetail -> CollectionDetailScreen(
                                client = client,
                                collectionId = s.id,
                                onBack = {
                                    collectionsRefresh += 1
                                    tab = MainTab.Collections
                                    screen = Screen.Main
                                },
                            )

                            is Screen.WorkRequestPhotos -> WorkRequestPhotosScreen(
                                client = client,
                                workRequestId = s.id,
                                onSubmitted = {
                                    tab = MainTab.WorkRequests
                                    screen = Screen.Main
                                },
                                onBack = {
                                    tab = MainTab.CallMaster
                                    screen = Screen.Main
                                },
                            )

                            is Screen.WorkRequestDetail -> WorkRequestDetailScreen(
                                client = client,
                                workRequestId = s.id,
                                onBack = {
                                    tab = MainTab.WorkRequests
                                    screen = Screen.Main
                                },
                                onConfirmAmount = { id -> screen = Screen.Confirm(id) },
                                onRate = { id -> screen = Screen.Rate(id) },
                            )

                            Screen.Subscription -> SubscriptionScreen(
                                client = client,
                                onBack = {
                                    tab = MainTab.Cabinet
                                    screen = Screen.Main
                                },
                            )

                            Screen.Paywall -> SubscriptionScreen(
                                client = client,
                                paymentRequired = true,
                                onBack = { logoutToLogin() },
                                onAccessRestored = {
                                    pendingAfterBootstrap = null
                                    enterMainWithSplash(splash = true)
                                },
                                onLogout = { logoutToLogin() },
                            )

                            Screen.Onboarding -> OnboardingScreen(
                                client = client,
                                apiBaseUrl = session.baseUrl,
                                onBack = {
                                    tab = MainTab.Cabinet
                                    screen = Screen.Main
                                },
                            )

                            Screen.Feedback -> FeedbackScreen(
                                client = client,
                                onBack = {
                                    tab = MainTab.Cabinet
                                    screen = Screen.Main
                                },
                            )

                            Screen.Wish -> WishScreen(
                                client = client,
                                onBack = {
                                    tab = MainTab.Cabinet
                                    screen = Screen.Main
                                },
                            )

                            Screen.ChangePin -> ChangePinScreen(
                                client = client,
                                onBack = {
                                    tab = MainTab.Cabinet
                                    screen = Screen.Main
                                },
                            )

                            Screen.GroupChat -> GroupChatScreen(
                                client = client,
                                onBack = {
                                    scope.launch {
                                        chatUnread = runCatching {
                                            client.groups().unreadTotal
                                        }.getOrDefault(0)
                                    }
                                    goMain(MainTab.Collections, refreshCollections = true)
                                },
                            )

                            is Screen.Confirm -> ConfirmAmountScreen(
                                client = client,
                                workRequestId = s.id,
                                onDone = { needsRating ->
                                    if (needsRating) {
                                        screen = Screen.Rate(s.id)
                                    } else {
                                        tab = MainTab.WorkRequests
                                        screen = Screen.Main
                                    }
                                },
                                onBack = {
                                    tab = MainTab.WorkRequests
                                    screen = Screen.Main
                                },
                            )

                            is Screen.Rate -> RateMasterScreen(
                                client = client,
                                workRequestId = s.id,
                                onDone = {
                                    tab = MainTab.WorkRequests
                                    screen = Screen.Main
                                },
                                onBack = {
                                    tab = MainTab.WorkRequests
                                    screen = Screen.Main
                                },
                            )
                        }
                    }
                }
            }
        } catch (t: Throwable) {
            runCatching { CrashFileLogger.install(this) }
            throw t
        }
    }

    override fun onUserInteraction() {
        super.onUserInteraction()
        lastUserInteractionMs.set(SystemClock.elapsedRealtime())
    }

    override fun onResume() {
        super.onResume()
        lastUserInteractionMs.set(SystemClock.elapsedRealtime())
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
    }

    private suspend fun registerDevPushToken() {
        val provider = DevPushTokenProvider(
            deviceLabel = Build.MODEL.replace(' ', '-'),
            sessionSuffix = session.accessToken?.takeLast(8) ?: "anon",
        )
        try {
            client.registerDevice(provider.currentToken(), "android")
        } catch (_: Exception) {
        }
    }

    private fun resolveDeepLink(uri: Uri?): Screen? {
        if (uri == null) return null
        return screenFromDeepLink(uri.toString())
    }

    private fun screenFromDeepLink(link: String): Screen? {
        return when (val route = DeepLinks.parse(link)) {
            is DeepLinks.Route.Collection -> Screen.CollectionDetail(route.id)
            is DeepLinks.Route.WorkRequest ->
                when (route.action) {
                    "confirm" -> Screen.Confirm(route.id)
                    "rate" -> Screen.Rate(route.id)
                    else -> Screen.WorkRequestDetail(route.id)
                }
            is DeepLinks.Route.Subscription -> Screen.Subscription
            is DeepLinks.Route.Feedback -> Screen.Feedback
            else -> Screen.Main
        }
    }
}
