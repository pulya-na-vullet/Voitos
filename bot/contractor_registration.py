"""Регистрация исполнителей (трактор-погрузчик / камаз) в MAX-боте."""
from __future__ import annotations

from django.utils import timezone

from database.models import (
    ActivityKind,
    ActivityLog,
    AdminTaskKind,
    BotUser,
    ContractorProfile,
    ContractorStatus,
    EquipmentType,
    PendingAction,
)
from panel.admin_tasks import upsert_task
from subscriptions.receipts import normalize_phone

CONTRACTOR_REG_KIND = "contractor_registration"

_YES = {"да", "yes", "y", "+", "ага", "угу"}

_TYPE_PROMPTS = (
    "Выберите тип техники:\n"
    "1 — трактор-погрузчик (чистка снега)\n"
    "2 — камаз / грузовой (вывоз снега)\n"
    "Напишите номер или название."
)


def start_contractor_registration(
    user: BotUser,
    pending: PendingAction,
    *,
    equipment_type: str | None = None,
) -> str:
    pending.pending_kind = CONTRACTOR_REG_KIND
    if equipment_type in EquipmentType.values:
        pending.pending_payload = {"step": "label", "equipment_type": equipment_type}
        pending.save(update_fields=["pending_kind", "pending_payload", "updated_at"])
        label = dict(EquipmentType.choices)[equipment_type]
        return (
            f"Регистрация исполнителя: {label}.\n"
            "Укажите модель / описание техники "
            "(например: МТЗ-82 погрузчик или Камаз 55111)."
        )
    pending.pending_payload = {"step": "type"}
    pending.save(update_fields=["pending_kind", "pending_payload", "updated_at"])
    return (
        "Регистрация владельца техники для заказов "
        "(чистка снега / вывоз / дорожные работы).\n\n"
        + _TYPE_PROMPTS
    )


def _parse_type(text: str) -> str | None:
    low = (text or "").strip().lower()
    if low in {"1", "трактор", "трактор-погрузчик", "погрузчик", "тракторист"}:
        return EquipmentType.TRACTOR
    if low in {"2", "камаз", "грузовой", "грузовик", "водитель", "самосвал"}:
        return EquipmentType.TRUCK
    if "трактор" in low or "погруз" in low:
        return EquipmentType.TRACTOR
    if "камаз" in low or "груз" in low:
        return EquipmentType.TRUCK
    return None


def handle_contractor_registration_step(
    user: BotUser,
    text: str,
    pending: PendingAction,
) -> str:
    payload = dict(pending.pending_payload or {})
    step = payload.get("step") or "type"
    raw = (text or "").strip()

    if step == "type":
        eq = _parse_type(raw)
        if not eq:
            return "Не понял тип.\n\n" + _TYPE_PROMPTS
        payload["equipment_type"] = eq
        payload["step"] = "label"
        pending.pending_payload = payload
        pending.save(update_fields=["pending_payload", "updated_at"])
        return "Укажите модель / описание техники."

    if step == "label":
        if len(raw) < 2:
            return "Напишите модель или краткое описание техники."
        payload["equipment_label"] = raw[:255]
        payload["step"] = "plate"
        pending.pending_payload = payload
        pending.save(update_fields=["pending_payload", "updated_at"])
        return "Укажите госномер (или «нет», если пока без номера)."

    if step == "plate":
        if raw.lower() in _YES | {"нет", "no", "n", "-", "нету", "без номера"}:
            payload["plate_number"] = ""
        else:
            payload["plate_number"] = raw[:32]
        # phone
        if (user.phone or "").strip():
            payload["phone"] = user.phone.strip()
            payload["step"] = "locality"
            pending.pending_payload = payload
            pending.save(update_fields=["pending_payload", "updated_at"])
            hint = (user.locality or "").strip()
            extra = f"\nСейчас в анкете: {hint}" if hint else ""
            return "Укажите населённый пункт, где работаете." + extra
        payload["step"] = "phone"
        pending.pending_payload = payload
        pending.save(update_fields=["pending_payload", "updated_at"])
        return "Отправьте телефон для связи (например 89625507832)."

    if step == "phone":
        phone = normalize_phone(raw)
        if len(phone) < 10:
            return "Нужен корректный телефон, например 89625507832."
        payload["phone"] = phone
        if not (user.phone or "").strip():
            user.phone = phone
            user.save(update_fields=["phone", "last_seen_at"])
        payload["step"] = "locality"
        pending.pending_payload = payload
        pending.save(update_fields=["pending_payload", "updated_at"])
        return "Укажите населённый пункт, где работаете."

    if step == "locality":
        if len(raw) < 2:
            return "Укажите населённый пункт."
        payload["locality"] = raw[:255]
        return _finish(user, pending, payload)

    return start_contractor_registration(user, pending)


def _finish(user: BotUser, pending: PendingAction, payload: dict) -> str:
    eq = payload.get("equipment_type")
    if eq not in EquipmentType.values:
        pending.clear_pending()
        return "Ошибка типа техники — начните снова: «регистрация техники»."

    profile, _created = ContractorProfile.objects.update_or_create(
        user=user,
        defaults={
            "equipment_type": eq,
            "equipment_label": (payload.get("equipment_label") or "")[:255],
            "plate_number": (payload.get("plate_number") or "")[:32],
            "phone": (payload.get("phone") or user.phone or "")[:32],
            "locality": (payload.get("locality") or user.locality or "")[:255],
            "status": ContractorStatus.PENDING_REVIEW,
            "submitted_at": timezone.now(),
            "verified_at": None,
        },
    )
    if not (user.locality or "").strip() and profile.locality:
        user.locality = profile.locality
        user.save(update_fields=["locality", "last_seen_at"])

    pending.clear_pending()
    ActivityLog.objects.create(
        user=user,
        kind=ActivityKind.CONTRACTOR_REGISTER,
        title="Анкета исполнителя отправлена",
        detail=f"{profile.get_equipment_type_display()}: {profile.equipment_label}",
        meta={"contractor_id": profile.id, "equipment_type": eq},
    )
    try:
        upsert_task(
            kind=AdminTaskKind.CONTRACTOR_REVIEW,
            title=f"Проверить исполнителя: {user}",
            description=(
                f"{profile.get_equipment_type_display()}\n"
                f"{profile.equipment_label}\n"
                f"Госномер: {profile.plate_number or '—'}\n"
                f"Тел: {profile.phone or '—'}\n"
                f"НП: {profile.locality or '—'}"
            ),
            user=user,
            action_url="/panel/contractors/",
            source_model="ContractorProfile",
            source_id=profile.id,
            priority=30,
        )
    except Exception:
        pass
    return (
        "Анкета исполнителя отправлена администратору.\n"
        f"Тип: {profile.get_equipment_type_display()}\n"
        f"Техника: {profile.equipment_label or '—'}\n"
        "После проверки вы сможете получать заказы."
    )
