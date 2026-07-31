from django.contrib import admin

from database.models import (
    ActivityLog,
    AdminTask,
    AppSettings,
    BotRuntimeStatus,
    BotUser,
    ChatMessage,
    MemoryItem,
    PaymentReceipt,
    PendingAction,
    Reminder,
    ServiceCampaign,
    ServiceGroup,
    ServiceInvite,
    ServiceReceipt,
    TaskItem,
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
        "locality",
        "profile_status",
        "subscription_until",
        "is_active",
        "last_seen_at",
    )
    search_fields = ("max_user_id", "display_name", "real_name", "username", "locality", "phone")
    list_filter = ("profile_status", "locality")


@admin.register(PaymentReceipt)
class PaymentReceiptAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "amount", "status", "details_match", "months_granted", "created_at")
    list_filter = ("status", "details_match")


@admin.register(ServiceGroup)
class ServiceGroupAdmin(admin.ModelAdmin):
    list_display = ("name", "created_at", "updated_at")
    filter_horizontal = ("members",)


@admin.register(ServiceCampaign)
class ServiceCampaignAdmin(admin.ModelAdmin):
    list_display = ("title", "category", "group", "total_amount", "amount_per_user", "status", "created_at")
    list_filter = ("category", "status")


@admin.register(ServiceInvite)
class ServiceInviteAdmin(admin.ModelAdmin):
    list_display = ("campaign", "user", "amount_due", "amount_paid", "status", "offered_at")
    list_filter = ("status",)


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
