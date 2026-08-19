"""Безопасная эмиссия событий в mobile inbox (не ломает MAX-поток)."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def emit_app_event(
    bot_user,
    *,
    ntype: str,
    title: str,
    body: str = "",
    entity_type: str = "",
    entity_id: int | None = None,
    deep_link: str = "",
    payload: dict[str, Any] | None = None,
) -> None:
    try:
        from api.notifications import notify_user

        notify_user(
            bot_user,
            ntype=ntype,
            title=title,
            body=body,
            entity_type=entity_type,
            entity_id=entity_id,
            deep_link=deep_link,
            payload=payload,
        )
    except Exception:
        logger.exception(
            "emit_app_event failed type=%s user=%s",
            ntype,
            getattr(bot_user, "id", None),
        )
