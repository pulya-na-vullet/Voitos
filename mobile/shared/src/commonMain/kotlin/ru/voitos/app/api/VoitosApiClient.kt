package ru.voitos.app.api

import io.ktor.client.HttpClient
import io.ktor.client.call.body
import io.ktor.client.plugins.HttpTimeout
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
import ru.voitos.app.model.CollectionDetail
import ru.voitos.app.model.CollectionList
import ru.voitos.app.model.ExecutorMe
import ru.voitos.app.model.ExecutorOfferList
import ru.voitos.app.model.ExecutorOfferRespondResult
import ru.voitos.app.model.ExecutorRegisterResult
import ru.voitos.app.model.ExecutorRoleList
import ru.voitos.app.model.HealthResponse
import ru.voitos.app.model.Me
import ru.voitos.app.model.NotificationList
import ru.voitos.app.model.OnboardingProgress
import ru.voitos.app.model.PhotoUploadResult
import ru.voitos.app.model.ReceiptList
import ru.voitos.app.model.ReceiptUploadResult
import ru.voitos.app.model.SubscriptionInfo
import ru.voitos.app.model.WorkRequestCancelResult
import ru.voitos.app.model.WorkRequestCreated
import ru.voitos.app.model.WorkRequestList
import ru.voitos.app.model.WorkRequestSubmitResult

/**
 * Thin Ktor client for /api/v1. Business rules stay on Django.
 */
class VoitosApiClient(
    private val baseUrl: String = VoitosApi.DEFAULT_BASE_URL,
) {
    var accessToken: String? = null
    private val http: HttpClient = defaultClient()

    suspend fun health(): Boolean {
        val body: HealthResponse = http.get("$baseUrl/health").body()
        return body.ok
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

    suspend fun collection(id: Int): CollectionDetail = authedGet("/collections/$id")

    suspend fun subscription(): SubscriptionInfo = authedGet("/me/subscription")

    suspend fun receipts(): ReceiptList = authedGet("/me/receipts")

    suspend fun uploadReceipt(
        contentBase64: String,
        filename: String = "receipt.jpg",
    ): ReceiptUploadResult =
        http.post("$baseUrl/me/receipts") {
            applyAuth()
            contentType(ContentType.Application.Json)
            setBody(
                buildJsonObject {
                    put("content_base64", contentBase64)
                    put("filename", filename)
                },
            )
        }.body()

    suspend fun executorRoles(): ExecutorRoleList = authedGet("/executor-roles")

    suspend fun createWorkRequest(roleId: Int, description: String): WorkRequestCreated =
        http.post("$baseUrl/work-requests") {
            applyAuth()
            contentType(ContentType.Application.Json)
            setBody(
                buildJsonObject {
                    put("role_id", roleId)
                    put("description", description)
                },
            )
        }.body()

    suspend fun addWorkRequestPhoto(
        workRequestId: Int,
        contentBase64: String,
        filename: String = "photo.jpg",
    ): PhotoUploadResult =
        http.post("$baseUrl/work-requests/$workRequestId/photos") {
            applyAuth()
            contentType(ContentType.Application.Json)
            setBody(
                buildJsonObject {
                    put("content_base64", contentBase64)
                    put("filename", filename)
                },
            )
        }.body()

    suspend fun submitWorkRequest(workRequestId: Int): WorkRequestSubmitResult =
        http.post("$baseUrl/work-requests/$workRequestId/submit") {
            applyAuth()
            contentType(ContentType.Application.Json)
            setBody(buildJsonObject {})
        }.body()

    suspend fun cancelWorkRequest(workRequestId: Int): WorkRequestCancelResult {
        return try {
            http.post("$baseUrl/work-requests/$workRequestId/cancel") {
                applyAuth()
                contentType(ContentType.Application.Json)
                setBody(buildJsonObject {})
            }.body()
        } catch (e: Exception) {
            val msg = (e.message ?: "") + " " + (e.cause?.message ?: "")
            if ("404" in msg || "Not Found" in msg) {
                throw IllegalStateException(
                    "Сервер вернул 404 на отмену заявки. Обновите бэкенд " +
                        "(нужен /api/v1/work-requests/{id}/cancel) и перезапустите app.py.",
                    e,
                )
            }
            throw e
        }
    }

    suspend fun executorMe(): ExecutorMe = authedGet("/me/executor")

    suspend fun registerExecutor(
        roleId: Int,
        equipmentLabel: String,
        plateNumber: String = "",
        phone: String,
        locality: String,
        bankName: String,
        payoutPhone: String = "",
        qualBase64: String = "",
        qualFilename: String = "doc.jpg",
    ): ExecutorRegisterResult =
        http.post("$baseUrl/executor/register") {
            applyAuth()
            contentType(ContentType.Application.Json)
            setBody(
                buildJsonObject {
                    put("role_id", roleId)
                    put("equipment_label", equipmentLabel)
                    put("plate_number", plateNumber)
                    put("phone", phone)
                    put("locality", locality)
                    put("bank_name", bankName)
                    put("payout_phone", payoutPhone)
                    if (qualBase64.isNotBlank()) {
                        put("qual_base64", qualBase64)
                        put("qual_filename", qualFilename)
                    }
                },
            )
        }.body()

    suspend fun executorOffers(): ExecutorOfferList = authedGet("/executor/offers")

    suspend fun respondExecutorOffer(offerId: Int, accept: Boolean): ExecutorOfferRespondResult =
        http.post("$baseUrl/executor/offers/$offerId/respond") {
            applyAuth()
            contentType(ContentType.Application.Json)
            setBody(buildJsonObject { put("accept", accept) })
        }.body()

    suspend fun onboarding(): OnboardingProgress = authedGet("/onboarding")

    suspend fun completeOnboardingStep(code: String): OnboardingProgress =
        http.post("$baseUrl/onboarding/steps/$code/complete") {
            applyAuth()
            contentType(ContentType.Application.Json)
            setBody(buildJsonObject {})
        }.body()

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
            install(HttpTimeout) {
                requestTimeoutMillis = 20_000
                connectTimeoutMillis = 10_000
                socketTimeoutMillis = 20_000
            }
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
