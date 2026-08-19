package ru.voitos.app.ui

import android.content.Context
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import ru.voitos.app.ui.theme.VoitosColors

fun isVpnActive(context: Context): Boolean {
    val cm = context.getSystemService(Context.CONNECTIVITY_SERVICE) as? ConnectivityManager
        ?: return false
    return cm.allNetworks.any { network ->
        val caps = cm.getNetworkCapabilities(network) ?: return@any false
        caps.hasTransport(NetworkCapabilities.TRANSPORT_VPN)
    }
}

@Composable
fun VpnDebugBanner(modifier: Modifier = Modifier) {
    val context = LocalContext.current
    val vpn = remember(context) { isVpnActive(context) }
    if (!vpn) return
    Text(
        text = "Похоже, включён VPN. Для работы с локальным сервером (LAN) лучше отключить VPN — иначе приложение может не достучаться до ПК.",
        color = VoitosColors.OnAccent,
        modifier = modifier
            .fillMaxWidth()
            .background(VoitosColors.Warn.copy(alpha = 0.92f), RoundedCornerShape(10.dp))
            .padding(12.dp),
    )
}
