"""Хелперы JSON API без DRF."""

from __future__ import annotations

import json
from functools import wraps
from typing import Any, Callable

from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt

from api.models import MobileAuthToken


def json_response(data: dict[str, Any], status: int = 200) -> JsonResponse:
    return JsonResponse(data, status=status, json_dumps_params={"ensure_ascii": False})


def parse_json(request: HttpRequest) -> dict[str, Any]:
    if not request.body:
        return {}
    try:
        data = json.loads(request.body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def bearer_token(request: HttpRequest) -> str:
    auth = request.META.get("HTTP_AUTHORIZATION") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return (request.META.get("HTTP_X_VOITOS_TOKEN") or "").strip()


def get_token(request: HttpRequest) -> MobileAuthToken | None:
    raw = bearer_token(request)
    if not raw:
        return None
    auth = (
        MobileAuthToken.objects.select_related("bot_user")
        .filter(token=raw, revoked_at__isnull=True)
        .first()
    )
    if auth is None:
        return None
    # Удалённый пользователь → CASCADE уже снял токены (сюда не попадём).
    # Деактивированный — отклоняем и отзываем токен, чтобы приложение разлогинилось.
    if not getattr(auth.bot_user, "is_active", True):
        auth.revoke()
        return None
    return auth


def client_version_code(request: HttpRequest, data: dict[str, Any] | None = None) -> int | None:
    """version_code из JSON / query / заголовка X-Voitos-App-Version."""
    candidates: list[Any] = []
    if data:
        candidates.append(data.get("version_code"))
    candidates.append(request.GET.get("version_code"))
    candidates.append(request.META.get("HTTP_X_VOITOS_APP_VERSION"))
    for raw in candidates:
        if raw is None or str(raw).strip() == "":
            continue
        try:
            return int(raw)
        except (TypeError, ValueError):
            continue
    return None


def update_required_response(client_code: int | None = None):
    from services.app_version import mobile_version_payload

    body = {
        "error": "update_required",
        "detail": "Просим обновить приложение",
        "update_required": True,
    }
    body.update(mobile_version_payload())
    if client_code is not None:
        body["client_version_code"] = client_code
    return json_response(body, status=426)


def api_login_required(view: Callable):
    @wraps(view)
    @csrf_exempt
    def _wrapped(request: HttpRequest, *args, **kwargs):
        token = get_token(request)
        if not token:
            return json_response({"error": "unauthorized"}, status=401)
        request.mobile_token = token  # type: ignore[attr-defined]
        request.bot_user = token.bot_user  # type: ignore[attr-defined]
        from services.app_version import client_needs_update

        ver = client_version_code(request)
        if ver is not None and client_needs_update(ver):
            return update_required_response(ver)
        return view(request, *args, **kwargs)

    return _wrapped


def api_public(view: Callable):
    @wraps(view)
    @csrf_exempt
    def _wrapped(request: HttpRequest, *args, **kwargs):
        return view(request, *args, **kwargs)

    return _wrapped
