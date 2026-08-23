"""Версии мобильного клиента vs бэкенд."""

from __future__ import annotations

from django.conf import settings

from database.models import AppSettings


def _normalize_apk_url(url: str) -> str:
    """github.com/.../raw/... → raw.githubusercontent.com (без HTML-редиректа)."""
    raw = (url or "").strip()
    if not raw:
        return ""
    prefix = "https://github.com/"
    marker = "/raw/"
    if raw.startswith(prefix) and marker in raw:
        rest = raw[len(prefix) :]
        owner_repo, _, path = rest.partition(marker)
        parts = owner_repo.split("/")
        if len(parts) >= 2 and path:
            owner, repo = parts[0], parts[1]
            return f"https://raw.githubusercontent.com/{owner}/{repo}/{path}"
    if raw.startswith("http://github.com/") and marker in raw:
        return _normalize_apk_url("https://" + raw[len("http://") :])
    return raw


def mobile_version_payload() -> dict:
    cfg = AppSettings.load()
    settings_min = int(getattr(settings, "MOBILE_MIN_VERSION_CODE", 1) or 1)
    cfg_min = int(getattr(cfg, "mobile_min_version_code", None) or 0)
    # Деплой нового кода всегда поднимает пол: даже если в панели старое значение.
    min_code = max(cfg_min, settings_min)

    settings_latest = int(
        getattr(settings, "MOBILE_LATEST_VERSION_CODE", min_code) or min_code
    )
    cfg_latest = int(getattr(cfg, "mobile_latest_version_code", None) or 0)
    latest_code = max(cfg_latest, settings_latest, min_code)

    latest_name = (
        getattr(cfg, "mobile_latest_version_name", None)
        or getattr(settings, "MOBILE_LATEST_VERSION_NAME", "")
        or ""
    ).strip()
    if not latest_name:
        latest_name = str(getattr(settings, "MOBILE_LATEST_VERSION_NAME", "") or "")

    cfg_url = (getattr(cfg, "mobile_apk_url", None) or "").strip()
    settings_url = (getattr(settings, "MOBILE_APK_URL", "") or "").strip()
    # Если код на сервере новее записи в панели — берём URL из settings,
    # иначе клиент качает старый APK и снова упирается в ForceUpdate.
    if settings_latest > cfg_latest and settings_url:
        apk_url = settings_url
    else:
        apk_url = cfg_url or settings_url
    apk_url = _normalize_apk_url(apk_url)

    backend_version = str(getattr(settings, "VOITOS_BACKEND_VERSION", "") or "").strip()
    return {
        "backend_version": backend_version,
        "min_app_version_code": min_code,
        "latest_app_version_code": latest_code,
        "latest_app_version_name": latest_name,
        "apk_url": apk_url,
        "update_message": "Просим обновить приложение",
    }


def client_needs_update(client_version_code: int | None) -> bool:
    if client_version_code is None:
        return False
    try:
        code = int(client_version_code)
    except (TypeError, ValueError):
        return False
    return code < mobile_version_payload()["min_app_version_code"]
