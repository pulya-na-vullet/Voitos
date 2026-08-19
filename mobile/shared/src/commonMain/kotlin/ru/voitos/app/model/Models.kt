package ru.voitos.app.model

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

@Serializable
data class HealthResponse(
    val ok: Boolean = false,
    val service: String = "",
)

@Serializable
data class AuthSession(
    @SerialName("access_token") val accessToken: String,
    @SerialName("bot_user_id") val botUserId: Int,
    @SerialName("display_name") val displayName: String = "",
)

@Serializable
data class Me(
    val id: Int,
    @SerialName("real_name") val realName: String = "",
    val phone: String = "",
    val address: String = "",
    val locality: String = "",
    @SerialName("profile_status") val profileStatus: String = "",
    @SerialName("onboarding_completed") val onboardingCompleted: Boolean = false,
)

@Serializable
data class AccessInfo(
    val state: String,
    @SerialName("subscription_until") val subscriptionUntil: String? = null,
    @SerialName("grace_until") val graceUntil: String? = null,
    val label: String = "",
)

@Serializable
data class AppNotification(
    val id: Int,
    val type: String,
    val title: String,
    val body: String = "",
    @SerialName("deep_link") val deepLink: String = "",
    @SerialName("entity_type") val entityType: String? = null,
    @SerialName("entity_id") val entityId: Int? = null,
    @SerialName("read_at") val readAt: String? = null,
    @SerialName("created_at") val createdAt: String = "",
)

@Serializable
data class NotificationList(
    val items: List<AppNotification> = emptyList(),
)

@Serializable
data class WorkRequestBrief(
    val id: Int,
    val status: String,
    @SerialName("role_name") val roleName: String = "",
    val description: String = "",
    @SerialName("created_at") val createdAt: String = "",
    @SerialName("assigned_executor_name") val assignedExecutorName: String? = null,
)

@Serializable
data class WorkRequestList(
    val items: List<WorkRequestBrief> = emptyList(),
)

@Serializable
data class CollectionBrief(
    val id: Int,
    val title: String = "",
    val category: String = "",
    @SerialName("amount_due") val amountDue: Double = 0.0,
    val status: String = "",
    @SerialName("event_at") val eventAt: String? = null,
)

@Serializable
data class CollectionList(
    val items: List<CollectionBrief> = emptyList(),
)

@Serializable
data class CollectionDetail(
    val id: Int,
    val title: String = "",
    val category: String = "",
    val description: String = "",
    @SerialName("amount_due") val amountDue: Double = 0.0,
    @SerialName("amount_paid") val amountPaid: Double = 0.0,
    val status: String = "",
    @SerialName("event_at") val eventAt: String? = null,
    @SerialName("paid_count") val paidCount: Int = 0,
    @SerialName("invite_count") val inviteCount: Int = 0,
)

@Serializable
data class SubscriptionInfo(
    val state: String = "",
    @SerialName("subscription_until") val subscriptionUntil: String? = null,
    @SerialName("grace_until") val graceUntil: String? = null,
    val label: String = "",
    @SerialName("price_rub") val priceRub: Int = 0,
    @SerialName("payment_phone") val paymentPhone: String = "",
    @SerialName("payment_name") val paymentName: String = "",
    @SerialName("pending_receipts") val pendingReceipts: Int = 0,
)

@Serializable
data class ExecutorRole(
    val id: Int,
    val code: String = "",
    val name: String = "",
    @SerialName("requires_work_photos") val requiresWorkPhotos: Boolean = true,
)

@Serializable
data class ExecutorRoleList(
    val items: List<ExecutorRole> = emptyList(),
)

@Serializable
data class WorkRequestCreated(
    val id: Int,
    val status: String = "",
    @SerialName("role_name") val roleName: String = "",
    val description: String = "",
    @SerialName("created_at") val createdAt: String = "",
    @SerialName("needs_photos") val needsPhotos: Boolean = false,
    @SerialName("photo_count") val photoCount: Int = 0,
)

@Serializable
data class PhotoUploadResult(
    val ok: Boolean = true,
    @SerialName("photo_id") val photoId: Int = 0,
    @SerialName("photo_count") val photoCount: Int = 0,
    val status: String = "",
)

@Serializable
data class WorkRequestSubmitResult(
    val ok: Boolean = true,
    val id: Int = 0,
    val status: String = "",
    @SerialName("photo_count") val photoCount: Int = 0,
    val dispatched: Boolean = false,
)

@Serializable
data class ReceiptBrief(
    val id: Int,
    val status: String = "",
    val amount: Double? = null,
    val period: String = "",
    @SerialName("created_at") val createdAt: String = "",
    @SerialName("reviewed_at") val reviewedAt: String? = null,
    @SerialName("admin_comment") val adminComment: String = "",
)

@Serializable
data class ReceiptList(
    val items: List<ReceiptBrief> = emptyList(),
)

@Serializable
data class ReceiptUploadResult(
    val ok: Boolean = true,
    val id: Int = 0,
    val status: String = "",
    @SerialName("created_at") val createdAt: String = "",
)

@Serializable
data class OnboardingStep(
    val code: String,
    val title: String = "",
    val caption: String = "",
    val done: Boolean = false,
    @SerialName("image_url") val imageUrl: String = "",
)

@Serializable
data class OnboardingProgress(
    @SerialName("done_count") val doneCount: Int = 0,
    val total: Int = 5,
    val completed: Boolean = false,
    @SerialName("reward_granted") val rewardGranted: Boolean = false,
    @SerialName("completed_at") val completedAt: String? = null,
    val steps: List<OnboardingStep> = emptyList(),
)
