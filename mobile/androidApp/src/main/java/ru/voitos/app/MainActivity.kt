package ru.voitos.app

import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import kotlinx.coroutines.launch
import ru.voitos.app.api.VoitosApiClient
import ru.voitos.app.nav.DeepLinks
import ru.voitos.app.ui.CollectionDetailScreen
import ru.voitos.app.ui.CollectionsScreen
import ru.voitos.app.ui.ConfirmAmountScreen
import ru.voitos.app.ui.InboxScreen
import ru.voitos.app.ui.LoginScreen
import ru.voitos.app.ui.NewWorkRequestScreen
import ru.voitos.app.ui.SubscriptionScreen
import ru.voitos.app.ui.WorkRequestsScreen

class MainActivity : ComponentActivity() {
    private lateinit var session: SessionStore
    private lateinit var client: VoitosApiClient

    private sealed class Screen {
        data object Login : Screen()
        data object Inbox : Screen()
        data object Collections : Screen()
        data class CollectionDetail(val id: Int) : Screen()
        data object WorkRequests : Screen()
        data object NewWorkRequest : Screen()
        data object Subscription : Screen()
        data class Confirm(val id: Int) : Screen()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        session = SessionStore(this)
        client = VoitosApiClient(baseUrl = session.baseUrl)
        client.accessToken = session.accessToken

        val initial = resolveDeepLink(intent?.data) ?: if (session.isLoggedIn()) {
            Screen.Inbox
        } else {
            Screen.Login
        }

        setContent {
            var screen by remember { mutableStateOf<Screen>(initial) }
            val scope = rememberCoroutineScope()
            MaterialTheme {
                when (val s = screen) {
                    Screen.Login -> LoginScreen(client) { token, name ->
                        session.accessToken = token
                        session.displayName = name
                        client.accessToken = token
                        scope.launch { registerDevPushToken() }
                        screen = Screen.Inbox
                    }
                    Screen.Inbox -> InboxScreen(
                        client = client,
                        onOpenDeepLink = { link ->
                            screen = screenFromDeepLink(link) ?: Screen.Inbox
                        },
                        onOpenCollections = { screen = Screen.Collections },
                        onOpenWorkRequests = { screen = Screen.WorkRequests },
                        onOpenSubscription = { screen = Screen.Subscription },
                        onNewWorkRequest = { screen = Screen.NewWorkRequest },
                        onLogout = {
                            session.clear()
                            client.accessToken = null
                            screen = Screen.Login
                        },
                    )
                    Screen.Collections -> CollectionsScreen(
                        client = client,
                        onOpen = { id -> screen = Screen.CollectionDetail(id) },
                        onBack = { screen = Screen.Inbox },
                    )
                    is Screen.CollectionDetail -> CollectionDetailScreen(
                        client = client,
                        collectionId = s.id,
                        onBack = { screen = Screen.Collections },
                    )
                    Screen.WorkRequests -> WorkRequestsScreen(
                        client = client,
                        onConfirm = { id -> screen = Screen.Confirm(id) },
                        onBack = { screen = Screen.Inbox },
                    )
                    Screen.NewWorkRequest -> NewWorkRequestScreen(
                        client = client,
                        onCreated = { screen = Screen.WorkRequests },
                        onBack = { screen = Screen.Inbox },
                    )
                    Screen.Subscription -> SubscriptionScreen(
                        client = client,
                        onBack = { screen = Screen.Inbox },
                    )
                    is Screen.Confirm -> ConfirmAmountScreen(
                        client = client,
                        workRequestId = s.id,
                        onDone = { screen = Screen.Inbox },
                        onBack = { screen = Screen.WorkRequests },
                    )
                }
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
    }

    private suspend fun registerDevPushToken() {
        // До Firebase SDK: стабильный dev-токен, чтобы сервер мог dry-run FCM.
        val token = "dev-${Build.MODEL}-${session.accessToken?.takeLast(8) ?: "anon"}"
        try {
            client.registerDevice(token.take(120), "android")
        } catch (_: Exception) {
            // API может быть недоступен в offline-сборке
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
                if (route.action == "confirm") Screen.Confirm(route.id) else Screen.WorkRequests
            is DeepLinks.Route.Subscription -> Screen.Subscription
            else -> Screen.Inbox
        }
    }
}
