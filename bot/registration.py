from __future__ import annotations

import re

from django.utils import timezone

from database.models import (
    ActivityKind,
    ActivityLog,
    BotUser,
    PendingAction,
    ProfileStatus,
)
from panel.admin_tasks import task_family_claim, task_profile_review
from services.address_overlap import heuristic_candidates
from subscriptions.receipts import normalize_phone

REG_KIND = "registration"

_FAMILY_PROMPT = (
    "По этому адресу уже есть зарегистрированный житель.\n"
    "Вы проживаете вместе / являетесь членом семьи по этому адресу?\n"
    "Ответьте «да» или «нет».\n"
    "(Данные других жителей не показываются.)"
)

_YES = {"да", "yes", "y", "+", "ага", "угу", "именно", "верно", "являюсь"}
_NO = {"нет", "no", "n", "-", "не", "не являюсь", "нету"}

PROFILE_FIELD_LABELS = {
    "real_name": "Имя",
    "phone": "Телефон",
    "address": "Адрес места жительства",
    "locality": "Населённый пункт",
}

_STEP_PROMPTS = {
    "name": "Как вас зовут? (Имя)",
    "phone": "Отправьте номер телефона (например 89625507832).",
    "address": (
        "Укажите адрес места жительства.\n"
        "Формат: населённый пункт, улица, дом "
        "(например: Казань, ул. Баумана, 1)."
    ),
    "locality": "Укажите населённый пункт (город / посёлок).",
}


def missing_profile_fields(user: BotUser) -> list[tuple[str, str]]:
    """Return (field_key, label) for empty required profile fields."""
    missing: list[tuple[str, str]] = []
    if not (user.real_name or "").strip():
        missing.append(("real_name", PROFILE_FIELD_LABELS["real_name"]))
    if not (user.phone or "").strip():
        missing.append(("phone", PROFILE_FIELD_LABELS["phone"]))
    if not (user.address or "").strip():
        missing.append(("address", PROFILE_FIELD_LABELS["address"]))
    if not (user.locality or "").strip():
        missing.append(("locality", PROFILE_FIELD_LABELS["locality"]))
    return missing


def _field_to_step(field_key: str) -> str:
    return "name" if field_key == "real_name" else field_key


def _next_step_for_user(user: BotUser) -> str:
    missing = missing_profile_fields(user)
    if not missing:
        return "name"
    return _field_to_step(missing[0][0])


def needs_registration(user: BotUser) -> bool:
    return user.profile_status in {
        ProfileStatus.INCOMPLETE,
        ProfileStatus.REJECTED,
    } and bool(missing_profile_fields(user))


def start_registration(user: BotUser, pending: PendingAction) -> str:
    missing = missing_profile_fields(user)
    step = _next_step_for_user(user)
    pending.pending_kind = REG_KIND
    pending.pending_payload = {"step": step}
    pending.save(update_fields=["pending_kind", "pending_payload", "updated_at"])
    prompt = _STEP_PROMPTS.get(step, _STEP_PROMPTS["name"])
    if missing and any(
        [
            (user.real_name or "").strip(),
            (user.phone or "").strip(),
            (user.address or "").strip(),
            (user.locality or "").strip(),
        ]
    ):
        lines = "\n".join(f"• {label}" for _, label in missing)
        return f"Нужно дозаполнить недостающие поля:\n{lines}\n\n{prompt}"
    if missing:
        return (
            "Добро пожаловать! Для работы сервиса заполните короткую анкету.\n\n"
            f"{prompt}"
        )
    # Everything filled — restart from name
    pending.pending_payload = {"step": "name"}
    pending.save(update_fields=["pending_payload", "updated_at"])
    return (
        "Добро пожаловать! Для работы сервиса заполните короткую анкету.\n\n"
        f"{_STEP_PROMPTS['name']}"
    )


def incomplete_profile_user_message(user: BotUser, admin_note: str = "") -> str:
    missing = missing_profile_fields(user)
    if not missing:
        missing = list(PROFILE_FIELD_LABELS.items())
        missing = [(k, v) for k, v in PROFILE_FIELD_LABELS.items()]
    lines = "\n".join(f"• {label}" for _, label in missing)
    extra = f"\nКомментарий администратора: {admin_note}" if admin_note else ""
    return (
        "Администратор проверил ваши данные и просит дозаполнить "
        "недостающие поля:\n"
        f"{lines}"
        f"{extra}\n\n"
        "Напишите «регистрация» или ответьте на следующий вопрос бота."
    )


def begin_incomplete_profile_flow(
    user: BotUser, pending: PendingAction, admin_note: str = ""
) -> str:
    """Notify about missing fields and start dialog at the first empty field."""
    msg = incomplete_profile_user_message(user, admin_note=admin_note)
    step = _next_step_for_user(user)
    pending.pending_kind = REG_KIND
    pending.pending_payload = {"step": step}
    pending.save(update_fields=["pending_kind", "pending_payload", "updated_at"])
    prompt = _STEP_PROMPTS.get(step, _STEP_PROMPTS["name"])
    return f"{msg}\n\n{prompt}"


def _extract_locality(address: str) -> str:
    address = (address or "").strip()
    if not address:
        return ""
    first = address.split(",")[0].strip()
    first = re.sub(
        r"^(г\.|гор\.|город|п\.|пос\.|поселок|посёлок|с\.|село|д\.|деревня)\s*",
        "",
        first,
        flags=re.IGNORECASE,
    ).strip()
    return first or address[:80]


def _finish_if_complete(user: BotUser, pending: PendingAction) -> str | None:
    missing = missing_profile_fields(user)
    if missing:
        step = _field_to_step(missing[0][0])
        pending.pending_kind = REG_KIND
        pending.pending_payload = {"step": step}
        pending.save(update_fields=["pending_kind", "pending_payload", "updated_at"])
        return _STEP_PROMPTS.get(step, _STEP_PROMPTS["name"])
    user.profile_status = ProfileStatus.PENDING_REVIEW
    user.profile_submitted_at = timezone.now()
    user.save(update_fields=["profile_status", "profile_submitted_at", "last_seen_at"])
    pending.clear_pending()
    ActivityLog.objects.create(
        user=user,
        kind=ActivityKind.PROFILE_SUBMITTED,
        title="Анкета отправлена на проверку",
        detail=f"{user.real_name}, {user.phone}, {user.address}",
    )
    try:
        task_profile_review(user)
    except Exception:
        pass
    return (
        "Анкета сохранена. Администратор проверит данные.\n\n"
        f"Имя: {user.real_name}\n"
        f"Телефон: {user.phone}\n"
        f"Адрес: {user.address}\n"
        f"Населённый пункт: {user.locality}\n\n"
        "Напишите «описание» или «помощь», чтобы узнать, что умеет бот."
    )


def _begin_family_check(user: BotUser, pending: PendingAction, candidates: list[BotUser]) -> str:
    pending.pending_kind = REG_KIND
    pending.pending_payload = {
        "step": "family_check",
        "candidate_ids": [c.id for c in candidates],
        "address": user.address,
    }
    pending.save(update_fields=["pending_kind", "pending_payload", "updated_at"])
    return _FAMILY_PROMPT


def handle_registration_step(user: BotUser, text: str, pending: PendingAction) -> str:
    payload = pending.pending_payload or {}
    step = payload.get("step") or "name"
    value = (text or "").strip()
    if value.lower() in {"отмена", "стоп"}:
        pending.clear_pending()
        return "Анкета прервана. Напишите «регистрация», чтобы начать снова."

    if step == "name":
        if len(value) < 2:
            return "Укажите имя — хотя бы 2 символа."
        user.real_name = value
        user.save(update_fields=["real_name", "last_seen_at"])
        nxt = _finish_if_complete(user, pending)
        if nxt and nxt.startswith("Анкета сохранена"):
            return nxt
        return f"Спасибо. {nxt}"

    if step == "phone":
        phone = normalize_phone(value)
        if len(phone) < 10:
            return "Не распознал телефон. Пришлите номер цифрами, например 89625507832."
        if len(phone) == 10:
            phone = "8" + phone
        user.phone = phone
        user.save(update_fields=["phone", "last_seen_at"])
        nxt = _finish_if_complete(user, pending)
        if nxt and nxt.startswith("Анкета сохранена"):
            return nxt
        return f"Отлично. {nxt}"

    if step == "address":
        if len(value) < 5:
            return "Адрес слишком короткий. Укажите населённый пункт, улицу и дом."
        user.address = value
        if not (user.locality or "").strip():
            user.locality = _extract_locality(value)
        user.save(update_fields=["address", "locality", "last_seen_at"])
        candidates = heuristic_candidates(user)
        if candidates:
            return _begin_family_check(user, pending, candidates)
        nxt = _finish_if_complete(user, pending)
        if nxt and nxt.startswith("Анкета сохранена"):
            return nxt
        return f"Принято. {nxt}"

    if step == "family_check":
        answer = value.lower().strip().rstrip(".!")
        candidate_ids = list(payload.get("candidate_ids") or [])
        address = payload.get("address") or user.address
        if answer not in _YES and answer not in _NO:
            return "Ответьте «да» или «нет» — без данных других жителей."
        claimed = answer in _YES
        try:
            task_family_claim(
                user,
                candidate_ids=candidate_ids,
                claimed_family=claimed,
                address=address,
            )
        except Exception:
            pass
        ActivityLog.objects.create(
            user=user,
            kind=ActivityKind.PROFILE_SUBMITTED,
            title="Ответ по семейной заявке",
            detail="да" if claimed else "нет",
            meta={"candidate_ids": candidate_ids, "claimed_family": claimed},
        )
        nxt = _finish_if_complete(user, pending)
        if nxt and nxt.startswith("Анкета сохранена"):
            prefix = (
                "Спасибо. Администратор проверит семейную заявку.\n\n"
                if claimed
                else "Спасибо. Администратор при необходимости уточнит адрес.\n\n"
            )
            return prefix + nxt
        return f"Спасибо. {nxt}"

    if step == "locality":
        if len(value) < 2:
            return "Укажите населённый пункт."
        user.locality = value
        user.save(update_fields=["locality", "last_seen_at"])
        # Address may already be set; check family before finish
        if (user.address or "").strip():
            candidates = heuristic_candidates(user)
            if candidates and not payload.get("family_asked"):
                return _begin_family_check(user, pending, candidates)
        nxt = _finish_if_complete(user, pending)
        if nxt and nxt.startswith("Анкета сохранена"):
            return nxt
        return f"Спасибо. {nxt}"

    pending.pending_payload = {"step": "name"}
    pending.save(update_fields=["pending_payload", "updated_at"])
    return "Начнём сначала. Как вас зовут?"
