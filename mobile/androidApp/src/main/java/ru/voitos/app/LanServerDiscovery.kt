package ru.voitos.app

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
 */
object LanServerDiscovery {
    data class Found(
        val baseUrl: String,
        val host: String,
        val port: Int,
    )

    suspend fun find(
        port: Int = 18765,
        preferHosts: List<String> = emptyList(),
    ): List<Found> = withContext(Dispatchers.IO) {
        val found = linkedMapOf<String, Found>()
        val prefer = preferHosts.mapNotNull { hostFromUrlOrIp(it) }.distinct()

        // Сначала быстро проверяем известные хосты (из бэкапа / recent).
        for (host in prefer) {
            probe(host, port)?.let { found[it.host] = it }
        }
        if (found.isNotEmpty()) return@withContext found.values.toList()

        val prefix = localSubnetPrefix() ?: return@withContext emptyList()
        val gateways = listOf(1, 2, 100, 101, 102, 200, 254) // частые роутер/ПК
        for (i in gateways) {
            probe("$prefix.$i", port)?.let { found[it.host] = it }
        }
        if (found.isNotEmpty()) return@withContext found.values.toList()

        val sem = Semaphore(48)
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
    ): Found? = withContext(Dispatchers.IO) {
        val prefer = preferHosts.mapNotNull { hostFromUrlOrIp(it) }.distinct()
        for (host in prefer) {
            probe(host, port)?.let { return@withContext it }
        }
        val prefix = localSubnetPrefix() ?: return@withContext null
        val firstHit = AtomicReference<Found?>(null)
        val ordered = buildList {
            addAll(listOf(1, 2, 9, 10, 100, 101, 102, 110, 200, 254))
            addAll((1..254).toList())
        }.distinct()
        val sem = Semaphore(64)
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

    fun localSubnetPrefix(): String? {
        val ip = localIpv4() ?: return null
        val parts = ip.split('.')
        if (parts.size != 4) return null
        return "${parts[0]}.${parts[1]}.${parts[2]}"
    }

    fun localIpv4(): String? {
        val faces = NetworkInterface.getNetworkInterfaces() ?: return null
        for (iface in faces) {
            if (!iface.isUp || iface.isLoopback) continue
            val addrs = iface.inetAddresses
            while (addrs.hasMoreElements()) {
                val a = addrs.nextElement()
                if (a is Inet4Address && !a.isLoopbackAddress) {
                    val host = a.hostAddress ?: continue
                    if (host.startsWith("192.168.") || host.startsWith("10.") ||
                        host.matches(Regex("""172\.(1[6-9]|2\d|3[0-1])\..*"""))
                    ) {
                        return host
                    }
                }
            }
        }
        return null
    }

    private fun probe(host: String, port: Int): Found? {
        val base = "http://$host:$port/api/v1"
        var conn: HttpURLConnection? = null
        return try {
            conn = (URL("$base/health").openConnection() as HttpURLConnection).apply {
                connectTimeout = 250
                readTimeout = 250
                requestMethod = "GET"
                instanceFollowRedirects = false
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
