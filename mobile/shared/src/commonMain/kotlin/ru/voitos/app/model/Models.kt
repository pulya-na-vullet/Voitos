package ru.voitos.app.model

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

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
data class OnboardingProgress(
    @SerialName("done_count") val doneCount: Int = 0,
    val total: Int = 5,
    val completed: Boolean = false,
    @SerialName("reward_granted") val rewardGranted: Boolean = false,
)
