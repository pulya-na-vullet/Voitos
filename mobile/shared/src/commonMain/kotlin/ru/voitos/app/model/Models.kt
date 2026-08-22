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
    @SerialName("needs_pin_setup") val needsPinSetup: Boolean = false,
    @SerialName("has_pin") val hasPin: Boolean = false,
    val phone: String = "",
)

@Serializable
data class AuthConfig(
    @SerialName("max_bot_open_url") val maxBotOpenUrl: String = "",
    @SerialName("app_deep_link") val appDeepLink: String = "",
    @SerialName("phone_otp_fallback") val phoneOtpFallback: Boolean = false,
    @SerialName("registration_hint") val registrationHint: String = "",
)

@Serializable
data class OkResponse(
    val ok: Boolean = false,
    @SerialName("debug_code") val debugCode: String? = null,
    @SerialName("expires_in") val expiresIn: Int = 0,
    @SerialName("has_pin") val hasPin: Boolean = false,
    val message: String = "",
    val phone: String = "",
)

@Serializable
data class ApiErrorBody(
    val error: String = "",
    val detail: String = "",
)

@Serializable
data class WishItem(
    val id: Int = 0,
    val text: String = "",
    val topic: String = "",
    @SerialName("topic_label") val topicLabel: String = "",
    @SerialName("group_id") val groupId: Int = 0,
    @SerialName("group_name") val groupName: String = "",
    @SerialName("created_at") val createdAt: String = "",
)

@Serializable
data class WishGroupOption(
    val id: Int = 0,
    val name: String = "",
)

@Serializable
data class WishListResponse(
    val items: List<WishItem> = emptyList(),
    val groups: List<WishGroupOption> = emptyList(),
)

@Serializable
data class WishCreated(
    val id: Int = 0,
    val topic: String = "",
    @SerialName("topic_label") val topicLabel: String = "",
    val text: String = "",
    @SerialName("group_id") val groupId: Int = 0,
    @SerialName("group_name") val groupName: String = "",
)

@Serializable
data class Me(
    val id: Int = 0,
    @SerialName("real_name") val realName: String = "",
    val phone: String = "",
    val address: String = "",
    val locality: String = "",
    @SerialName("profile_status") val profileStatus: String = "",
    @SerialName("onboarding_completed") val onboardingCompleted: Boolean = false,
    @SerialName("avatar_url") val avatarUrl: String = "",
)

@Serializable
data class AvatarUploadResult(
    val ok: Boolean = true,
    val id: Int = 0,
    @SerialName("real_name") val realName: String = "",
    val phone: String = "",
    val address: String = "",
    val locality: String = "",
    @SerialName("profile_status") val profileStatus: String = "",
    @SerialName("onboarding_completed") val onboardingCompleted: Boolean = false,
    @SerialName("avatar_url") val avatarUrl: String = "",
    val detail: String = "",
    val error: String = "",
) {
    fun toMe(): Me = Me(
        id = id,
        realName = realName,
        phone = phone,
        address = address,
        locality = locality,
        profileStatus = profileStatus,
        onboardingCompleted = onboardingCompleted,
        avatarUrl = avatarUrl,
    )
}

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
    @SerialName("status_label") val statusLabel: String = "",
    @SerialName("role_name") val roleName: String = "",
    val description: String = "",
    @SerialName("created_at") val createdAt: String = "",
    @SerialName("assigned_executor_name") val assignedExecutorName: String? = null,
    @SerialName("proposed_slots") val proposedSlots: List<String> = emptyList(),
    @SerialName("agreed_slot") val agreedSlot: String = "",
    @SerialName("can_confirm_slot") val canConfirmSlot: Boolean = false,
    @SerialName("needs_confirm_amount") val needsConfirmAmount: Boolean = false,
)

@Serializable
data class WorkRequestList(
    val items: List<WorkRequestBrief> = emptyList(),
)

@Serializable
data class WorkRequestDetail(
    val id: Int,
    val status: String = "",
    @SerialName("status_label") val statusLabel: String = "",
    @SerialName("role_name") val roleName: String = "",
    val description: String = "",
    @SerialName("created_at") val createdAt: String = "",
    @SerialName("updated_at") val updatedAt: String = "",
    @SerialName("assigned_executor_name") val assignedExecutorName: String? = null,
    @SerialName("assigned_name") val assignedName: String? = null,
    @SerialName("assigned_phone") val assignedPhone: String? = null,
    @SerialName("proposed_slots") val proposedSlots: List<String> = emptyList(),
    @SerialName("agreed_slot") val agreedSlot: String = "",
    @SerialName("can_confirm_slot") val canConfirmSlot: Boolean = false,
    @SerialName("needs_confirm_amount") val needsConfirmAmount: Boolean = false,
    @SerialName("needs_photos") val needsPhotos: Boolean = false,
    @SerialName("photo_count") val photoCount: Int = 0,
    @SerialName("photo_urls") val photoUrls: List<String> = emptyList(),
    @SerialName("client_locality") val clientLocality: String = "",
    @SerialName("client_address") val clientAddress: String = "",
    @SerialName("master_address") val masterAddress: String = "",
    @SerialName("reported_amount") val reportedAmount: Double? = null,
    @SerialName("confirmed_amount") val confirmedAmount: Double? = null,
    @SerialName("pay_method") val payMethod: String = "",
)

@Serializable
data class CollectionBrief(
    val id: Int,
    val title: String = "",
    val category: String = "",
    @SerialName("category_label") val categoryLabel: String = "",
    @SerialName("amount_due") val amountDue: Double = 0.0,
    val status: String = "",
    @SerialName("campaign_status") val campaignStatus: String = "",
    @SerialName("event_at") val eventAt: String? = null,
    @SerialName("cover_photo_url") val coverPhotoUrl: String = "",
    @SerialName("photo_urls") val photoUrls: List<String> = emptyList(),
    val description: String = "",
    @SerialName("payment_name") val paymentName: String = "",
    @SerialName("payment_phone") val paymentPhone: String = "",
    @SerialName("payment_bank") val paymentBank: String = "",
    @SerialName("payment_status") val paymentStatus: String = "",
    @SerialName("pending_receipts") val pendingReceipts: Int = 0,
    @SerialName("rejected_receipts") val rejectedReceipts: Int = 0,
    @SerialName("paid_count") val paidCount: Int = 0,
    @SerialName("invite_count") val inviteCount: Int = 0,
    @SerialName("collected_amount") val collectedAmount: Double = 0.0,
    @SerialName("total_amount") val totalAmount: Double = 0.0,
    @SerialName("progress_percent") val progressPercent: Int = 0,
    @SerialName("share_policy") val sharePolicy: String = "fixed",
    @SerialName("share_policy_note") val sharePolicyNote: String = "",
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
    @SerialName("category_label") val categoryLabel: String = "",
    val description: String = "",
    @SerialName("amount_due") val amountDue: Double = 0.0,
    @SerialName("amount_paid") val amountPaid: Double = 0.0,
    val status: String = "",
    @SerialName("campaign_status") val campaignStatus: String = "",
    @SerialName("event_at") val eventAt: String? = null,
    @SerialName("paid_count") val paidCount: Int = 0,
    @SerialName("invite_count") val inviteCount: Int = 0,
    @SerialName("collected_amount") val collectedAmount: Double = 0.0,
    @SerialName("total_amount") val totalAmount: Double = 0.0,
    @SerialName("progress_percent") val progressPercent: Int = 0,
    @SerialName("invite_id") val inviteId: Int = 0,
    @SerialName("can_pay") val canPay: Boolean = false,
    @SerialName("pending_receipts") val pendingReceipts: Int = 0,
    @SerialName("rejected_receipts") val rejectedReceipts: Int = 0,
    @SerialName("cover_photo_url") val coverPhotoUrl: String = "",
    @SerialName("photo_urls") val photoUrls: List<String> = emptyList(),
    @SerialName("payment_name") val paymentName: String = "",
    @SerialName("payment_phone") val paymentPhone: String = "",
    @SerialName("payment_bank") val paymentBank: String = "",
    @SerialName("payment_status") val paymentStatus: String = "",
    @SerialName("share_policy") val sharePolicy: String = "fixed",
    @SerialName("share_policy_note") val sharePolicyNote: String = "",
)

@Serializable
data class CollectionReceiptResult(
    val ok: Boolean = true,
    val id: Int = 0,
    val status: String = "",
    val message: String = "",
    @SerialName("created_at") val createdAt: String = "",
)

@Serializable
data class FamilyMember(
    val id: Int = 0,
    val name: String = "",
    val phone: String = "",
    /** payer | member */
    val relation: String = "member",
)

@Serializable
data class FamilySubscriptionInfo(
    @SerialName("is_payer") val isPayer: Boolean = false,
    @SerialName("payer_name") val payerName: String = "",
    val members: List<FamilyMember> = emptyList(),
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
    val family: FamilySubscriptionInfo = FamilySubscriptionInfo(),
)

@Serializable
data class ExecutorRole(
    val id: Int,
    val code: String = "",
    val name: String = "",
    @SerialName("requires_work_photos") val requiresWorkPhotos: Boolean = true,
    @SerialName("is_equipment") val isEquipment: Boolean = false,
    @SerialName("requires_qualification_docs") val requiresQualificationDocs: Boolean = false,
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
data class WorkRequestCancelResult(
    val ok: Boolean = true,
    val id: Int = 0,
    val status: String = "",
)

@Serializable
data class WorkRequestConfirmSlotResult(
    val ok: Boolean = true,
    val message: String = "",
    val status: String = "",
    @SerialName("status_label") val statusLabel: String = "",
    @SerialName("agreed_slot") val agreedSlot: String = "",
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
    /** Имя файла в APK assets/onboarding/ (01_snow.jpg …). */
    val asset: String = "",
)

@Serializable
data class OnboardingProgress(
    @SerialName("done_count") val doneCount: Int = 0,
    val total: Int = 5,
    val completed: Boolean = false,
    @SerialName("reward_granted") val rewardGranted: Boolean = false,
    @SerialName("reward_just_granted") val rewardJustGranted: Boolean = false,
    @SerialName("completed_at") val completedAt: String? = null,
    val steps: List<OnboardingStep> = emptyList(),
)

@Serializable
data class ExecutorProfileBrief(
    val id: Int,
    @SerialName("role_id") val roleId: Int = 0,
    @SerialName("role_name") val roleName: String = "",
    @SerialName("role_code") val roleCode: String = "",
    val status: String = "",
    @SerialName("status_label") val statusLabel: String = "",
    val locality: String = "",
    val phone: String = "",
)

@Serializable
data class ExecutorMe(
    @SerialName("is_executor") val isExecutor: Boolean = false,
    val profiles: List<ExecutorProfileBrief> = emptyList(),
    @SerialName("open_offers_count") val openOffersCount: Int = 0,
)

@Serializable
data class ExecutorRegisterResult(
    val ok: Boolean = true,
    val id: Int = 0,
    val status: String = "",
    val message: String = "",
)

@Serializable
data class ExecutorOfferBrief(
    @SerialName("offer_id") val offerId: Int,
    @SerialName("work_request_id") val workRequestId: Int = 0,
    @SerialName("role_id") val roleId: Int = 0,
    @SerialName("role_name") val roleName: String = "",
    val description: String = "",
    val locality: String = "",
    val address: String = "",
    val status: String = "",
    @SerialName("respond_deadline") val respondDeadline: String? = null,
)

@Serializable
data class ExecutorOfferList(
    val items: List<ExecutorOfferBrief> = emptyList(),
)

@Serializable
data class ExecutorOfferRespondResult(
    val ok: Boolean = true,
    val message: String = "",
    val status: String = "",
    @SerialName("offer_id") val offerId: Int = 0,
)

@Serializable
data class FeedbackTicket(
    val id: Int = 0,
    val kind: String = "",
    @SerialName("kind_label") val kindLabel: String = "",
    val status: String = "",
    @SerialName("status_label") val statusLabel: String = "",
    val subject: String = "",
    val body: String = "",
    val score: Int? = null,
    @SerialName("manager_name") val managerName: String = "",
    @SerialName("group_name") val groupName: String = "",
    @SerialName("group_id") val groupId: Int? = null,
    @SerialName("manager_id") val managerId: Int? = null,
    @SerialName("admin_reply") val adminReply: String = "",
    @SerialName("admin_replied_at") val adminRepliedAt: String? = null,
    @SerialName("answered_by_ai") val answeredByAi: Boolean = false,
    @SerialName("created_at") val createdAt: String = "",
    @SerialName("updated_at") val updatedAt: String = "",
)

@Serializable
data class ManagerFeedbackContext(
    val available: Boolean = false,
    @SerialName("manager_name") val managerName: String = "",
    @SerialName("group_name") val groupName: String = "",
    @SerialName("group_id") val groupId: Int? = null,
    @SerialName("manager_id") val managerId: Int? = null,
)

@Serializable
data class FeedbackListResponse(
    val items: List<FeedbackTicket> = emptyList(),
    val manager: ManagerFeedbackContext = ManagerFeedbackContext(),
    val managers: List<ManagerFeedbackContext> = emptyList(),
    val notice: String = "",
)

@Serializable
data class FeedbackCreateResult(
    val ok: Boolean = true,
    val ticket: FeedbackTicket? = null,
    val detail: String = "",
    val error: String = "",
)

@Serializable
data class ServiceGroupBrief(
    val id: Int = 0,
    val name: String = "",
    val description: String = "",
    @SerialName("member_count") val memberCount: Int = 0,
    @SerialName("unread_count") val unreadCount: Int = 0,
    @SerialName("free_balance") val freeBalance: Double = 0.0,
)

@Serializable
data class ServiceGroupList(
    val items: List<ServiceGroupBrief> = emptyList(),
    @SerialName("unread_total") val unreadTotal: Int = 0,
)

@Serializable
data class GroupChatMessage(
    val id: Int = 0,
    @SerialName("group_id") val groupId: Int = 0,
    val text: String = "",
    @SerialName("created_at") val createdAt: String = "",
    @SerialName("author_id") val authorId: Int? = null,
    @SerialName("author_name") val authorName: String = "",
    @SerialName("author_avatar_url") val authorAvatarUrl: String = "",
    @SerialName("is_mine") val isMine: Boolean = false,
)

@Serializable
data class GroupChatMessagesResponse(
    val group: ServiceGroupBrief = ServiceGroupBrief(),
    val items: List<GroupChatMessage> = emptyList(),
)

@Serializable
data class GroupChatSendResult(
    val ok: Boolean = true,
    val message: GroupChatMessage? = null,
    val detail: String = "",
    val error: String = "",
)

