package ru.voitos.app

import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import kotlinx.coroutines.launch
import ru.voitos.app.api.VoitosApiClient
import ru.voitos.app.nav.DeepLinks
import ru.voitos.app.push.DevPushTokenProvider
import ru.voitos.app.ui.CabinetScreen
import ru.voitos.app.ui.CollectionDetailScreen
import ru.voitos.app.ui.CollectionsScreen
import ru.voitos.app.ui.ConfirmAmountScreen
import ru.voitos.app.ui.LoginScreen
import ru.voitos.app.ui.MainShell
import ru.voitos.app.ui.MainTab
import ru.voitos.app.ui.NewWorkRequestScreen
import ru.voitos.app.ui.OnboardingScreen
import ru.voitos.app.ui.SubscriptionScreen
import ru.voitos.app.ui.VoitosBackground
import ru.voitos.app.ui.WorkRequestPhotosScreen
import ru.voitos.app.ui.WorkRequestsScreen
import ru.voitos.app.ui.theme.VoitosTheme

class MainActivity : ComponentActivity() {
    private lateinit var session: SessionStore
    private var client: VoitosApiClient = VoitosApiClient()

    private sealed class Screen {
        data object Login : Screen()
        data object Main : Screen()
        data class CollectionDetail(val id: Int) : Screen()
        data class WorkRequestPhotos(val id: Int) : Screen()
        data object Subscription : Screen()
        data object Onboarding : Screen()
        data class Confirm(val id: Int) : Screen()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        session = SessionStore(this)
        client = VoitosApiClient(baseUrl = session.baseUrl)
        client.accessToken = session.accessToken

        val initial = resolveDeepLink(intent?.data) ?: if (session.isLoggedIn()) {
            Screen.Main
        } else {
            Screen.Login
        }

        setContent {
            var screen by remember { mutableStateOf<Screen>(initial) }
            var tab by remember { mutableStateOf(MainTab.Collections) }
            var playSplash by remember {
                mutableStateOf(session.isLoggedIn() && session.hasLaunchedBefore)
            }
            val scope = rememberCoroutineScope()

            VoitosTheme {
                VoitosBackground {
                    when (val s = screen) {
                        Screen.Login -> LoginScreen(
                            initialBaseUrl = session.baseUrl,
                            initialPhone = session.lastPhone,
                            initialDebugCode = session.lastDebugCode,
                            onLoggedIn = { token, name, baseUrl, phone ->
                                session.baseUrl = baseUrl
                                session.lastPhone = phone
                                session.accessToken = token
                                session.displayName = name
                                client = VoitosApiClient(baseUrl = baseUrl).also {
                                    it.accessToken = token
                                }
                                scope.launch { registerDevPushToken() }
                                playSplash = session.hasLaunchedBefore
                                tab = MainTab.Collections
                                screen = Screen.Main
                            },
                            onDebugPrefs = { url, phone, dbg ->
                                session.baseUrl = url
                                session.lastPhone = phone
                                if (dbg.isNotBlank()) session.lastDebugCode = dbg
                            },
                        )

                        Screen.Main -> MainShell(
                            selected = tab,
                            onSelect = { tab = it },
                            playLogoSplash = playSplash,
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
                                    onBack = null,
                                )
                                MainTab.WorkRequests -> WorkRequestsScreen(
                                    client = client,
                                    onConfirm = { id -> screen = Screen.Confirm(id) },
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
                                    onLogout = {
                                        session.clearSession()
                                        client.accessToken = null
                                        screen = Screen.Login
                                    },
                                )
                            }
                        }

                        is Screen.CollectionDetail -> CollectionDetailScreen(
                            client = client,
                            collectionId = s.id,
                            onBack = {
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

                        Screen.Subscription -> SubscriptionScreen(
                            client = client,
                            onBack = {
                                tab = MainTab.Cabinet
                                screen = Screen.Main
                            },
                        )

                        Screen.Onboarding -> OnboardingScreen(
                            client = client,
                            apiBaseUrl = session.baseUrl,
                            onBack = {
                                tab = MainTab.Cabinet
                                screen = Screen.Main
                            },
                        )

                        is Screen.Confirm -> ConfirmAmountScreen(
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
                if (route.action == "confirm") Screen.Confirm(route.id) else Screen.Main
            is DeepLinks.Route.Subscription -> Screen.Subscription
            else -> Screen.Main
        }
    }
}
