"""Auth: phone OTP stub, MAX one-time code, permanent PIN."""

from __future__ import annotations

import random
from datetime import timedelta

from django.conf import settings
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from api.http import api_login_required, api_public, json_response, parse_json
from api.models import MobileAuthToken, PhoneOtpChallenge, PinChallengeKind
from database.models import BotUser
from services import mobile_auth
from subscriptions.receipts import normalize_phone


def _error(code: str, status: int = 400, detail: str | None = None):
    if status == 400 and code == "user_deactivated":
        status = 403
    text = detail or {
        "not_registered": (
            "Номер не найден. Нажмите «Регистрация», заполните анкету "
            "и подтвердите код из бота Voitos в Max."
        ),
        "already_registered": "Этот номер уже зарегистрирован — нажмите «Войти».",
        "invalid_phone": "Укажите корректный номер телефона",
        "invalid_code": "Неверный или просроченный код",
        "max_required": "Сначала привяжите аккаунт в боте Max",
        "name_required": "Укажите ФИО",
        "gender_invalid": "Укажите пол",
        "birth_date_required": "Укажите дату рождения",
        "birth_date_invalid": "Проверьте дату рождения",
        "address_required": "Укажите адрес",
        "locality_required": "Укажите населённый пункт",
        "draft_not_found": (
            "Заявка на регистрацию не найдена или устарела. "
            "Заполните анкету ещё раз."
        ),
        "max_phone_bound": (
            "К этому аккаунту Max уже привязан другой номер телефона. "
            "Войдите под ним или используйте другой Max."
        ),
        "user_deactivated": (
            "Аккаунт отключён администратором. "
            "Обратитесь в поддержку — вход недоступен."
        ),
    }.get(code, code)
    return json_response({"error": code, "detail": text}, status=status)


def _check_internal(request) -> bool:
    expected = (getattr(settings, "AUTH_BOT_INTERNAL_TOKEN", None) or "").strip()
    if not expected:
        # Dev: allow without token when MOBILE_OTP_DEBUG
        return bool(getattr(settings, "MOBILE_OTP_DEBUG", False) or settings.DEBUG)
    got = (
        request.headers.get("X-Voitos-Internal")
        or request.META.get("HTTP_X_VOITOS_INTERNAL")
        or ""
    ).strip()
    return got == expected and bool(expected)


@api_public
@require_http_methods(["POST"])
def phone_start(request):
    data = parse_json(request)
    phone = normalize_phone(data.get("phone") or "")
    if len(phone) < 10:
        return _error("invalid_phone")
    if len(phone) == 10:
        phone = "8" + phone
    code = f"{random.randint(0, 999999):06d}"
    expires = timezone.now() + timedelta(minutes=10)
    PhoneOtpChallenge.objects.create(phone=phone, code=code, expires_at=expires)
    body: dict = {"ok": True, "expires_in": 600}
    if settings.DEBUG or getattr(settings, "MOBILE_OTP_DEBUG", False):
        body["debug_code"] = code
    return json_response(body)


@api_public
@require_http_methods(["POST"])
def phone_verify(request):
    data = parse_json(request)
    phone = normalize_phone(data.get("phone") or "")
    if len(phone) == 10:
        phone = "8" + phone
    code = (data.get("code") or "").strip()
    challenge = (
        PhoneOtpChallenge.objects.filter(phone=phone, consumed_at__isnull=True)
        .order_by("-created_at")
        .first()
    )
    if not challenge or not challenge.is_valid(code):
        return _error("invalid_code")
    challenge.consumed_at = timezone.now()
    challenge.save(update_fields=["consumed_at"])

    user = BotUser.objects.filter(phone=phone).order_by("-last_seen_at").first()
    if not user:
        user = BotUser.objects.create(
            max_user_id=f"app_{phone}",
            phone=phone,
            display_name=phone,
        )
    token = MobileAuthToken.objects.create(
        bot_user=user,
        device_name=(data.get("device_name") or "")[:128],
    )
    return json_response(mobile_auth.auth_payload(user, token))


@api_public
@require_http_methods(["POST"])
def phone_login_request(request):
    """
    Приложение: телефон → если пользователь есть в боте Max, код уходит в Max.
    Иначе not_registered (нужна регистрация в боте).
    """
    data = parse_json(request)
    try:
        body = mobile_auth.request_login_code_by_phone(str(data.get("phone") or ""))
    except ValueError as exc:
        code = str(exc)
        status = 404 if code == "not_registered" else 400
        return _error(code, status=status)
    return json_response(body)


@api_public
@require_http_methods(["POST"])
def phone_login_verify(request):
    """Проверка кода из Max по телефону → access_token."""
    data = parse_json(request)
    try:
        body = mobile_auth.verify_challenge(
            phone=str(data.get("phone") or ""),
            code=str(data.get("code") or ""),
            kind=PinChallengeKind.LOGIN,
            device_name=str(data.get("device_name") or ""),
        )
    except ValueError as exc:
        return _error(str(exc))
    return json_response(body)


@api_public
@require_http_methods(["POST"])
def register_start(request):
    """Анкета регистрации из приложения (до кода из Max)."""
    data = parse_json(request)
    try:
        body = mobile_auth.start_app_registration(
            phone=str(data.get("phone") or ""),
            real_name=str(data.get("real_name") or data.get("name") or ""),
            gender=str(data.get("gender") or ""),
            birth_date=data.get("birth_date") or data.get("birthDate") or "",
            address=str(data.get("address") or ""),
            locality=str(data.get("locality") or ""),
        )
    except ValueError as exc:
        code = str(exc)
        status = 409 if code == "already_registered" else 400
        return _error(code, status=status)
    return json_response(body)


@api_public
@require_http_methods(["POST"])
def register_confirm(request):
    """Код из бота Max → завершение регистрации и access_token."""
    data = parse_json(request)
    try:
        body = mobile_auth.confirm_app_registration(
            phone=str(data.get("phone") or ""),
            code=str(data.get("code") or ""),
            device_name=str(data.get("device_name") or ""),
        )
    except ValueError as exc:
        return _error(str(exc))
    return json_response(body)


@api_public
@require_http_methods(["GET"])
def auth_config(request):
    """Публичные настройки экрана входа."""
    from services.app_version import mobile_version_payload

    body = {
        "max_bot_open_url": mobile_auth.max_bot_open_url(),
        "app_deep_link": mobile_auth.app_deep_link(),
        "registration_hint": (
            "Если номера нет — «Регистрация»: ФИО, адрес, пол, дата рождения, "
            "затем код из бота Voitos в Max."
        ),
    }
    body.update(mobile_version_payload())
    return json_response(body)


@api_public
@require_http_methods(["POST"])
def max_start(request):
    """Вызов из бота / internal: выдать разовый код в MAX."""
    if not _check_internal(request):
        return _error("forbidden", status=403)
    data = parse_json(request)
    try:
        body = mobile_auth.start_max_login(
            max_user_id=str(data.get("max_user_id") or ""),
            phone=str(data.get("phone") or ""),
            chat_id=str(data.get("chat_id") or ""),
            display_name=str(data.get("display_name") or ""),
            send=bool(data.get("send", True)),
        )
    except ValueError as exc:
        return _error(str(exc))
    return json_response(body)


@api_public
@require_http_methods(["POST"])
def max_verify(request):
    data = parse_json(request)
    try:
        body = mobile_auth.verify_challenge(
            phone=str(data.get("phone") or ""),
            code=str(data.get("code") or ""),
            kind=PinChallengeKind.LOGIN,
            device_name=str(data.get("device_name") or ""),
        )
    except ValueError as exc:
        return _error(str(exc))
    return json_response(body)


@api_login_required
@require_http_methods(["POST"])
def pin_set(request):
    data = parse_json(request)
    try:
        mobile_auth.set_pin(request.bot_user, str(data.get("pin") or ""))
    except ValueError as exc:
        return _error(str(exc))
    return json_response({"ok": True, "has_pin": True})


@api_public
@require_http_methods(["POST"])
def pin_login(request):
    data = parse_json(request)
    try:
        body = mobile_auth.pin_login(
            phone=str(data.get("phone") or ""),
            pin=str(data.get("pin") or ""),
            device_name=str(data.get("device_name") or ""),
        )
    except ValueError as exc:
        code = str(exc)
        status = 423 if code == "pin_locked" else 400
        return _error(code, status=status)
    return json_response(body)


@api_public
@require_http_methods(["POST"])
def pin_reset_request(request):
    data = parse_json(request)
    try:
        body = mobile_auth.request_reset_by_phone(str(data.get("phone") or ""))
    except ValueError as exc:
        return _error(str(exc))
    return json_response(body)


@api_public
@require_http_methods(["POST"])
def pin_reset_confirm(request):
    data = parse_json(request)
    try:
        body = mobile_auth.reset_pin_with_code(
            phone=str(data.get("phone") or ""),
            code=str(data.get("code") or ""),
            new_pin=str(data.get("pin") or data.get("new_pin") or ""),
        )
    except ValueError as exc:
        return _error(str(exc))
    return json_response(body)


@api_login_required
@require_http_methods(["POST"])
def pin_change_request(request):
    try:
        body = mobile_auth.request_change_for_user(request.bot_user)
    except ValueError as exc:
        return _error(str(exc))
    return json_response(body)


@api_login_required
@require_http_methods(["POST"])
def pin_change_confirm(request):
    data = parse_json(request)
    try:
        body = mobile_auth.change_pin_with_code(
            request.bot_user,
            code=str(data.get("code") or ""),
            new_pin=str(data.get("pin") or data.get("new_pin") or ""),
        )
    except ValueError as exc:
        return _error(str(exc))
    return json_response(body)


@api_login_required
@require_http_methods(["POST"])
def logout(request):
    request.mobile_token.revoke()
    return json_response({"ok": True}, status=200)


@api_login_required
@require_http_methods(["POST"])
def register_device(request):
    data = parse_json(request)
    push = (data.get("push_token") or "").strip()
    platform = (data.get("platform") or "").strip().lower()
    if not push or platform not in {"android", "ios"}:
        return _error("invalid_device")
    tok = request.mobile_token
    tok.push_token = push[:512]
    tok.push_platform = platform
    tok.save(update_fields=["push_token", "push_platform", "last_used_at"])
    return json_response({"ok": True})
