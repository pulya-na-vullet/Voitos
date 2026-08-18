"""Auth: phone OTP stub → MobileAuthToken."""

from __future__ import annotations

import random
from datetime import timedelta

from django.utils import timezone
from django.views.decorators.http import require_http_methods

from api.http import api_login_required, api_public, json_response, parse_json
from api.models import MobileAuthToken, PhoneOtpChallenge
from database.models import BotUser
from subscriptions.receipts import normalize_phone


@api_public
@require_http_methods(["POST"])
def phone_start(request):
    data = parse_json(request)
    phone = normalize_phone(data.get("phone") or "")
    if len(phone) < 10:
        return json_response({"error": "invalid_phone"}, status=400)
    if len(phone) == 10:
        phone = "8" + phone
    code = f"{random.randint(0, 999999):06d}"
    expires = timezone.now() + timedelta(minutes=10)
    PhoneOtpChallenge.objects.create(phone=phone, code=code, expires_at=expires)
    # В production — SMS. В DEBUG отдаём код для тестов/KMP.
    from django.conf import settings

    body: dict = {"ok": True, "expires_in": 600}
    # Django в TestCase принудительно DEBUG=False — отдельный флаг для OTP.
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
        return json_response({"error": "invalid_code"}, status=400)
    challenge.consumed_at = timezone.now()
    challenge.save(update_fields=["consumed_at"])

    user = BotUser.objects.filter(phone=phone).order_by("-last_seen_at").first()
    if not user:
        # Новый житель: временный max_user_id, анкета дозаполнится в app
        user = BotUser.objects.create(
            max_user_id=f"app_{phone}",
            phone=phone,
            display_name=phone,
        )
    token = MobileAuthToken.objects.create(
        bot_user=user,
        device_name=(data.get("device_name") or "")[:128],
    )
    return json_response(
        {
            "access_token": token.token,
            "bot_user_id": user.id,
            "display_name": str(user),
        }
    )


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
        return json_response({"error": "invalid_device"}, status=400)
    tok = request.mobile_token
    tok.push_token = push[:512]
    tok.push_platform = platform
    tok.save(update_fields=["push_token", "push_platform", "last_used_at"])
    return json_response({"ok": True})
