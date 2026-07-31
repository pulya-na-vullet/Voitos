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
from subscriptions.receipts import normalize_phone

REG_KIND = "registration"


def needs_registration(user: BotUser) -> bool:
    return user.profile_status in {
        ProfileStatus.INCOMPLETE,
        ProfileStatus.REJECTED,
    } and not user.profile_complete()


def start_registration(user: BotUser, pending: PendingAction) -> str:
    pending.pending_kind = REG_KIND
    pending.pending_payload = {"step": "name"}
    pending.save(update_fields=["pending_kind", "pending_payload", "updated_at"])
    return (
        "Добро пожаловать! Для работы сервиса заполните короткую анкету.\n\n"
        "Как вас зовут? (Имя)"
    )


def _extract_locality(address: str) -> str:
    address = (address or "").strip()
    if not address:
        return ""
    # "г. Казань, ул. ..." or "Казань, ..."
    first = address.split(",")[0].strip()
    first = re.sub(
        r"^(г\.|гор\.|город|п\.|пос\.|поселок|посёлок|с\.|село|д\.|деревня)\s*",
        "",
        first,
        flags=re.IGNORECASE,
    ).strip()
    return first or address[:80]


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
        pending.pending_payload = {"step": "phone"}
        pending.pending_kind = REG_KIND
        pending.save(update_fields=["pending_kind", "pending_payload", "updated_at"])
        return "Спасибо. Теперь отправьте номер телефона (например 89625507832)."

    if step == "phone":
        phone = normalize_phone(value)
        if len(phone) < 10:
            return "Не распознал телефон. Пришлите номер цифрами, например 89625507832."
        if len(phone) == 10:
            phone = "8" + phone
        user.phone = phone
        user.save(update_fields=["phone", "last_seen_at"])
        pending.pending_payload = {"step": "address"}
        pending.pending_kind = REG_KIND
        pending.save(update_fields=["pending_kind", "pending_payload", "updated_at"])
        return (
            "Отлично. Укажите адрес места жительства.\n"
            "Формат: населённый пункт, улица, дом "
            "(например: Казань, ул. Баумана, 1)."
        )

    if step == "address":
        if len(value) < 5:
            return "Адрес слишком короткий. Укажите населённый пункт, улицу и дом."
        user.address = value
        user.locality = _extract_locality(value)
        user.profile_status = ProfileStatus.PENDING_REVIEW
        user.profile_submitted_at = timezone.now()
        user.save(
            update_fields=[
                "address",
                "locality",
                "profile_status",
                "profile_submitted_at",
                "last_seen_at",
            ]
        )
        pending.clear_pending()
        ActivityLog.objects.create(
            user=user,
            kind=ActivityKind.PROFILE_SUBMITTED,
            title="Анкета отправлена на проверку",
            detail=f"{user.real_name}, {user.phone}, {user.address}",
        )
        return (
            "Анкета сохранена. Администратор проверит данные.\n\n"
            f"Имя: {user.real_name}\n"
            f"Телефон: {user.phone}\n"
            f"Адрес: {user.address}\n"
            f"Населённый пункт: {user.locality}\n\n"
            "Напишите «помощь», чтобы увидеть возможности бота."
        )

    pending.pending_payload = {"step": "name"}
    pending.save(update_fields=["pending_payload", "updated_at"])
    return "Начнём сначала. Как вас зовут?"
