package ru.voitos.app

import android.content.Context
import android.net.ConnectivityManager
import android.net.LinkProperties
import android.net.wifi.WifiManager
import android.os.Build
import java.net.HttpURLConnection
import java.net.Inet4Address
import java.net.NetworkInterface
import java.net.URL
import java.util.concurrent.atomic.AtomicReference
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.sync.Semaphore
import kotlinx.coroutines.sync.withPermit
import kotlinx.coroutines.withContext

/**
 * Поиск Voitos на LAN: сканирует /24 подсеть телефона по :port/api/v1/health.
 * Нужен, когда APK ставится «как новый» и prefs с IP сброшены.
 *
 * На части OEM (Meizu/Flyme и др.) NetworkInterface пустой — берём IP через
 * ConnectivityManager / WifiManager, дольше ждём ответ и сначала бьём gateway.
 */
object LanServerDiscovery {
    data class Found(
        val baseUrl: String,
        val host: String,
        val port: Int,
    )

    data class LocalNet(
        val phoneIp: String?,
        val prefix: String?,
        val gateways: List<String>,
    )

    suspend fun find(
        port: Int = 18765,
        preferHosts: List<String> = emptyList(),
        context: Context? = null,
    ): List<Found> = withContext(Dispatchers.IO) {
        val found = linkedMapOf<String, Found>()
        val prefer = preferHosts.mapNotNull { hostFromUrlOrIp(it) }.distinct()

        for (host in prefer) {
            probe(host, port)?.let { found[it.host] = it }
        }
        if (found.isNotEmpty()) return@withContext found.values.toList()

        val net = localNet(context)
        val prefix = net.prefix ?: return@withContext emptyList()
        for (gw in net.gateways) {
            probe(gw, port)?.let { found[it.host] = it }
        }
        for (i in listOf(1, 2, 9, 10, 100, 101, 102, 110, 200, 254)) {
            probe("$prefix.$i", port)?.let { found[it.host] = it }
        }
        if (found.isNotEmpty()) return@withContext found.values.toList()

        val sem = Semaphore(24)
        coroutineScope {
            (1..254).map { i ->
                async {
                    sem.withPermit {
                        probe("$prefix.$i", port)
                    }
                }
            }.awaitAll().forEach { hit ->
                if (hit != null) found[hit.host] = hit
            }
        }
        found.values.toList()
    }

    suspend fun findFirst(
        port: Int = 18765,
        preferHosts: List<String> = emptyList(),
        context: Context? = null,
    ): Found? = withContext(Dispatchers.IO) {
        val prefer = preferHosts.mapNotNull { hostFromUrlOrIp(it) }.distinct()
        for (host in prefer) {
            probe(host, port)?.let { return@withContext it }
        }
        val net = localNet(context)
        val prefix = net.prefix ?: return@withContext null
        val firstHit = AtomicReference<Found?>(null)

        // Сначала шлюз и частые хосты — быстрее на медленных OEM.
        val priority = buildList {
            addAll(net.gateways.mapNotNull { it.substringAfterLast('.').toIntOrNull() })
            addAll(listOf(1, 2, 9, 10, 100, 101, 102, 110, 200, 254))
        }.distinct()
        for (i in priority) {
            probe("$prefix.$i", port)?.let { return@withContext it }
        }

        val ordered = (1..254).toList()
        val sem = Semaphore(32)
        coroutineScope {
            ordered.map { i ->
                async {
                    if (firstHit.get() != null) return@async
                    sem.withPermit {
                        if (firstHit.get() != null) return@withPermit
                        probe("$prefix.$i", port)?.let { firstHit.compareAndSet(null, it) }
                    }
                }
            }.awaitAll()
        }
        firstHit.get()
    }

    fun localNet(context: Context?): LocalNet {
        val gateways = linkedSetOf<String>()
        var phoneIp: String? = null

        if (context != null) {
            runCatching {
                val cm = context.applicationContext.getSystemService(Context.CONNECTIVITY_SERVICE)
                    as? ConnectivityManager
                val network = cm?.activeNetwork
                val props: LinkProperties? = network?.let { cm.getLinkProperties(it) }
                props?.linkAddresses?.forEach { la ->
                    val a = la.address
                    if (a is Inet4Address && !a.isLoopbackAddress) {
                        val host = a.hostAddress ?: return@forEach
                        if (isPrivateIpv4(host) && phoneIp == null) {
                            phoneIp = host
                        }
                    }
                }
                props?.routes?.forEach { route ->
                    val gw = route.gateway
                    if (gw is Inet4Address && !gw.isLoopbackAddress) {
                        val host = gw.hostAddress ?: return@forEach
                        if (isPrivateIpv4(host)) gateways.add(host)
                    }
                }
            }
            if (phoneIp == null) {
                runCatching {
                    @Suppress("DEPRECATION")
                    val wm = context.applicationContext.getSystemService(Context.WIFI_SERVICE)
                        as? WifiManager
                    val dhcp = wm?.dhcpInfo
                    if (dhcp != null) {
                        if (dhcp.ipAddress != 0) {
                            phoneIp = intToIpv4(dhcp.ipAddress)
                        }
                        if (dhcp.gateway != 0) {
                            gateways.add(intToIpv4(dhcp.gateway))
                        }
                        if (dhcp.serverAddress != 0) {
                            gateways.add(intToIpv4(dhcp.serverAddress))
                        }
                    }
                }
            }
        }

        if (phoneIp == null) {
            phoneIp = localIpv4FromInterfaces()
        }

        val prefix = phoneIp?.let { ip ->
            val parts = ip.split('.')
            if (parts.size == 4) "${parts[0]}.${parts[1]}.${parts[2]}" else null
        }
        if (prefix != null) {
            listOf(1, 2, 254).forEach { gateways.add("$prefix.$it") }
        }
        return LocalNet(phoneIp = phoneIp, prefix = prefix, gateways = gateways.toList())
    }

    /** Совместимость со старыми вызовами. */
    fun localSubnetPrefix(): String? = localNet(null).prefix

    fun localIpv4(): String? = localNet(null).phoneIp

    private fun localIpv4FromInterfaces(): String? {
        val faces = NetworkInterface.getNetworkInterfaces() ?: return null
        for (iface in faces) {
            if (!iface.isUp || iface.isLoopback) continue
            val addrs = iface.inetAddresses
            while (addrs.hasMoreElements()) {
                val a = addrs.nextElement()
                if (a is Inet4Address && !a.isLoopbackAddress) {
                    val host = a.hostAddress ?: continue
                    if (isPrivateIpv4(host)) return host
                }
            }
        }
        return null
    }

    private fun isPrivateIpv4(host: String): Boolean =
        host.startsWith("192.168.") ||
            host.startsWith("10.") ||
            host.matches(Regex("""172\.(1[6-9]|2\d|3[0-1])\..*"""))

    /** WifiManager dhcp ints are little-endian on Android. */
    private fun intToIpv4(value: Int): String {
        val v = if (Build.VERSION.SDK_INT >= 21) {
            // dhcpInfo fields are little-endian
            value
        } else {
            value
        }
        return listOf(
            v and 0xff,
            v shr 8 and 0xff,
            v shr 16 and 0xff,
            v shr 24 and 0xff,
        ).joinToString(".")
    }

    private fun hostFromUrlOrIp(raw: String): String? {
        val t = raw.trim()
        if (t.isBlank()) return null
        return try {
            if (t.startsWith("http")) URI_SAFE(t)?.host else t.substringBefore(':').takeIf { it.isNotBlank() }
        } catch (_: Exception) {
            null
        }
    }

    private fun URI_SAFE(s: String) = try {
        java.net.URI(s)
    } catch (_: Exception) {
        null
    }

    private fun probe(host: String, port: Int): Found? {
        val base = "http://$host:$port/api/v1"
        var conn: HttpURLConnection? = null
        return try {
            conn = (URL("$base/health").openConnection() as HttpURLConnection).apply {
                connectTimeout = 700
                readTimeout = 700
                requestMethod = "GET"
                instanceFollowRedirects = false
                setRequestProperty("Connection", "close")
            }
            val code = conn.responseCode
            if (code in 200..299) {
                val body = runCatching { conn.inputStream.bufferedReader().readText() }.getOrDefault("")
                if ("ok" in body.lowercase() || body.isBlank() || code == 200) {
                    Found(baseUrl = base, host = host, port = port)
                } else {
                    null
                }
            } else {
                null
            }
        } catch (_: Exception) {
            null
        } finally {
            conn?.disconnect()
        }
    }
}
