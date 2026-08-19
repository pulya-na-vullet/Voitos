package ru.voitos.app

import android.content.Intent
import android.net.Uri
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import ru.voitos.app.api.VoitosApiClient
import ru.voitos.app.nav.DeepLinks
import ru.voitos.app.ui.CollectionsScreen
import ru.voitos.app.ui.ConfirmAmountScreen
import ru.voitos.app.ui.InboxScreen
import ru.voitos.app.ui.LoginScreen
import ru.voitos.app.ui.WorkRequestsScreen

class MainActivity : ComponentActivity() {
    private lateinit var session: SessionStore
    private lateinit var client: VoitosApiClient

    private sealed class Screen {
        data object Login : Screen()
        data object Inbox : Screen()
        data object Collections : Screen()
        data object WorkRequests : Screen()
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
            MaterialTheme {
                when (val s = screen) {
                    Screen.Login -> LoginScreen(client) { token, name ->
                        session.accessToken = token
                        session.displayName = name
                        client.accessToken = token
                        screen = Screen.Inbox
                    }
                    Screen.Inbox -> InboxScreen(
                        client = client,
                        onOpenDeepLink = { link ->
                            screen = screenFromDeepLink(link) ?: Screen.Inbox
                        },
                        onOpenCollections = { screen = Screen.Collections },
                        onOpenWorkRequests = { screen = Screen.WorkRequests },
                        onLogout = {
                            session.clear()
                            client.accessToken = null
                            screen = Screen.Login
                        },
                    )
                    Screen.Collections -> CollectionsScreen(client) { screen = Screen.Inbox }
                    Screen.WorkRequests -> WorkRequestsScreen(
                        client = client,
                        onConfirm = { id -> screen = Screen.Confirm(id) },
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
        // Deep link while running — activity recreates content via process if needed.
        setIntent(intent)
    }

    private fun resolveDeepLink(uri: Uri?): Screen? {
        if (uri == null) return null
        return screenFromDeepLink(uri.toString())
    }

    private fun screenFromDeepLink(link: String): Screen? {
        return when (val route = DeepLinks.parse(link)) {
            is DeepLinks.Route.Collection -> Screen.Collections
            is DeepLinks.Route.WorkRequest ->
                if (route.action == "confirm") Screen.Confirm(route.id) else Screen.WorkRequests
            is DeepLinks.Route.Subscription -> Screen.Inbox
            else -> Screen.Inbox
        }
    }
}
