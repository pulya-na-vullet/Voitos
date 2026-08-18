from django.contrib import admin

from api.models import AppNotification, MobileAuthToken, PhoneOtpChallenge


@admin.register(MobileAuthToken)
class MobileAuthTokenAdmin(admin.ModelAdmin):
    list_display = ("id", "bot_user", "device_name", "push_platform", "created_at", "revoked_at")
    list_filter = ("push_platform",)
    search_fields = ("bot_user__phone", "bot_user__real_name", "token")
    raw_id_fields = ("bot_user",)


@admin.register(PhoneOtpChallenge)
class PhoneOtpChallengeAdmin(admin.ModelAdmin):
    list_display = ("id", "phone", "code", "created_at", "expires_at", "consumed_at")
    search_fields = ("phone",)


@admin.register(AppNotification)
class AppNotificationAdmin(admin.ModelAdmin):
    list_display = ("id", "bot_user", "type", "title", "read_at", "created_at")
    list_filter = ("type",)
    search_fields = ("title", "body", "bot_user__phone")
    raw_id_fields = ("bot_user",)
