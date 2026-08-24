package ru.voitos.app.model

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

@Serializable
data class HealthResponse(
    val ok: Boolean = false,
    val service: String = "",
    @SerialName("min_app_version_code") val minAppVersionCode: Int = 0,
    @SerialName("latest_app_version_code") val latestAppVersionCode: Int = 0,
    @SerialName("latest_app_version_name") val latestAppVersionName: String = "",
    @SerialName("apk_url") val apkUrl: String = "",
    @SerialName("update_message") val updateMessage: String = "",
    @SerialName("update_required") val updateRequired: Boolean = false,
    @SerialName("client_version_code") val clientVersionCode: Int = 0,
)

@Serializable
data class AuthSession(
    @SerialName("access_token") val accessToken: String,
    @SerialName("bot_user_id") val botUserId: Int,
    @SerialName("display_name") val displayName: String = "",
    @SerialName("needs_pin_setup") val needsPinSetup: Boolean = false,
    @SerialName("has_pin") val hasPin: Boolean = false,
    val phone: String = "",
    /** Бэкенд решает, показывать ли комикс-онбординг. */
    @SerialName("needs_onboarding") val needsOnboarding: Boolean = false,
    @SerialName("is_active") val isActive: Boolean = true,
    /** Подписка закончилась — нужен чек, вход при этом разрешён. */
    @SerialName("needs_payment") val needsPayment: Boolean = false,
    val access: AccessInfo? = null,
    @SerialName("update_required") val updateRequired: Boolean = false,
    @SerialName("min_app_version_code") val minAppVersionCode: Int = 0,
    @SerialName("apk_url") val apkUrl: String = "",
    @SerialName("update_message") val updateMessage: String = "",
    @SerialName("latest_app_version_name") val latestAppVersionName: String = "",
)

@Serializable
data class AuthConfig(
    @SerialName("max_bot_open_url") val maxBotOpenUrl: String = "",
    @SerialName("app_deep_link") val appDeepLink: String = "",
    @SerialName("phone_otp_fallback") val phoneOtpFallback: Boolean = false,
    @SerialName("registration_hint") val registrationHint: String = "",
    @SerialName("min_app_version_code") val minAppVersionCode: Int = 0,
    @SerialName("latest_app_version_code") val latestAppVersionCode: Int = 0,
    @SerialName("latest_app_version_name") val latestAppVersionName: String = "",
    @SerialName("apk_url") val apkUrl: String = "",
    @SerialName("update_message") val updateMessage: String = "",
    @SerialName("update_required") val updateRequired: Boolean = false,
)

@Serializable
data class OkResponse(
    val ok: Boolean = false,
    @SerialName("debug_code") val debugCode: String? = null,
    @SerialName("expires_in") val expiresIn: Int = 0,
    @SerialName("has_pin") val hasPin: Boolean = false,
    val message: String = "",
    val phone: String = "",
    @SerialName("max_bot_open_url") val maxBotOpenUrl: String = "",
)

@Serializable
data class ApiErrorBody(
    val error: String = "",
    val detail: String = "",
    @SerialName("update_required") val updateRequired: Boolean = false,
    @SerialName("min_app_version_code") val minAppVersionCode: Int = 0,
    @SerialName("apk_url") val apkUrl: String = "",
    @SerialName("update_message") val updateMessage: String = "",
    @SerialName("latest_app_version_name") val latestAppVersionName: String = "",
)

class ApiException(
    val code: String,
    override val message: String,
    val apkUrl: String = "",
) : Exception(message)

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
    val state: String = "",
    @SerialName("subscription_until") val subscriptionUntil: String? = null,
    @SerialName("grace_until") val graceUntil: String? = null,
    val label: String = "",
    @SerialName("needs_payment") val needsPayment: Boolean = false,
    @SerialName("is_active") val isActive: Boolean = true,
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
    @SerialName("role_code") val roleCode: String = "",
    @SerialName("role_accepts_at_home") val roleAcceptsAtHome: Boolean = false,
    @SerialName("client_notice") val clientNotice: String = "",
    val description: String = "",
    @SerialName("created_at") val createdAt: String = "",
    @SerialName("assigned_executor_name") val assignedExecutorName: String? = null,
    @SerialName("assigned_phone") val assignedPhone: String? = null,
    @SerialName("proposed_slots") val proposedSlots: List<String> = emptyList(),
    @SerialName("agreed_slot") val agreedSlot: String = "",
    @SerialName("can_confirm_slot") val canConfirmSlot: Boolean = false,
    @SerialName("needs_confirm_amount") val needsConfirmAmount: Boolean = false,
    @SerialName("needs_rating") val needsRating: Boolean = false,
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
    /** client | executor — кто открыл карточку. */
    val viewer: String = "client",
    @SerialName("client_notice") val clientNotice: String = "",
    @SerialName("role_code") val roleCode: String = "",
    @SerialName("assigned_executor_name") val assignedExecutorName: String? = null,
    @SerialName("assigned_name") val assignedName: String? = null,
    @SerialName("assigned_phone") val assignedPhone: String? = null,
    @SerialName("assigned_max_username") val assignedMaxUsername: String? = null,
    @SerialName("assigned_max_link") val assignedMaxLink: String? = null,
    @SerialName("assigned_contacts") val assignedContacts: List<String> = emptyList(),
    @SerialName("client_name") val clientName: String? = null,
    @SerialName("client_phone") val clientPhone: String? = null,
    @SerialName("role_accepts_at_home") val roleAcceptsAtHome: Boolean = false,
    @SerialName("proposed_slots") val proposedSlots: List<String> = emptyList(),
    @SerialName("agreed_slot") val agreedSlot: String = "",
    @SerialName("can_confirm_slot") val canConfirmSlot: Boolean = false,
    @SerialName("needs_confirm_amount") val needsConfirmAmount: Boolean = false,
    @SerialName("needs_rating") val needsRating: Boolean = false,
    @SerialName("needs_photos") val needsPhotos: Boolean = false,
    @SerialName("photo_count") val photoCount: Int = 0,
    @SerialName("photo_urls") val photoUrls: List<String> = emptyList(),
    @SerialName("client_locality") val clientLocality: String = "",
    @SerialName("client_address") val clientAddress: String = "",
    @SerialName("master_address") val masterAddress: String = "",
    @SerialName("reported_amount") val reportedAmount: Double? = null,
    @SerialName("confirmed_amount") val confirmedAmount: Double? = null,
    @SerialName("pay_method") val payMethod: String = "",
    @SerialName("can_mark_done") val canMarkDone: Boolean = false,
    @SerialName("can_cancel") val canCancel: Boolean = false,
    @SerialName("client_marked_done") val clientMarkedDone: Boolean = false,
    @SerialName("executor_marked_done") val executorMarkedDone: Boolean = false,
    @SerialName("needs_executor_payment_report") val needsExecutorPaymentReport: Boolean = false,
    @SerialName("client_cancel_comment") val clientCancelComment: String = "",
    @SerialName("executor_cancel_comment") val executorCancelComment: String = "",
    @SerialName("amount_mismatch_message") val amountMismatchMessage: String = "",
    @SerialName("amount_mismatch_due") val amountMismatchDue: Double? = null,
    @SerialName("commission_amount") val commissionAmount: Double? = null,
    @SerialName("commission_status") val commissionStatus: String = "",
    @SerialName("commission_status_label") val commissionStatusLabel: String = "",
    @SerialName("commission_admin_note") val commissionAdminNote: String = "",
    @SerialName("commission_payee_text") val commissionPayeeText: String = "",
    @SerialName("client_pay_method") val clientPayMethod: String = "",
    @SerialName("needs_commission_submit") val needsCommissionSubmit: Boolean = false,
    @SerialName("commission_awaiting_approval") val commissionAwaitingApproval: Boolean = false,
)

@Serializable
data class ConfirmAmountResult(
    val ok: Boolean = true,
    val message: String = "",
    val status: String = "",
    @SerialName("needs_rating") val needsRating: Boolean = false,
)

@Serializable
data class RateWorkRequestResult(
    val ok: Boolean = true,
    val score: Int = 0,
    val comment: String = "",
    val message: String = "",
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
    @SerialName("created_at") val createdAt: String? = null,
)

@Serializable
data class CollectionList(
    val items: List<CollectionBrief> = emptyList(),
    /** Макс. одновременных активных сборов на группу. */
    @SerialName("active_limit") val activeLimit: Int = 4,
    /** Группа достигла лимита — показать баннер в списке. */
    @SerialName("at_active_limit") val atActiveLimit: Boolean = false,
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
    @SerialName("needs_payment") val needsPayment: Boolean = false,
    @SerialName("is_active") val isActive: Boolean = true,
    val family: FamilySubscriptionInfo = FamilySubscriptionInfo(),
)

@Serializable
data class ExecutorRole(
    val id: Int,
    val code: String = "",
    val name: String = "",
    @SerialName("requires_work_photos") val requiresWorkPhotos: Boolean = true,
    @SerialName("accepts_at_home") val acceptsAtHome: Boolean = false,
    @SerialName("is_equipment") val isEquipment: Boolean = false,
    @SerialName("requires_qualification_docs") val requiresQualificationDocs: Boolean = false,
    @SerialName("client_books_master") val clientBooksMaster: Boolean = true,
    @SerialName("client_notice") val clientNotice: String = "",
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
    @SerialName("agreed_slot") val agreedSlot: String = "",
    @SerialName("client_prebooked") val clientPrebooked: Boolean = false,
    @SerialName("client_notice") val clientNotice: String = "",
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
    @SerialName("client_cancel_comment") val clientCancelComment: String = "",
    @SerialName("executor_cancel_comment") val executorCancelComment: String = "",
)

@Serializable
data class WorkRequestMarkDoneResult(
    val ok: Boolean = true,
    val id: Int = 0,
    val status: String = "",
    val message: String = "",
    @SerialName("both_done") val bothDone: Boolean = false,
    @SerialName("client_marked_done") val clientMarkedDone: Boolean = false,
    @SerialName("executor_marked_done") val executorMarkedDone: Boolean = false,
    @SerialName("needs_executor_payment_report") val needsExecutorPaymentReport: Boolean = false,
)

@Serializable
data class WorkRequestReportPaymentResult(
    val ok: Boolean = true,
    val message: String = "",
    val status: String = "",
    @SerialName("reported_amount") val reportedAmount: Double? = null,
    @SerialName("commission_amount") val commissionAmount: Double? = null,
)

@Serializable
data class WorkRequestCommissionSubmitResult(
    val ok: Boolean = true,
    val message: String = "",
    val status: String = "",
    @SerialName("commission_status") val commissionStatus: String = "",
    @SerialName("commission_amount") val commissionAmount: Double? = null,
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
    /** Явный флаг с бэка: клиент обязан следовать ему. */
    @SerialName("needs_onboarding") val needsOnboarding: Boolean = false,
    @SerialName("reward_granted") val rewardGranted: Boolean = false,
    @SerialName("reward_just_granted") val rewardJustGranted: Boolean = false,
    @SerialName("completed_at") val completedAt: String? = null,
    val steps: List<OnboardingStep> = emptyList(),
) {
    fun requiresOnboarding(): Boolean =
        if (needsOnboarding) true else !(completed || rewardGranted)
}

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
    @SerialName("is_voitos_team") val isVoitosTeam: Boolean = false,
)

@Serializable
data class WorkNotice(
    @SerialName("work_request_id") val workRequestId: Int = 0,
    val message: String = "",
    @SerialName("amount_due") val amountDue: Double = 0.0,
)

@Serializable
data class ExecutorMe(
    @SerialName("is_executor") val isExecutor: Boolean = false,
    val profiles: List<ExecutorProfileBrief> = emptyList(),
    @SerialName("open_offers_count") val openOffersCount: Int = 0,
    @SerialName("work_notices") val workNotices: List<WorkNotice> = emptyList(),
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
    @SerialName("client_phone") val clientPhone: String = "",
    @SerialName("client_name") val clientName: String = "",
    @SerialName("accepts_at_home") val acceptsAtHome: Boolean = false,
    val status: String = "",
    @SerialName("respond_deadline") val respondDeadline: String? = null,
    @SerialName("yandex_maps_url") val yandexMapsUrl: String = "",
    @SerialName("dgis_maps_url") val dgisMapsUrl: String = "",
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
data class ExecutorJobBrief(
    val id: Int = 0,
    val status: String = "",
    @SerialName("status_label") val statusLabel: String = "",
    @SerialName("role_name") val roleName: String = "",
    val description: String = "",
    @SerialName("client_name") val clientName: String = "",
    @SerialName("client_phone") val clientPhone: String = "",
    @SerialName("client_locality") val clientLocality: String = "",
    @SerialName("client_address") val clientAddress: String = "",
    @SerialName("accepts_at_home") val acceptsAtHome: Boolean = false,
    @SerialName("master_address") val masterAddress: String = "",
    @SerialName("proposed_slots") val proposedSlots: List<String> = emptyList(),
    @SerialName("agreed_slot") val agreedSlot: String = "",
    @SerialName("client_prebooked") val clientPrebooked: Boolean = false,
    @SerialName("needs_confirm_booking") val needsConfirmBooking: Boolean = false,
    @SerialName("needs_propose_slots") val needsProposeSlots: Boolean = false,
    @SerialName("commission_blocked") val commissionBlocked: Boolean = false,
    @SerialName("commission_blocked_message") val commissionBlockedMessage: String = "",
)

@Serializable
data class ExecutorJobList(
    val items: List<ExecutorJobBrief> = emptyList(),
)

@Serializable
data class ExecutorCampaignPeer(
    @SerialName("assignment_id") val assignmentId: Int = 0,
    val name: String = "",
    @SerialName("role_label") val roleLabel: String = "",
    @SerialName("equipment_label") val equipmentLabel: String = "",
    val phone: String = "",
    @SerialName("max_username") val maxUsername: String = "",
    @SerialName("max_profile_url") val maxProfileUrl: String = "",
    @SerialName("plate_number") val plateNumber: String = "",
)

@Serializable
data class ExecutorCampaignJobBrief(
    @SerialName("assignment_id") val assignmentId: Int = 0,
    @SerialName("campaign_id") val campaignId: Int = 0,
    val title: String = "",
    val category: String = "",
    @SerialName("category_label") val categoryLabel: String = "",
    val description: String = "",
    val status: String = "",
    @SerialName("status_label") val statusLabel: String = "",
    @SerialName("equipment_type") val equipmentType: String = "",
    @SerialName("equipment_label") val equipmentLabel: String = "",
    @SerialName("scheduled_at") val scheduledAt: String? = null,
    @SerialName("proposed_at") val proposedAt: String? = null,
    @SerialName("group_name") val groupName: String = "",
    val locality: String = "",
    val address: String = "",
    @SerialName("yandex_maps_url") val yandexMapsUrl: String = "",
    @SerialName("dgis_maps_url") val dgisMapsUrl: String = "",
    @SerialName("show_peers") val showPeers: Boolean = false,
    val peers: List<ExecutorCampaignPeer> = emptyList(),
    @SerialName("needs_response") val needsResponse: Boolean = false,
)

@Serializable
data class ExecutorCampaignJobList(
    val items: List<ExecutorCampaignJobBrief> = emptyList(),
)

@Serializable
data class ExecutorCampaignJobRespondResult(
    val ok: Boolean = true,
    val message: String = "",
    val status: String = "",
    @SerialName("assignment_id") val assignmentId: Int = 0,
)

@Serializable
data class ProposeSlotsResult(
    val ok: Boolean = true,
    val message: String = "",
    val status: String = "",
    @SerialName("proposed_slots") val proposedSlots: List<String> = emptyList(),
    val detail: String = "",
    val error: String = "",
)

@Serializable
data class ExecutorScheduleEvent(
    @SerialName("work_request_id") val workRequestId: Int = 0,
    @SerialName("role_name") val roleName: String = "",
    @SerialName("client_name") val clientName: String = "",
    val status: String = "",
    @SerialName("status_label") val statusLabel: String = "",
    val label: String = "",
    @SerialName("start_at") val startAt: String = "",
    @SerialName("end_at") val endAt: String = "",
    val day: String = "",
)

@Serializable
data class ExecutorScheduleResponse(
    @SerialName("week_start") val weekStart: String = "",
    @SerialName("week_end") val weekEnd: String = "",
    val items: List<ExecutorScheduleEvent> = emptyList(),
)

@Serializable
data class RoleMasterBrief(
    @SerialName("contractor_id") val contractorId: Int = 0,
    val name: String = "",
    val locality: String = "",
    val phone: String = "",
    @SerialName("can_accept") val canAccept: Boolean = false,
    @SerialName("blocked_reason") val blockedReason: String = "",
    @SerialName("blocked_message") val blockedMessage: String = "",
    @SerialName("free_slots_preview") val freeSlotsPreview: Int = 0,
)

@Serializable
data class RoleMastersResponse(
    @SerialName("role_id") val roleId: Int = 0,
    @SerialName("role_name") val roleName: String = "",
    val items: List<RoleMasterBrief> = emptyList(),
    val detail: String = "",
    val error: String = "",
)

@Serializable
data class FreeSlotBrief(
    val day: String = "",
    @SerialName("start_at") val startAt: String = "",
    @SerialName("end_at") val endAt: String = "",
    val label: String = "",
)

@Serializable
data class FreeSlotsResponse(
    @SerialName("contractor_id") val contractorId: Int = 0,
    val name: String = "",
    @SerialName("can_accept") val canAccept: Boolean = true,
    @SerialName("blocked_reason") val blockedReason: String = "",
    @SerialName("blocked_message") val blockedMessage: String = "",
    val items: List<FreeSlotBrief> = emptyList(),
)

@Serializable
data class ConfirmBookingResult(
    val ok: Boolean = true,
    val message: String = "",
    val status: String = "",
    @SerialName("agreed_slot") val agreedSlot: String = "",
    val detail: String = "",
    val error: String = "",
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
data class GroupChatActiveCollection(
    val id: Int = 0,
    val title: String = "",
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
    /** Оплата сборов автором (семья). Дублирует author_paid[author_id]. */
    @SerialName("payment_dots") val paymentDots: List<Boolean> = emptyList(),
)

@Serializable
data class GroupChatMessagesResponse(
    val group: ServiceGroupBrief = ServiceGroupBrief(),
    val items: List<GroupChatMessage> = emptyList(),
    @SerialName("active_collections") val activeCollections: List<GroupChatActiveCollection> = emptyList(),
    /** author_id → оплатил ли каждый активный сбор (порядок = active_collections). */
    @SerialName("author_paid") val authorPaid: Map<String, List<Boolean>> = emptyMap(),
)

@Serializable
data class GroupChatSendResult(
    val ok: Boolean = true,
    val message: GroupChatMessage? = null,
    val detail: String = "",
    val error: String = "",
)

