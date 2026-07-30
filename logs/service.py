"""Helpers for activity logging (models live in database app)."""

from __future__ import annotations

from database.models import ActivityKind, ActivityLog, BotUser


def log_activity(
    *,
    kind: str = ActivityKind.OTHER,
    title: str,
    detail: str = "",
    user: BotUser | None = None,
    meta: dict | None = None,
) -> ActivityLog:
    return ActivityLog.objects.create(
        user=user,
        kind=kind,
        title=title,
        detail=detail,
        meta=meta or {},
    )
