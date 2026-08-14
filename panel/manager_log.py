"""Логирование действий менеджеров панели (только для администратора)."""

from __future__ import annotations

from django.contrib.auth.models import AbstractBaseUser
from django.http import HttpRequest

from database.models import PanelActionLog
from panel.roles import is_panel_manager


def log_manager_action(
    request_or_user: HttpRequest | AbstractBaseUser,
    *,
    action: str,
    title: str,
    detail: str = "",
    meta: dict | None = None,
) -> PanelActionLog | None:
    """Пишет лог только если актёр — менеджер (не администратор)."""
    if hasattr(request_or_user, "user"):
        user = request_or_user.user
    else:
        user = request_or_user
    if not user or not getattr(user, "is_authenticated", False):
        return None
    if not is_panel_manager(user):
        return None
    return PanelActionLog.objects.create(
        actor=user,
        action=(action or "other")[:64],
        title=(title or action)[:255],
        detail=detail or "",
        meta=meta or {},
    )
