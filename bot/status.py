from __future__ import annotations

import logging
from typing import Any

from django.utils import timezone

from database.models import BotRuntimeStatus

logger = logging.getLogger(__name__)


def set_bot_status(
    *,
    state: str,
    detail: str = "",
    bot_name: str = "",
    bot_username: str = "",
    last_marker: int | None = None,
    touch_poll: bool = False,
    touch_update: bool = False,
    clear_error: bool = False,
) -> BotRuntimeStatus:
    status = BotRuntimeStatus.load()
    status.state = state
    if detail:
        status.detail = detail[:1000]
    if bot_name:
        status.bot_name = bot_name
    if bot_username:
        status.bot_username = bot_username
    if last_marker is not None:
        status.last_marker = last_marker
    if touch_poll:
        status.last_poll_at = timezone.now()
    if touch_update:
        status.last_update_at = timezone.now()
    if clear_error:
        status.last_error = ""
    status.save()
    return status


def set_bot_error(message: str) -> None:
    status = BotRuntimeStatus.load()
    status.state = "error"
    status.last_error = message[:2000]
    status.detail = message[:1000]
    status.save(update_fields=["state", "last_error", "detail", "updated_at"])
    logger.error("Bot status error: %s", message)


def normalize_sender(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize MAX user payload to always expose user_id as string."""
    if not payload:
        return {}
    data = dict(payload)
    uid = data.get("user_id")
    if uid is None:
        uid = data.get("id")
    if uid is not None:
        data["user_id"] = uid
    return data
