from __future__ import annotations

import logging

from django.utils import timezone

from ai.factory import get_runtime_settings
from database.models import ActivityKind, ActivityLog, BotUser

logger = logging.getLogger(__name__)


class AccessDenied(Exception):
    pass


def resolve_or_create_user(payload_user: dict, chat_id: str | int | None = None) -> BotUser:
    """Enforce single-user access and persist the allowed MAX user."""
    settings_obj = get_runtime_settings()
    max_user_id = str(payload_user.get("user_id") or payload_user.get("id") or "")
    if not max_user_id:
        raise AccessDenied("Не удалось определить пользователя.")

    allowed = (settings_obj.allowed_max_user_id or "").strip()
    if allowed and allowed != max_user_id:
        ActivityLog.objects.create(
            kind=ActivityKind.ACCESS_DENIED,
            title="Отказ в доступе",
            detail=f"user_id={max_user_id}",
            meta={"user": payload_user},
        )
        raise AccessDenied("Бот предназначен только для одного пользователя.")

    display = (
        payload_user.get("name")
        or " ".join(
            filter(
                None,
                [payload_user.get("first_name"), payload_user.get("last_name")],
            )
        ).strip()
        or payload_user.get("username")
        or max_user_id
    )
    username = payload_user.get("username") or ""

    user, created = BotUser.objects.get_or_create(
        max_user_id=max_user_id,
        defaults={
            "chat_id": str(chat_id or ""),
            "display_name": display,
            "username": username,
        },
    )
    changed = False
    if chat_id and user.chat_id != str(chat_id):
        user.chat_id = str(chat_id)
        changed = True
    if display and user.display_name != display:
        user.display_name = display
        changed = True
    if username and user.username != username:
        user.username = username
        changed = True
    user.last_seen_at = timezone.now()
    user.save()

    # Lock to first user automatically
    if not allowed:
        settings_obj.allowed_max_user_id = max_user_id
        settings_obj.save(update_fields=["allowed_max_user_id", "updated_at"])
        logger.info("Bound bot to first user max_user_id=%s", max_user_id)

    if created:
        logger.info("Created bot user %s", max_user_id)
    elif changed:
        logger.debug("Updated bot user %s", max_user_id)

    if not user.is_active:
        raise AccessDenied("Пользователь деактивирован.")

    return user
