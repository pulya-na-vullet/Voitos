from django.contrib import admin

from database.models import (
    ActivityLog,
    AppSettings,
    BotRuntimeStatus,
    BotUser,
    ChatMessage,
    MemoryItem,
    PaymentReceipt,
    PendingAction,
    Reminder,
    TaskItem,
)


@admin.register(AppSettings)
class AppSettingsAdmin(admin.ModelAdmin):
    list_display = ("id", "bot_display_name", "yandex_model", "payment_phone", "updated_at")


@admin.register(BotRuntimeStatus)
class BotRuntimeStatusAdmin(admin.ModelAdmin):
    list_display = ("state", "bot_name", "last_poll_at", "last_update_at", "updated_at")


@admin.register(BotUser)
class BotUserAdmin(admin.ModelAdmin):
    list_display = (
        "max_user_id",
        "display_name",
        "subscription_until",
        "grace_until",
        "is_active",
        "last_seen_at",
    )
    search_fields = ("max_user_id", "display_name", "username")


@admin.register(PaymentReceipt)
class PaymentReceiptAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "amount", "status", "details_match", "months_granted", "created_at")
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
    list_display = ("text", "due_at", "is_done", "user", "created_at")
    list_filter = ("is_done",)


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
    list_display = ("user", "updated_at")
