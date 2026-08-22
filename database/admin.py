from django.contrib import admin

from database.models import (
    ActivityLog,
    AdminTask,
    AppSettings,
    BotRuntimeStatus,
    BotUser,
    CampaignAssignment,
    ChatMessage,
    ContractorPayout,
    ContractorProfile,
    ExecutorRole,
    MemoryItem,
    NeighborhoodWish,
    PanelActionLog,
    PanelProfile,
    PaymentReceipt,
    PendingAction,
    Reminder,
    ServiceCampaign,
    ServiceGroup,
    ServiceInvite,
    ServiceReceipt,
    TaskItem,
    WorkRequest,
    WorkRequestOffer,
)


@admin.register(AdminTask)
class AdminTaskAdmin(admin.ModelAdmin):
    list_display = ("id", "kind", "status", "title", "user", "priority", "created_at")
    list_filter = ("kind", "status")
    search_fields = ("title", "description")


@admin.register(AppSettings)
class AppSettingsAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "bot_display_name",
        "yandex_model",
        "payment_phone",
        "service_payee_phone",
        "updated_at",
    )


@admin.register(BotRuntimeStatus)
class BotRuntimeStatusAdmin(admin.ModelAdmin):
    list_display = ("state", "bot_name", "last_poll_at", "last_update_at", "updated_at")


@admin.register(BotUser)
class BotUserAdmin(admin.ModelAdmin):
    list_display = (
        "max_user_id",
        "real_name",
        "gender",
        "birth_date",
        "locality",
        "profile_status",
        "subscription_until",
        "is_active",
        "last_seen_at",
    )
    search_fields = ("max_user_id", "display_name", "real_name", "username", "locality", "phone")
    list_filter = ("profile_status", "locality", "gender")


@admin.register(PaymentReceipt)
class PaymentReceiptAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "amount", "status", "details_match", "months_granted", "created_at")
    list_filter = ("status", "details_match")


@admin.register(ServiceGroup)
class ServiceGroupAdmin(admin.ModelAdmin):
    list_display = ("name", "manager", "created_at", "updated_at")
    filter_horizontal = ("members",)
    raw_id_fields = ("manager",)


@admin.register(PanelProfile)
class PanelProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "role", "bot_user", "updated_at")
    list_filter = ("role",)
    search_fields = ("user__username", "bot_user__real_name", "bot_user__phone")
    raw_id_fields = ("user", "bot_user")


@admin.register(NeighborhoodWish)
class NeighborhoodWishAdmin(admin.ModelAdmin):
    list_display = ("id", "topic", "text", "group", "user", "created_at")
    list_filter = ("topic", "group")
    search_fields = ("text", "source_message")


@admin.register(ServiceCampaign)
class ServiceCampaignAdmin(admin.ModelAdmin):
    list_display = ("title", "category", "group", "total_amount", "amount_per_user", "status", "created_at")
    list_filter = ("category", "status")


@admin.register(ServiceInvite)
class ServiceInviteAdmin(admin.ModelAdmin):
    list_display = ("campaign", "user", "amount_due", "amount_paid", "status", "offered_at")
    list_filter = ("status",)


@admin.register(ContractorProfile)
class ContractorProfileAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "role",
        "equipment_type",
        "equipment_label",
        "plate_number",
        "bank_name",
        "payout_phone",
        "status",
        "locality",
        "submitted_at",
    )
    list_filter = ("equipment_type", "status", "role")
    search_fields = (
        "user__real_name",
        "user__display_name",
        "equipment_label",
        "plate_number",
        "bank_name",
        "payout_phone",
    )
    raw_id_fields = ("user", "role")


@admin.register(ExecutorRole)
class ExecutorRoleAdmin(admin.ModelAdmin):
    list_display = (
        "code",
        "name",
        "requires_qualification_docs",
        "is_equipment",
        "requires_work_photos",
        "is_active",
        "sort_order",
    )
    list_filter = (
        "is_active",
        "requires_qualification_docs",
        "is_equipment",
        "requires_work_photos",
    )
    search_fields = ("code", "name")


@admin.register(WorkRequest)
class WorkRequestAdmin(admin.ModelAdmin):
    list_display = ("id", "role", "user", "status", "client_locality", "assigned_contractor", "created_at")
    list_filter = ("status", "role")
    search_fields = ("description", "user__real_name", "client_locality")
    raw_id_fields = ("user", "role", "assigned_contractor")


@admin.register(WorkRequestOffer)
class WorkRequestOfferAdmin(admin.ModelAdmin):
    list_display = ("id", "work_request", "contractor", "status", "offered_at", "respond_deadline")
    list_filter = ("status",)
    raw_id_fields = ("work_request", "contractor")


@admin.register(PanelActionLog)
class PanelActionLogAdmin(admin.ModelAdmin):
    list_display = ("id", "actor", "action", "title", "created_at")
    list_filter = ("action",)
    search_fields = ("title", "detail", "actor__username")
    raw_id_fields = ("actor",)


@admin.register(ContractorPayout)
class ContractorPayoutAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "campaign",
        "contractor",
        "amount",
        "bank_name",
        "payout_phone",
        "created_at",
    )
    list_filter = ("created_at",)
    search_fields = ("contractor__user__real_name", "bank_name", "payout_phone")


@admin.register(CampaignAssignment)
class CampaignAssignmentAdmin(admin.ModelAdmin):
    list_display = (
        "campaign",
        "contractor",
        "equipment_type",
        "status",
        "scheduled_at",
        "proposed_at",
        "offered_at",
    )
    list_filter = ("status", "equipment_type")


@admin.register(ServiceReceipt)
class ServiceReceiptAdmin(admin.ModelAdmin):
    list_display = ("id", "campaign", "user", "amount", "status", "details_match", "created_at")
    list_filter = ("status", "details_match")


@admin.register(MemoryItem)
class MemoryItemAdmin(admin.ModelAdmin):
    list_display = ("text", "category", "user", "created_at")
    list_filter = ("category",)


@admin.register(TaskItem)
class TaskItemAdmin(admin.ModelAdmin):
    list_display = ("text", "status", "user", "created_at", "completed_at")
    list_filter = ("status",)


@admin.register(Reminder)
class ReminderAdmin(admin.ModelAdmin):
    list_display = ("text", "due_at", "repeat", "is_done", "user", "created_at")
    list_filter = ("is_done", "repeat")


@admin.register(ChatMessage)
class ChatMessageAdmin(admin.ModelAdmin):
    list_display = ("role", "text", "user", "is_voice", "intent", "created_at")
    list_filter = ("role", "is_voice")


@admin.register(ActivityLog)
class ActivityLogAdmin(admin.ModelAdmin):
    list_display = ("kind", "title", "user", "created_at")
    list_filter = ("kind",)


@admin.register(PendingAction)
class PendingActionAdmin(admin.ModelAdmin):
    list_display = ("user", "pending_kind", "updated_at")
