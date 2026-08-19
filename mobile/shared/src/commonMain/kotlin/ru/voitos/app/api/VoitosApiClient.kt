package ru.voitos.app.api

import io.ktor.client.HttpClient
import io.ktor.client.call.body
import io.ktor.client.plugins.contentnegotiation.ContentNegotiation
import io.ktor.client.request.bearerAuth
import io.ktor.client.request.get
import io.ktor.client.request.header
import io.ktor.client.request.post
import io.ktor.client.request.setBody
import io.ktor.http.ContentType
import io.ktor.http.contentType
import io.ktor.serialization.kotlinx.json.json
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import ru.voitos.app.VoitosApi
import ru.voitos.app.model.AuthSession
import ru.voitos.app.model.CollectionList
import ru.voitos.app.model.Me
import ru.voitos.app.model.NotificationList
import ru.voitos.app.model.OnboardingProgress
import ru.voitos.app.model.WorkRequestList

/**
 * Thin Ktor client for /api/v1. Business rules stay on Django.
 */
class VoitosApiClient(
    private val baseUrl: String = VoitosApi.DEFAULT_BASE_URL,
    private val http: HttpClient = defaultClient(),
) {
    var accessToken: String? = null

    suspend fun health(): Boolean {
        val body: Map<String, Boolean> = http.get("$baseUrl/health").body()
        return body["ok"] == true
    }

    suspend fun phoneStart(phone: String): Map<String, String?> =
        http.post("$baseUrl/auth/phone/start") {
            contentType(ContentType.Application.Json)
            setBody(buildJsonObject { put("phone", phone) })
        }.body()

    suspend fun phoneVerify(phone: String, code: String): AuthSession {
        val session: AuthSession = http.post("$baseUrl/auth/phone/verify") {
            contentType(ContentType.Application.Json)
            setBody(
                buildJsonObject {
                    put("phone", phone)
                    put("code", code)
                },
            )
        }.body()
        accessToken = session.accessToken
        return session
    }

    suspend fun me(): Me = authedGet("/me")

    suspend fun notifications(unreadOnly: Boolean = false): NotificationList {
        val q = if (unreadOnly) "?unread=1" else ""
        return authedGet("/notifications$q")
    }

    suspend fun workRequests(): WorkRequestList = authedGet("/work-requests")

    suspend fun collections(): CollectionList = authedGet("/collections")

    suspend fun onboarding(): OnboardingProgress = authedGet("/onboarding")

    suspend fun confirmAmount(workRequestId: Int, confirmed: Boolean, amount: Double? = null) {
        http.post("$baseUrl/work-requests/$workRequestId/confirm-amount") {
            applyAuth()
            contentType(ContentType.Application.Json)
            setBody(
                buildJsonObject {
                    put("confirmed", confirmed)
                    if (amount != null) put("amount", amount)
                },
            )
        }
    }

    /** Register FCM/APNs token for the current session. */
    suspend fun registerDevice(pushToken: String, platform: String = "android") {
        http.post("$baseUrl/devices") {
            applyAuth()
            contentType(ContentType.Application.Json)
            setBody(
                buildJsonObject {
                    put("push_token", pushToken)
                    put("platform", platform)
                },
            )
        }
    }

    private suspend inline fun <reified T> authedGet(path: String): T =
        http.get("$baseUrl$path") { applyAuth() }.body()

    private fun io.ktor.client.request.HttpRequestBuilder.applyAuth() {
        val token = accessToken
        if (!token.isNullOrBlank()) {
            bearerAuth(token)
            header("X-Voitos-Token", token)
        }
    }

    companion object {
        fun defaultClient(): HttpClient = HttpClient {
            install(ContentNegotiation) {
                json(
                    Json {
                        ignoreUnknownKeys = true
                        isLenient = true
                    },
                )
            }
        }
    }
}
