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
import io.ktor.client.statement.HttpResponse
import io.ktor.http.ContentType
import io.ktor.http.contentType
import io.ktor.http.isSuccess
import io.ktor.serialization.kotlinx.json.json
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import ru.voitos.app.VoitosApi
import ru.voitos.app.model.AuthSession
import ru.voitos.app.model.AvatarUploadResult
import ru.voitos.app.model.CollectionDetail
import ru.voitos.app.model.CollectionList
import ru.voitos.app.model.CollectionReceiptResult
import ru.voitos.app.model.ExecutorMe
import ru.voitos.app.model.ExecutorOfferList
import ru.voitos.app.model.ExecutorOfferRespondResult
import ru.voitos.app.model.ExecutorRegisterResult
import ru.voitos.app.model.ExecutorRoleList
import ru.voitos.app.model.FeedbackCreateResult
import ru.voitos.app.model.FeedbackListResponse
import ru.voitos.app.model.GroupChatMessagesResponse
import ru.voitos.app.model.GroupChatSendResult
import ru.voitos.app.model.HealthResponse
import ru.voitos.app.model.Me
import ru.voitos.app.model.NotificationList
import ru.voitos.app.model.OnboardingProgress
import ru.voitos.app.model.PhotoUploadResult
import ru.voitos.app.model.ReceiptList
import ru.voitos.app.model.ReceiptUploadResult
import ru.voitos.app.model.ServiceGroupList
import ru.voitos.app.model.SubscriptionInfo
import ru.voitos.app.model.WorkRequestCancelResult
import ru.voitos.app.model.WorkRequestConfirmSlotResult
import ru.voitos.app.model.WorkRequestCreated
import ru.voitos.app.model.WorkRequestDetail
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

    suspend fun uploadAvatar(
        contentBase64: String,
        filename: String = "avatar.jpg",
    ): Me {
        val response: HttpResponse = http.post("$baseUrl/me/avatar") {
            applyAuth()
            contentType(ContentType.Application.Json)
            setBody(
                buildJsonObject {
                    put("content_base64", contentBase64)
                    put("filename", filename)
                },
            )
        }
        val parsed: AvatarUploadResult = runCatching { response.body<AvatarUploadResult>() }
            .getOrElse {
                throw IllegalStateException(
                    if (response.status.isSuccess()) {
                        "Сервер вернул неожиданный ответ при загрузке аватара"
                    } else {
                        "Не удалось загрузить аватар (HTTP ${response.status.value}). Обновите бэкенд."
                    },
                )
            }
        if (!response.status.isSuccess() || !parsed.ok) {
            val msg = parsed.detail.ifBlank { parsed.error }.ifBlank {
                "Не удалось загрузить аватар (HTTP ${response.status.value})"
            }
            throw IllegalStateException(msg)
        }
        if (parsed.id <= 0 && parsed.avatarUrl.isBlank()) {
            throw IllegalStateException("Сервер не вернул профиль после загрузки аватара")
        }
        return parsed.toMe()
    }

    suspend fun groups(): ServiceGroupList = authedGet("/groups")

    suspend fun groupMessages(
        groupId: Int,
        afterId: Int? = null,
        beforeId: Int? = null,
    ): GroupChatMessagesResponse {
        val params = buildList {
            if (afterId != null) add("after_id=$afterId")
            if (beforeId != null) add("before_id=$beforeId")
        }
        val q = if (params.isEmpty()) "" else "?${params.joinToString("&")}"
        return authedGet("/groups/$groupId/messages$q")
    }

    suspend fun sendGroupMessage(groupId: Int, text: String): GroupChatSendResult {
        val response: HttpResponse = http.post("$baseUrl/groups/$groupId/messages") {
            applyAuth()
            contentType(ContentType.Application.Json)
            setBody(buildJsonObject { put("text", text) })
        }
        if (!response.status.isSuccess()) {
            val err: GroupChatSendResult = runCatching { response.body<GroupChatSendResult>() }
                .getOrElse { GroupChatSendResult(ok = false, error = "HTTP ${response.status.value}") }
            val msg = err.detail.ifBlank { err.error }.ifBlank { "Не удалось отправить" }
            throw IllegalStateException(msg)
        }
        return response.body()
    }

    suspend fun markGroupRead(groupId: Int, lastReadMessageId: Int? = null) {
        http.post("$baseUrl/groups/$groupId/read") {
            applyAuth()
            contentType(ContentType.Application.Json)
            setBody(
                buildJsonObject {
                    if (lastReadMessageId != null) put("last_read_message_id", lastReadMessageId)
                },
            )
        }
    }

    suspend fun notifications(unreadOnly: Boolean = false): NotificationList {
        val q = if (unreadOnly) "?unread=1" else ""
        return authedGet("/notifications$q")
    }

    suspend fun workRequests(): WorkRequestList = authedGet("/work-requests")

    suspend fun workRequest(id: Int): WorkRequestDetail = authedGet("/work-requests/$id")

    suspend fun collections(): CollectionList = authedGet("/collections")

    suspend fun collection(id: Int): CollectionDetail = authedGet("/collections/$id")

    suspend fun uploadCollectionReceipt(
        collectionId: Int,
        contentBase64: String,
        filename: String = "receipt.jpg",
    ): CollectionReceiptResult =
        http.post("$baseUrl/collections/$collectionId/receipt") {
            applyAuth()
            contentType(ContentType.Application.Json)
            setBody(
                buildJsonObject {
                    put("content_base64", contentBase64)
                    put("filename", filename)
                },
            )
        }.body()

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

    suspend fun confirmWorkRequestSlot(
        workRequestId: Int,
        slot: String = "",
    ): WorkRequestConfirmSlotResult =
        http.post("$baseUrl/work-requests/$workRequestId/confirm-slot") {
            applyAuth()
            contentType(ContentType.Application.Json)
            setBody(buildJsonObject { put("slot", slot) })
        }.body()

    /**
     * Старый бэкенд без /me/executor отдаёт HTML 404 → NoTransformationFoundException.
     * Для UI кабинета считаем это «не исполнитель», а не фатальной ошибкой.
     */
    suspend fun executorMe(): ExecutorMe {
        val response: HttpResponse = http.get("$baseUrl/me/executor") { applyAuth() }
        if (response.status.value == 404) {
            return ExecutorMe(isExecutor = false)
        }
        if (!response.status.isSuccess()) {
            val code = response.status.value
            throw IllegalStateException(
                "Сервер вернул $code на /me/executor. Обновите бэкенд и перезапустите app.py.",
            )
        }
        return response.body()
    }

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

    suspend fun executorOffers(): ExecutorOfferList {
        val response: HttpResponse = http.get("$baseUrl/executor/offers") { applyAuth() }
        if (response.status.value == 404) {
            return ExecutorOfferList()
        }
        if (!response.status.isSuccess()) {
            throw IllegalStateException(
                "Сервер вернул ${response.status.value} на /executor/offers. Обновите бэкенд.",
            )
        }
        return response.body()
    }

    suspend fun respondExecutorOffer(offerId: Int, accept: Boolean): ExecutorOfferRespondResult =
        http.post("$baseUrl/executor/offers/$offerId/respond") {
            applyAuth()
            contentType(ContentType.Application.Json)
            setBody(buildJsonObject { put("accept", accept) })
        }.body()

    suspend fun onboarding(): OnboardingProgress = authedGet("/onboarding")

    suspend fun feedback(): FeedbackListResponse = authedGet("/me/feedback")

    suspend fun createFeedback(
        kind: String,
        body: String,
        subject: String = "",
        score: Int? = null,
        groupId: Int? = null,
    ): FeedbackCreateResult {
        val response: HttpResponse = http.post("$baseUrl/me/feedback") {
            applyAuth()
            contentType(ContentType.Application.Json)
            setBody(
                buildJsonObject {
                    put("kind", kind)
                    put("body", body)
                    put("subject", subject)
                    if (score != null) put("score", score)
                    if (groupId != null) put("group_id", groupId)
                },
            )
        }
        if (!response.status.isSuccess()) {
            val err: FeedbackCreateResult = runCatching { response.body<FeedbackCreateResult>() }
                .getOrElse { FeedbackCreateResult(ok = false, error = "HTTP ${response.status.value}") }
            val msg = err.detail.ifBlank { err.error }.ifBlank { "Не удалось отправить обращение" }
            throw IllegalStateException(msg)
        }
        return response.body()
    }

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
