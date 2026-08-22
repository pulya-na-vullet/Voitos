"""Модели для мобильного клиента: токены и inbox уведомлений."""

from __future__ import annotations

import secrets

from django.db import models
from django.utils import timezone


def _token() -> str:
    return secrets.token_urlsafe(32)


class MobileAuthToken(models.Model):
    """Bearer-токен жителя → BotUser (не PanelProfile)."""

    bot_user = models.ForeignKey(
        "database.BotUser",
        on_delete=models.CASCADE,
        related_name="mobile_tokens",
    )
    token = models.CharField(max_length=64, unique=True, default=_token, db_index=True)
    device_name = models.CharField(max_length=128, blank=True, default="")
    push_token = models.CharField(max_length=512, blank=True, default="")
    push_platform = models.CharField(
        max_length=16,
        blank=True,
        default="",
        help_text="android | ios",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(auto_now=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Мобильный токен"
        verbose_name_plural = "Мобильные токены"

    def __str__(self) -> str:
        return f"token:{self.bot_user_id}"

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None

    def revoke(self) -> None:
        self.revoked_at = timezone.now()
        self.save(update_fields=["revoked_at"])


class PhoneOtpChallenge(models.Model):
    """Временный OTP для входа по телефону (stub; SMS позже)."""

    phone = models.CharField(max_length=32, db_index=True)
    code = models.CharField(max_length=8)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    consumed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "OTP телефон"
        verbose_name_plural = "OTP телефоны"

    def is_valid(self, code: str) -> bool:
        if self.consumed_at:
            return False
        if timezone.now() > self.expires_at:
            return False
        return self.code == (code or "").strip()


class PinChallengeKind(models.TextChoices):
    LOGIN = "login", "Вход через Max"
    RESET = "reset", "Сброс PIN"
    CHANGE = "change", "Смена PIN"


class PinChallenge(models.Model):
    """Разовый 4-значный код из MAX (хранится только hash)."""

    bot_user = models.ForeignKey(
        "database.BotUser",
        on_delete=models.CASCADE,
        related_name="pin_challenges",
        null=True,
        blank=True,
    )
    phone = models.CharField(max_length=32, db_index=True)
    kind = models.CharField(
        max_length=16,
        choices=PinChallengeKind.choices,
        default=PinChallengeKind.LOGIN,
        db_index=True,
    )
    code_hash = models.CharField(max_length=128)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    consumed_at = models.DateTimeField(null=True, blank=True)
    failed_attempts = models.PositiveSmallIntegerField(default=0)

    class Meta:
        verbose_name = "Код Max/PIN challenge"
        verbose_name_plural = "Коды Max/PIN challenge"
        ordering = ["-created_at"]

    def is_expired(self) -> bool:
        return timezone.now() > self.expires_at

    def is_open(self) -> bool:
        return self.consumed_at is None and not self.is_expired()


class AppNotification(models.Model):
    """Inbox + источник для FCM (type → deep_link)."""

    bot_user = models.ForeignKey(
        "database.BotUser",
        on_delete=models.CASCADE,
        related_name="app_notifications",
    )
    type = models.CharField(max_length=64, db_index=True)
    title = models.CharField(max_length=255)
    body = models.TextField(blank=True, default="")
    deep_link = models.CharField(max_length=512, blank=True, default="")
    entity_type = models.CharField(max_length=64, blank=True, default="")
    entity_id = models.PositiveIntegerField(null=True, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)
    push_sent_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Уведомление приложения"
        verbose_name_plural = "Уведомления приложения"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.type} → {self.bot_user_id}"

    def mark_read(self) -> None:
        if not self.read_at:
            self.read_at = timezone.now()
            self.save(update_fields=["read_at"])
