"""Версии мобильного клиента vs бэкенд."""

from __future__ import annotations

from django.conf import settings

from database.models import AppSettings


def mobile_version_payload() -> dict:
    cfg = AppSettings.load()
    min_code = int(
        getattr(cfg, "mobile_min_version_code", None)
        or getattr(settings, "MOBILE_MIN_VERSION_CODE", 1)
        or 1
    )
    latest_code = int(
        getattr(cfg, "mobile_latest_version_code", None)
        or getattr(settings, "MOBILE_LATEST_VERSION_CODE", min_code)
        or min_code
    )
    latest_name = (
        getattr(cfg, "mobile_latest_version_name", None)
        or getattr(settings, "MOBILE_LATEST_VERSION_NAME", "")
        or ""
    ).strip()
    apk_url = (
        getattr(cfg, "mobile_apk_url", None)
        or getattr(settings, "MOBILE_APK_URL", "")
        or ""
    ).strip()
    return {
        "min_app_version_code": min_code,
        "latest_app_version_code": latest_code,
        "latest_app_version_name": latest_name,
        "apk_url": apk_url,
        "update_message": (
            "Доступна новая версия приложения. "
            "Обновите Voitos, чтобы продолжить работу."
        ),
    }


def client_needs_update(client_version_code: int | None) -> bool:
    if client_version_code is None:
        return False
    try:
        code = int(client_version_code)
    except (TypeError, ValueError):
        return False
    return code < mobile_version_payload()["min_app_version_code"]
