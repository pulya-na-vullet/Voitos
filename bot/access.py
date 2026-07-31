from __future__ import annotations

import logging

from django.utils import timezone

from database.models import ActivityKind, ActivityLog, BotUser

logger = logging.getLogger(__name__)


class AccessDenied(Exception):
    pass


def resolve_or_create_user(payload_user: dict, chat_id: str | int | None = None) -> BotUser:
    """Create/update bot user. Multi-user mode: anyone can start the bot."""
    max_user_id = str(payload_user.get("user_id") or payload_user.get("id") or "")
    if not max_user_id:
        raise AccessDenied("Не удалось определить пользователя.")

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
    changed_fields = ["last_seen_at"]
    if chat_id and user.chat_id != str(chat_id):
        user.chat_id = str(chat_id)
        changed_fields.append("chat_id")
    if display and user.display_name != display:
        user.display_name = display
        changed_fields.append("display_name")
    if username and user.username != username:
        user.username = username
        changed_fields.append("username")
    user.last_seen_at = timezone.now()
    user.save(update_fields=list(dict.fromkeys(changed_fields)))

    if created:
        user.ensure_grace_period()
        logger.info("Created bot user %s", max_user_id)
        ActivityLog.objects.create(
            user=user,
            kind=ActivityKind.OTHER,
            title="Новый пользователь",
            detail=display,
        )

    if not user.is_active:
        raise AccessDenied("Пользователь деактивирован администратором.")

    return user
