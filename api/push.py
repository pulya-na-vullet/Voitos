"""Отправка push на устройства жителя (FCM Legacy HTTP).

Если FCM_SERVER_KEY не задан — пишем в лог (dev) и не считаем ошибкой.
"""

from __future__ import annotations

import logging
from typing import Any

import requests
from django.conf import settings
from django.utils import timezone

from api.models import AppNotification, MobileAuthToken

logger = logging.getLogger(__name__)

FCM_ENDPOINT = "https://fcm.googleapis.com/fcm/send"

# Для тестов: список последних payload'ов
_LAST_PUSHES: list[dict[str, Any]] = []


def clear_push_log() -> None:
    _LAST_PUSHES.clear()


def recent_pushes() -> list[dict[str, Any]]:
    return list(_LAST_PUSHES)


def _fcm_server_key() -> str:
    return (getattr(settings, "FCM_SERVER_KEY", "") or "").strip()


def build_fcm_payload(notification: AppNotification, push_token: str) -> dict[str, Any]:
    return {
        "to": push_token,
        "priority": "high",
        "notification": {
            "title": notification.title,
            "body": (notification.body or "")[:240],
            "sound": "default",
        },
        "data": {
            "type": notification.type,
            "title": notification.title,
            "body": notification.body or "",
            "deep_link": notification.deep_link or "",
            "entity_type": notification.entity_type or "",
            "entity_id": str(notification.entity_id or ""),
            "notification_id": str(notification.id),
        },
    }


def send_fcm(push_token: str, payload: dict[str, Any]) -> tuple[bool, str]:
    """Отправить один FCM. Возвращает (ok, detail)."""
    key = _fcm_server_key()
    if not key:
        # Dev / без Firebase: считаем «доставлено» в лог, чтобы inbox+тест жили.
        if getattr(settings, "FCM_DRY_RUN", True):
            logger.info(
                "FCM dry-run -> token=%s... type=%s",
                push_token[:12],
                (payload.get("data") or {}).get("type"),
            )
            return True, "dry_run"
        return False, "no_fcm_key"

    try:
        resp = requests.post(
            FCM_ENDPOINT,
            json=payload,
            headers={
                "Authorization": f"key={key}",
                "Content-Type": "application/json",
            },
            timeout=15,
        )
        if resp.status_code >= 400:
            return False, f"http_{resp.status_code}:{resp.text[:200]}"
        data = resp.json() if resp.content else {}
        if data.get("failure"):
            return False, str(data)[:300]
        return True, "ok"
    except Exception as exc:
        logger.exception("FCM request failed")
        return False, str(exc)[:200]


def dispatch_push(notification: AppNotification) -> int:
    """Разослать уведомление на все активные устройства пользователя.

    Возвращает число успешных доставок (включая dry-run).
    """
    tokens = list(
        MobileAuthToken.objects.filter(
            bot_user_id=notification.bot_user_id,
            revoked_at__isnull=True,
        ).exclude(push_token="")
    )
    if not tokens:
        logger.debug(
            "No push tokens for user=%s notification=%s",
            notification.bot_user_id,
            notification.id,
        )
        return 0

    ok_n = 0
    for tok in tokens:
        payload = build_fcm_payload(notification, tok.push_token)
        _LAST_PUSHES.append(
            {
                "notification_id": notification.id,
                "user_id": notification.bot_user_id,
                "type": notification.type,
                "token": tok.push_token[:16],
                "platform": tok.push_platform,
            }
        )
        ok, detail = send_fcm(tok.push_token, payload)
        if ok:
            ok_n += 1
        else:
            logger.warning(
                "Push failed user=%s token=%s… detail=%s",
                notification.bot_user_id,
                tok.push_token[:12],
                detail,
            )

    if ok_n:
        notification.push_sent_at = timezone.now()
        notification.save(update_fields=["push_sent_at"])
    return ok_n
