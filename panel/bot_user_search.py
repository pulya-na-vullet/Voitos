"""Поиск жителей бота для панели (масштабируемо: лимит + индексы)."""

from __future__ import annotations

import re

from django.db.models import Q, QuerySet

from database.models import BotUser

SEARCH_MIN_CHARS = 2
SEARCH_DEFAULT_LIMIT = 30
SEARCH_MAX_LIMIT = 50


def bot_user_search_qs(q: str) -> QuerySet[BotUser]:
    """Фильтр по имени / телефону / MAX id / населённому пункту."""
    raw = (q or "").strip()
    if len(raw) < SEARCH_MIN_CHARS:
        return BotUser.objects.none()

    digits = re.sub(r"\D+", "", raw)
    filters = (
        Q(real_name__icontains=raw)
        | Q(display_name__icontains=raw)
        | Q(username__icontains=raw)
        | Q(max_user_id__icontains=raw)
        | Q(phone__icontains=raw)
        | Q(locality__icontains=raw)
        | Q(address__icontains=raw)
    )
    if len(digits) >= 3:
        filters |= Q(phone__icontains=digits)
        # Поиск по хвосту телефона (часто вводят последние 4–10 цифр)
        if len(digits) >= 4:
            filters |= Q(phone__endswith=digits[-10:] if len(digits) > 10 else digits)

    return BotUser.objects.filter(filters)


def serialize_bot_user(u: BotUser) -> dict:
    name = (u.real_name or u.display_name or u.username or "").strip() or u.max_user_id
    return {
        "id": u.id,
        "name": name,
        "phone": (u.phone or "").strip(),
        "locality": (u.locality or "").strip(),
        "max_user_id": u.max_user_id,
        "label": _label(u, name),
    }


def _label(u: BotUser, name: str) -> str:
    parts = [name]
    if u.phone:
        parts.append(u.phone)
    if u.locality:
        parts.append(u.locality)
    return " · ".join(parts)


def search_bot_users(q: str, *, limit: int = SEARCH_DEFAULT_LIMIT) -> list[dict]:
    limit = max(1, min(int(limit or SEARCH_DEFAULT_LIMIT), SEARCH_MAX_LIMIT))
    qs = (
        bot_user_search_qs(q)
        .only(
            "id",
            "real_name",
            "display_name",
            "username",
            "phone",
            "locality",
            "max_user_id",
        )
        .order_by("real_name", "display_name", "id")[:limit]
    )
    return [serialize_bot_user(u) for u in qs]
