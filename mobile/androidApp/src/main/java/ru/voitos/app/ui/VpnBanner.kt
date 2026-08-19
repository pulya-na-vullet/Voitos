package ru.voitos.app.ui

import android.content.Context
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.core.content.edit
import ru.voitos.app.ui.theme.VoitosColors

private const val VPN_PREFS = "voitos_vpn_banner"
private const val KEY_DISMISSED = "dismissed"

fun isVpnActive(context: Context): Boolean {
    return try {
        val cm = context.getSystemService(Context.CONNECTIVITY_SERVICE) as? ConnectivityManager
            ?: return false
        // Без ACCESS_NETWORK_STATE на части OEM (Huawei) — SecurityException → краш на старте.
        cm.allNetworks.any { network ->
            val caps = cm.getNetworkCapabilities(network) ?: return@any false
            caps.hasTransport(NetworkCapabilities.TRANSPORT_VPN)
        }
    } catch (_: SecurityException) {
        false
    } catch (_: Exception) {
        false
    }
}

@Composable
fun VpnDebugBanner(modifier: Modifier = Modifier) {
    val context = LocalContext.current
    val prefs = remember(context) {
        context.getSharedPreferences(VPN_PREFS, Context.MODE_PRIVATE)
    }
    var vpn by remember { mutableStateOf(isVpnActive(context)) }
    var dismissed by remember {
        mutableStateOf(prefs.getBoolean(KEY_DISMISSED, false))
    }

    LaunchedEffect(Unit) {
        // Периодически не нужно — достаточно на вход в composition и сброс при выкл. VPN.
        vpn = isVpnActive(context)
        if (!vpn && dismissed) {
            prefs.edit(commit = true) { putBoolean(KEY_DISMISSED, false) }
            dismissed = false
        }
    }

    if (!vpn || dismissed) return

    Text(
        text = "Похоже, включён VPN. Для работы с локальным сервером (LAN) лучше отключить VPN — иначе приложение может не достучаться до ПК.\nНажмите, чтобы скрыть.",
        color = VoitosColors.OnAccent,
        modifier = modifier
            .fillMaxWidth()
            .background(VoitosColors.Warn.copy(alpha = 0.92f), RoundedCornerShape(10.dp))
            .clickable {
                prefs.edit(commit = true) { putBoolean(KEY_DISMISSED, true) }
                dismissed = true
            }
            .padding(12.dp),
    )
}
