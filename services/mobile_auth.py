"""Вход в приложение через MAX (разовый код) и постоянный PIN."""

from __future__ import annotations

import logging
import random
import re
from datetime import date, datetime, timedelta

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.db import transaction
from django.utils import timezone

from api.models import AppRegistrationDraft, MobileAuthToken, PinChallenge, PinChallengeKind
from database.models import AppSettings, BotUser, UserGender
from subscriptions.receipts import normalize_phone

logger = logging.getLogger(__name__)

PIN_RE = re.compile(r"^\d{4}$")
CODE_TTL_MINUTES = 10
DRAFT_TTL_HOURS = 24
PIN_MAX_ATTEMPTS = 5
PIN_LOCK_MINUTES = 15
CHALLENGE_MAX_ATTEMPTS = 8

APP_LOGIN_PENDING = "app_login_phone"
APP_REGISTER_PENDING = "app_register_phone"
APP_LOGIN_KEYWORDS = (
    "войти в приложение",
    "код для приложения",
    "код входа",
    "пин для приложения",
    "открыть приложение",
    "/app",
    "/login",
    "приложение voitos",
)
APP_REGISTER_KEYWORDS = (
    "код регистрации",
    "подтвердить регистрацию",
    "регистрация в приложении",
    "код для регистрации",
    "/register",
)


def normalize_user_phone(raw: str) -> str:
    phone = normalize_phone(raw or "")
    if len(phone) == 10:
        phone = "8" + phone
    return phone


def validate_pin(pin: str) -> str:
    pin = (pin or "").strip()
    if not PIN_RE.match(pin):
        raise ValueError("pin_invalid")
    return pin


def needs_pin_setup(user: BotUser) -> bool:
    return not bool((user.pin_hash or "").strip())


def has_pin(user: BotUser) -> bool:
    return bool((user.pin_hash or "").strip())


def app_deep_link() -> str:
    return "voitos://app/login"


def max_bot_open_url() -> str:
    cfg = AppSettings.load()
    url = (getattr(cfg, "max_bot_open_url", None) or "").strip()
    if url:
        return url
    url = (getattr(settings, "MAX_BOT_OPEN_URL", None) or "").strip()
    if url:
        return url
    return "https://max.ru/se13602985_1_bot"


def auth_payload(user: BotUser, token: MobileAuthToken) -> dict:
    from bot.onboarding import is_complete

    return {
        "access_token": token.token,
        "bot_user_id": user.id,
        "display_name": str(user),
        "needs_pin_setup": needs_pin_setup(user),
        "has_pin": has_pin(user),
        "phone": user.phone or "",
        "needs_onboarding": not is_complete(user),
    }


def issue_token(user: BotUser, *, device_name: str = "") -> MobileAuthToken:
    return MobileAuthToken.objects.create(
        bot_user=user,
        device_name=(device_name or "")[:128],
    )


@transaction.atomic
def upsert_max_user(
    *,
    max_user_id: str,
    phone: str,
    chat_id: str = "",
    display_name: str = "",
) -> tuple[BotUser, bool]:
    """Найти/создать BotUser; слить app_{phone} при первом входе из MAX."""
    max_user_id = (max_user_id or "").strip()
    phone = normalize_user_phone(phone)
    chat_id = (chat_id or "").strip()
    display_name = (display_name or "").strip()
    if not max_user_id:
        raise ValueError("max_user_id_required")
    if len(phone) < 11:
        raise ValueError("invalid_phone")

    user = BotUser.objects.select_for_update().filter(max_user_id=max_user_id).first()
    if user:
        fields = ["last_seen_at"]
        # Не перезаписываем уже привязанный другой телефон (защита аккаунта/семьи).
        if phone and user.phone != phone:
            bound = normalize_user_phone(user.phone or "")
            if len(bound) < 11:
                user.phone = phone
                fields.append("phone")
            elif bound != phone:
                raise ValueError("max_phone_bound")
        if chat_id and user.chat_id != chat_id:
            user.chat_id = chat_id
            fields.append("chat_id")
        if display_name and not user.display_name:
            user.display_name = display_name
            fields.append("display_name")
        user.save(update_fields=fields)
        return user, False

    app_key = f"app_{phone}"
    app_user = BotUser.objects.select_for_update().filter(max_user_id=app_key).first()
    if app_user:
        app_user.max_user_id = max_user_id
        app_user.phone = phone
        if chat_id:
            app_user.chat_id = chat_id
        if display_name and not app_user.display_name:
            app_user.display_name = display_name
        app_user.save()
        return app_user, False

    by_phone = (
        BotUser.objects.select_for_update()
        .filter(phone=phone)
        .order_by("-last_seen_at")
        .first()
    )
    if by_phone and str(by_phone.max_user_id).startswith("app_"):
        by_phone.max_user_id = max_user_id
        if chat_id:
            by_phone.chat_id = chat_id
        if display_name and not by_phone.display_name:
            by_phone.display_name = display_name
        by_phone.save()
        return by_phone, False

    user = BotUser.objects.create(
        max_user_id=max_user_id,
        phone=phone,
        chat_id=chat_id,
        display_name=display_name or phone,
    )
    return user, True


def _generate_code() -> str:
    return f"{random.randint(0, 9999):04d}"


def create_challenge(
    user: BotUser,
    *,
    kind: str = PinChallengeKind.LOGIN,
) -> tuple[PinChallenge, str]:
    phone = normalize_user_phone(user.phone)
    if len(phone) < 11:
        raise ValueError("phone_required")
    code = _generate_code()
    # Invalidate previous open challenges of same kind
    PinChallenge.objects.filter(
        phone=phone,
        kind=kind,
        consumed_at__isnull=True,
    ).update(consumed_at=timezone.now())
    ch = PinChallenge.objects.create(
        bot_user=user,
        phone=phone,
        kind=kind,
        code_hash=make_password(code),
        expires_at=timezone.now() + timedelta(minutes=CODE_TTL_MINUTES),
    )
    return ch, code


def _send_max_code(user: BotUser, code: str, *, kind: str) -> None:
    from bot.client import MaxClient, MaxApiError

    cfg = AppSettings.load()
    token = (cfg.max_bot_token or settings.MAX_BOT_TOKEN or "").strip()
    if not token:
        logger.warning("MAX bot token missing — cannot send login code")
        return
    if kind == PinChallengeKind.RESET:
        lead = "Код для сброса PIN в приложении Voitos"
    elif kind == PinChallengeKind.CHANGE:
        lead = "Код подтверждения смены PIN в приложении Voitos"
    elif kind == PinChallengeKind.REGISTER:
        lead = "Код для завершения регистрации в приложении Voitos"
    else:
        lead = "Код для входа в приложение Voitos"
    text = (
        f"{lead}: {code}\n"
        f"Действует {CODE_TTL_MINUTES} мин.\n\n"
        f"Откройте приложение и введите код.\n"
        f"Или ссылка: {app_deep_link()}"
    )
    client = MaxClient(token)
    try:
        if user.chat_id:
            client.send_message(text, chat_id=user.chat_id)
        else:
            client.send_message(text, user_id=user.max_user_id)
    except MaxApiError:
        logger.exception("Failed to send MAX login code to user %s", user.id)


def is_max_registered(user: BotUser | None) -> bool:
    """Житель прошёл бота Max: есть телефон и настоящий max_user_id (не app_*)."""
    if user is None:
        return False
    phone = normalize_user_phone(user.phone or "")
    if len(phone) < 11:
        return False
    mid = (user.max_user_id or "").strip()
    if not mid or mid.startswith("app_"):
        return False
    return True


def find_registered_user(phone: str) -> BotUser | None:
    phone = normalize_user_phone(phone)
    if len(phone) < 11:
        return None
    qs = BotUser.objects.filter(phone=phone).order_by("-last_seen_at")
    for user in qs:
        if is_max_registered(user):
            return user
    return None


def request_login_code_by_phone(phone: str, *, send: bool = True) -> dict:
    """
    Вход из приложения: телефон → если есть в боте Max, шлём код в Max.
    Иначе not_registered — нужна регистрация в боте.
    """
    phone = normalize_user_phone(phone)
    if len(phone) < 11:
        raise ValueError("invalid_phone")
    user = find_registered_user(phone)
    if not user:
        raise ValueError("not_registered")
    _ch, code = create_challenge(user, kind=PinChallengeKind.LOGIN)
    if send:
        _send_max_code(user, code, kind=PinChallengeKind.LOGIN)
    body: dict = {
        "ok": True,
        "expires_in": CODE_TTL_MINUTES * 60,
        "phone": user.phone,
        "message": "Код отправлен в Max",
    }
    if settings.DEBUG or getattr(settings, "MOBILE_OTP_DEBUG", False):
        body["debug_code"] = code
    return body


def start_max_login(
    *,
    max_user_id: str,
    phone: str,
    chat_id: str = "",
    display_name: str = "",
    send: bool = True,
) -> dict:
    user, is_new = upsert_max_user(
        max_user_id=max_user_id,
        phone=phone,
        chat_id=chat_id,
        display_name=display_name,
    )
    _ch, code = create_challenge(user, kind=PinChallengeKind.LOGIN)
    if send:
        _send_max_code(user, code, kind=PinChallengeKind.LOGIN)
    body: dict = {
        "ok": True,
        "expires_in": CODE_TTL_MINUTES * 60,
        "is_new_user": is_new,
        "phone": user.phone,
        "deep_link": app_deep_link(),
    }
    if settings.DEBUG or getattr(settings, "MOBILE_OTP_DEBUG", False):
        body["debug_code"] = code
    return body


def verify_challenge(
    *,
    phone: str,
    code: str,
    kind: str = PinChallengeKind.LOGIN,
    device_name: str = "",
    issue_auth_token: bool = True,
) -> dict:
    phone = normalize_user_phone(phone)
    code = (code or "").strip()
    if not PIN_RE.match(code):
        raise ValueError("invalid_code")
    ch = (
        PinChallenge.objects.filter(phone=phone, kind=kind, consumed_at__isnull=True)
        .order_by("-created_at")
        .first()
    )
    if not ch or not ch.is_open():
        raise ValueError("invalid_code")
    if ch.failed_attempts >= CHALLENGE_MAX_ATTEMPTS:
        raise ValueError("too_many_attempts")
    if not check_password(code, ch.code_hash):
        ch.failed_attempts += 1
        ch.save(update_fields=["failed_attempts"])
        raise ValueError("invalid_code")
    ch.consumed_at = timezone.now()
    ch.save(update_fields=["consumed_at"])
    user = ch.bot_user or BotUser.objects.filter(phone=phone).order_by("-last_seen_at").first()
    if not user:
        raise ValueError("user_not_found")
    if not issue_auth_token:
        return {"ok": True, "bot_user_id": user.id, "phone": user.phone}
    token = issue_token(user, device_name=device_name)
    return auth_payload(user, token)


def set_pin(user: BotUser, pin: str) -> None:
    pin = validate_pin(pin)
    user.pin_hash = make_password(pin)
    user.pin_failed_attempts = 0
    user.pin_locked_until = None
    user.save(update_fields=["pin_hash", "pin_failed_attempts", "pin_locked_until", "last_seen_at"])


def _ensure_not_locked(user: BotUser) -> None:
    if user.pin_locked_until and user.pin_locked_until > timezone.now():
        raise ValueError("pin_locked")


def pin_login(*, phone: str, pin: str, device_name: str = "") -> dict:
    phone = normalize_user_phone(phone)
    pin = validate_pin(pin)
    user = BotUser.objects.filter(phone=phone).order_by("-last_seen_at").first()
    if not user or not has_pin(user):
        raise ValueError("invalid_pin")
    _ensure_not_locked(user)
    if not check_password(pin, user.pin_hash):
        user.pin_failed_attempts = int(user.pin_failed_attempts or 0) + 1
        fields = ["pin_failed_attempts", "last_seen_at"]
        if user.pin_failed_attempts >= PIN_MAX_ATTEMPTS:
            user.pin_locked_until = timezone.now() + timedelta(minutes=PIN_LOCK_MINUTES)
            user.pin_failed_attempts = 0
            fields.append("pin_locked_until")
        user.save(update_fields=fields)
        raise ValueError("invalid_pin")
    user.pin_failed_attempts = 0
    user.pin_locked_until = None
    user.save(update_fields=["pin_failed_attempts", "pin_locked_until", "last_seen_at"])
    token = issue_token(user, device_name=device_name)
    return auth_payload(user, token)


def request_reset_by_phone(phone: str, *, send: bool = True) -> dict:
    phone = normalize_user_phone(phone)
    user = BotUser.objects.filter(phone=phone).order_by("-last_seen_at").first()
    if not user:
        # Не раскрываем наличие пользователя
        return {"ok": True, "expires_in": CODE_TTL_MINUTES * 60}
    if str(user.max_user_id).startswith("app_") and not user.chat_id:
        raise ValueError("max_required")
    _ch, code = create_challenge(user, kind=PinChallengeKind.RESET)
    if send:
        _send_max_code(user, code, kind=PinChallengeKind.RESET)
    body: dict = {"ok": True, "expires_in": CODE_TTL_MINUTES * 60, "phone": phone}
    if settings.DEBUG or getattr(settings, "MOBILE_OTP_DEBUG", False):
        body["debug_code"] = code
    return body


def request_change_for_user(user: BotUser, *, send: bool = True) -> dict:
    if not (user.phone or "").strip():
        raise ValueError("phone_required")
    if str(user.max_user_id).startswith("app_") and not user.chat_id:
        raise ValueError("max_required")
    _ch, code = create_challenge(user, kind=PinChallengeKind.CHANGE)
    if send:
        _send_max_code(user, code, kind=PinChallengeKind.CHANGE)
    body: dict = {"ok": True, "expires_in": CODE_TTL_MINUTES * 60}
    if settings.DEBUG or getattr(settings, "MOBILE_OTP_DEBUG", False):
        body["debug_code"] = code
    return body


def reset_pin_with_code(*, phone: str, code: str, new_pin: str) -> dict:
    verify_challenge(
        phone=phone,
        code=code,
        kind=PinChallengeKind.RESET,
        issue_auth_token=False,
    )
    user = BotUser.objects.filter(phone=normalize_user_phone(phone)).order_by("-last_seen_at").first()
    if not user:
        raise ValueError("user_not_found")
    set_pin(user, new_pin)
    token = issue_token(user)
    return auth_payload(user, token)


def change_pin_with_code(user: BotUser, *, code: str, new_pin: str) -> dict:
    verify_challenge(
        phone=user.phone,
        code=code,
        kind=PinChallengeKind.CHANGE,
        issue_auth_token=False,
    )
    set_pin(user, new_pin)
    return {"ok": True}


def looks_like_app_login(text: str) -> bool:
    lower = (text or "").strip().lower()
    if not lower:
        return False
    return any(k in lower for k in APP_LOGIN_KEYWORDS)


def bot_start_app_login(user: BotUser) -> str:
    """Сообщение бота: выдать код или попросить телефон."""
    phone = normalize_user_phone(user.phone or "")
    if len(phone) < 11:
        from database.models import PendingAction

        pending, _ = PendingAction.objects.get_or_create(user=user)
        pending.pending_kind = APP_LOGIN_PENDING
        pending.pending_payload = {"step": "phone"}
        pending.save(update_fields=["pending_kind", "pending_payload", "updated_at"])
        return (
            "Чтобы войти в приложение Voitos, пришлите номер телефона "
            "(например 89625507832). Я пришлю код для входа."
        )
    result = start_max_login(
        max_user_id=user.max_user_id,
        phone=phone,
        chat_id=user.chat_id or "",
        display_name=user.display_name or "",
        send=True,
    )
    extra = ""
    if result.get("debug_code"):
        extra = f"\n(dev) код: {result['debug_code']}"
    return (
        "Код для входа отправлен в этот чат.\n"
        "Откройте приложение Voitos → введите код → задайте постоянный PIN.\n"
        f"Ссылка: {app_deep_link()}"
        f"{extra}"
    )


def bot_handle_app_login_phone(user: BotUser, text: str) -> str:
    from database.models import PendingAction

    phone = normalize_user_phone(text)
    if len(phone) < 11:
        return "Нужен номер из 11 цифр, например 89625507832."
    user.phone = phone
    user.save(update_fields=["phone", "last_seen_at"])
    pending, _ = PendingAction.objects.get_or_create(user=user)
    pending.pending_kind = ""
    pending.pending_payload = {}
    pending.save(update_fields=["pending_kind", "pending_payload", "updated_at"])
    return bot_start_app_login(user)


def _parse_birth_date(raw) -> date:
    if isinstance(raw, date) and not isinstance(raw, datetime):
        value = raw
    else:
        text = str(raw or "").strip()
        if not text:
            raise ValueError("birth_date_required")
        value = None
        for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y"):
            try:
                value = datetime.strptime(text, fmt).date()
                break
            except ValueError:
                continue
        if value is None:
            raise ValueError("birth_date_invalid")
    today = timezone.localdate()
    if value > today:
        raise ValueError("birth_date_invalid")
    age = today.year - value.year - (
        (today.month, today.day) < (value.month, value.day)
    )
    if age < 5 or age > 120:
        raise ValueError("birth_date_invalid")
    return value


def _normalize_gender(raw: str) -> str:
    g = (raw or "").strip().lower()
    aliases = {
        "male": UserGender.MALE,
        "m": UserGender.MALE,
        "м": UserGender.MALE,
        "муж": UserGender.MALE,
        "мужской": UserGender.MALE,
        "female": UserGender.FEMALE,
        "f": UserGender.FEMALE,
        "ж": UserGender.FEMALE,
        "жен": UserGender.FEMALE,
        "женский": UserGender.FEMALE,
        "other": UserGender.OTHER,
        "другое": UserGender.OTHER,
        "другой": UserGender.OTHER,
    }
    if g not in aliases:
        raise ValueError("gender_invalid")
    return aliases[g]


def get_open_registration_draft(phone: str) -> AppRegistrationDraft | None:
    phone = normalize_user_phone(phone)
    if len(phone) < 11:
        return None
    draft = (
        AppRegistrationDraft.objects.filter(phone=phone, consumed_at__isnull=True)
        .order_by("-created_at")
        .first()
    )
    if not draft or not draft.is_open():
        return None
    return draft


def start_app_registration(
    *,
    phone: str,
    real_name: str,
    gender: str,
    birth_date,
) -> dict:
    """Сохранить анкету из приложения; код выдаёт бот Max."""
    phone = normalize_user_phone(phone)
    if len(phone) < 11:
        raise ValueError("invalid_phone")
    if find_registered_user(phone):
        raise ValueError("already_registered")
    name = (real_name or "").strip()
    if len(name) < 2:
        raise ValueError("name_required")
    gender_norm = _normalize_gender(gender)
    birth = _parse_birth_date(birth_date)

    AppRegistrationDraft.objects.filter(
        phone=phone, consumed_at__isnull=True
    ).update(consumed_at=timezone.now())
    draft = AppRegistrationDraft.objects.create(
        phone=phone,
        real_name=name[:255],
        gender=gender_norm,
        birth_date=birth,
        expires_at=timezone.now() + timedelta(hours=DRAFT_TTL_HOURS),
    )
    bot_url = registration_max_deep_link(draft)
    return {
        "ok": True,
        "phone": phone,
        "max_bot_open_url": bot_url,
        "message": (
            "Нажмите «Перейти в Max» — бот сразу пришлёт код. "
            "Если кода нет, напишите в боте «код»."
        ),
    }


def registration_max_deep_link(draft: AppRegistrationDraft) -> str:
    """Deep link с payload: при открытии бот сразу выдаёт код регистрации."""
    base = max_bot_open_url().split("?")[0].rstrip("/")
    return f"{base}?start=appreg-{draft.id}"


def parse_app_register_payload(payload: str) -> tuple[str | None, int | None]:
    """Разбор start-payload → (phone, draft_id)."""
    raw = (payload or "").strip()
    if not raw:
        return None, None
    lower = raw.lower()
    m = re.match(r"^appreg[-_](\d+)$", lower)
    if m:
        return None, int(m.group(1))
    m = re.match(r"^reg(\d{10,15})$", lower)
    if m:
        return normalize_user_phone(m.group(1)), None
    return None, None


def bot_handle_app_register_start_payload(user: BotUser, payload: str) -> str | None:
    """Ответ на bot_started с payload из приложения; None если не наш payload."""
    phone, draft_id = parse_app_register_payload(payload)
    if draft_id is not None:
        draft = (
            AppRegistrationDraft.objects.filter(id=draft_id, consumed_at__isnull=True)
            .order_by("-created_at")
            .first()
        )
        if not draft or not draft.is_open():
            return (
                "Заявка из приложения не найдена или устарела.\n"
                "Заполните регистрацию в приложении Voitos ещё раз."
            )
        return bot_issue_register_code(user, phone=draft.phone)
    if phone:
        return bot_issue_register_code(user, phone=phone)
    return None


@transaction.atomic
def confirm_app_registration(
    *,
    phone: str,
    code: str,
    device_name: str = "",
) -> dict:
    phone = normalize_user_phone(phone)
    draft = get_open_registration_draft(phone)
    if not draft:
        raise ValueError("draft_not_found")
    verified = verify_challenge(
        phone=phone,
        code=code,
        kind=PinChallengeKind.REGISTER,
        issue_auth_token=False,
    )
    user = (
        BotUser.objects.select_for_update()
        .filter(id=verified["bot_user_id"])
        .first()
    )
    if not user:
        raise ValueError("user_not_found")
    mid = (user.max_user_id or "").strip()
    if not mid or mid.startswith("app_"):
        raise ValueError("max_required")

    # Защита: код мог устареть/обойти — не даём сменить телефон у занятого Max.
    conflict = max_phone_bind_conflict_message(user, phone)
    if conflict:
        raise ValueError("max_phone_bound")

    # Привязка телефона только если его ещё не было или он совпадает.
    bound = normalize_user_phone(user.phone or "")
    if len(bound) < 11:
        user.phone = phone
    elif bound != phone:
        raise ValueError("max_phone_bound")

    user.real_name = draft.real_name
    user.gender = draft.gender
    user.birth_date = draft.birth_date
    user.save(
        update_fields=[
            "phone",
            "real_name",
            "gender",
            "birth_date",
            "last_seen_at",
        ]
    )
    draft.consumed_at = timezone.now()
    draft.save(update_fields=["consumed_at"])
    token = issue_token(user, device_name=device_name)
    return auth_payload(user, token)


def looks_like_app_register(text: str) -> bool:
    lower = (text or "").strip().lower()
    if not lower:
        return False
    # Короткие команды от нового клиента после перехода из приложения
    if lower in {
        "код",
        "code",
        "старт",
        "start",
        "/start",
        "регистрация приложение",
    }:
        return True
    return any(k in lower for k in APP_REGISTER_KEYWORDS)


def bot_issue_register_code(user: BotUser, phone: str | None = None) -> str:
    """Выдать код регистрации Max-пользователю по заявке из приложения."""
    phone_n = normalize_user_phone(phone or user.phone or "")
    if len(phone_n) < 11:
        from database.models import PendingAction

        pending, _ = PendingAction.objects.get_or_create(user=user)
        pending.pending_kind = APP_REGISTER_PENDING
        pending.pending_payload = {"step": "phone"}
        pending.save(update_fields=["pending_kind", "pending_payload", "updated_at"])
        return (
            "Чтобы завершить регистрацию в приложении Voitos, пришлите номер телефона, "
            "который указали в приложении (например 89625507832)."
        )

    draft = get_open_registration_draft(phone_n)
    if not draft:
        return (
            "По этому номеру нет заявки из приложения.\n"
            "Сначала в приложении Voitos нажмите «Регистрация», заполните анкету "
            "и снова напишите сюда «код регистрации»."
        )

    # К этому Max уже привязан другой телефон — не перезаписываем и код не шлём.
    conflict = max_phone_bind_conflict_message(user, phone_n)
    if conflict:
        return conflict

    existing = find_registered_user(phone_n)
    if existing and existing.id != user.id:
        return (
            "Этот номер уже привязан к другому аккаунту Max. "
            "Войдите в приложение по телефону."
        )

    bound = normalize_user_phone(user.phone or "")
    if bound == phone_n:
        # Телефон уже тот же — только код, без перезаписи профиля.
        target = user
    else:
        # Новая привязка только если у Max ещё не было телефона.
        target, _ = upsert_max_user(
            max_user_id=user.max_user_id,
            phone=phone_n,
            chat_id=user.chat_id or "",
            display_name=user.display_name or "",
        )
    _ch, code = create_challenge(target, kind=PinChallengeKind.REGISTER)
    return (
        f"Код для завершения регистрации в приложении Voitos: {code}\n"
        f"Действует {CODE_TTL_MINUTES} мин.\n\n"
        "Вернитесь в приложение и введите этот код."
    )


def max_phone_bind_conflict_message(user: BotUser, phone: str) -> str | None:
    """
    Если у Max-аккаунта уже есть другой телефон — нельзя регистрировать новый номер.
    Иначе чужой max_id «переедет» на новый phone и заберёт семью/данные.
    """
    bound = normalize_user_phone(user.phone or "")
    want = normalize_user_phone(phone)
    if len(bound) < 11:
        return None
    if bound == want:
        return None
    return (
        f"К вашему аккаунту Max уже привязан другой номер: {bound}.\n"
        f"Нельзя зарегистрировать номер {want} на этот же Max.\n\n"
        "Войдите в приложение под привязанным номером "
        "или откройте бота Voitos с другого аккаунта Max "
        "(того, на котором ещё нет привязанного телефона)."
    )


def bot_start_app_register(user: BotUser) -> str:
    return bot_issue_register_code(user)


def bot_handle_app_register_phone(user: BotUser, text: str) -> str:
    from database.models import PendingAction

    phone = normalize_user_phone(text)
    if len(phone) < 11:
        return "Нужен номер из 11 цифр, например 89625507832."
    pending, _ = PendingAction.objects.get_or_create(user=user)
    pending.pending_kind = ""
    pending.pending_payload = {}
    pending.save(update_fields=["pending_kind", "pending_payload", "updated_at"])
    return bot_issue_register_code(user, phone=phone)

