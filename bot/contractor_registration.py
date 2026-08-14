"""Регистрация исполнителей (роли из каталога ExecutorRole) в MAX-боте."""
from __future__ import annotations

from django.core.files.base import ContentFile
from django.utils import timezone

from database.models import (
    ActivityKind,
    ActivityLog,
    AdminTaskKind,
    BotUser,
    ContractorProfile,
    ContractorStatus,
    ExecutorRole,
    PendingAction,
)
from panel.admin_tasks import upsert_task
from services.executor_roles import (
    active_roles,
    format_roles_list,
    match_role_from_text,
    role_by_code,
)
from subscriptions.receipts import normalize_phone

CONTRACTOR_REG_KIND = "contractor_registration"

_YES = {"да", "yes", "y", "+", "ага", "угу"}
_SKIP = {"нет", "no", "n", "-", "нету", "тот же", "тотже", "как для связи", "как связь"}


def start_contractor_registration(
    user: BotUser,
    pending: PendingAction,
    *,
    equipment_type: str | None = None,
    role: ExecutorRole | None = None,
) -> str:
    pending.pending_kind = CONTRACTOR_REG_KIND
    if role is None and equipment_type:
        role = role_by_code(equipment_type)
    if role is not None:
        return _after_role_chosen(user, pending, role)

    roles = list(active_roles())
    if not roles:
        pending.clear_pending()
        return (
            "Пока нет ролей мастеров в каталоге.\n"
            "Попробуйте позже."
        )

    pending.pending_payload = {"step": "role"}
    pending.save(update_fields=["pending_kind", "pending_payload", "updated_at"])
    existing = list(
        ContractorProfile.objects.filter(user=user)
        .select_related("role")
        .order_by("id")
    )
    have = ""
    if existing:
        names = ", ".join(p.role_label for p in existing)
        have = f"Уже есть: {names}. Можно добавить ещё.\n"
    return (
        "Регистрация мастера.\n"
        f"{have}"
        "Кем работаете? Напишите номер:\n\n"
        + format_roles_list(roles)
    )


def _after_role_chosen(user: BotUser, pending: PendingAction, role: ExecutorRole) -> str:
    payload = {"step": "label", "role_id": role.id, "equipment_type": role.code}
    pending.pending_kind = CONTRACTOR_REG_KIND
    pending.pending_payload = payload
    pending.save(update_fields=["pending_kind", "pending_payload", "updated_at"])
    if role.is_equipment:
        return (
            f"Роль: {role.name}.\n"
            "Модель или описание техники (например: МТЗ-82)."
        )
    doc = ""
    if role.requires_qualification_docs:
        doc = "\nПозже попросим фото документа."
    return (
        f"Роль: {role.name}.\n"
        f"Кратко опишите опыт (или «нет»).{doc}"
    )


def handle_contractor_registration_step(
    user: BotUser,
    text: str,
    pending: PendingAction,
) -> str:
    payload = dict(pending.pending_payload or {})
    step = payload.get("step") or "role"
    raw = (text or "").strip()
    role = None
    if payload.get("role_id"):
        role = ExecutorRole.objects.filter(pk=payload["role_id"]).first()

    if step == "role":
        roles = list(active_roles())
        if not roles:
            pending.clear_pending()
            return (
                "Сейчас нет доступных ролей.\n"
                "Обратитесь к администратору или попробуйте позже."
            )
        role = match_role_from_text(raw, roles)
        if not role:
            return (
                "Не понял номер.\n"
                "Напишите цифру из списка:\n\n"
                + format_roles_list(roles)
                + "\n\nНапример: 1"
            )
        return _after_role_chosen(user, pending, role)

    if not role:
        pending.clear_pending()
        return "Ошибка роли — начните снова: «стать исполнителем»."

    if step == "label":
        if role.is_equipment and len(raw) < 2:
            return "Напишите модель или краткое описание техники."
        if raw.lower() in _SKIP | {"нет", "no", "n", "-", "нету"}:
            payload["equipment_label"] = ""
        else:
            payload["equipment_label"] = raw[:255]
        if role.is_equipment:
            payload["step"] = "plate"
            pending.pending_payload = payload
            pending.save(update_fields=["pending_payload", "updated_at"])
            return "Укажите госномер (или «нет», если пока без номера)."
        return _ask_phone_or_locality(user, payload, pending)

    if step == "plate":
        # «да» ≠ отказ: только явный отказ / «без номера»
        if raw.lower() in _SKIP | {"нет", "no", "n", "-", "нету", "без номера"}:
            payload["plate_number"] = ""
        elif raw.lower() in _YES:
            return "Укажите госномер цифрами и буквами (или «нет», если без номера)."
        else:
            payload["plate_number"] = raw[:32]
        return _ask_phone_or_locality(user, payload, pending)

    if step == "phone":
        phone = normalize_phone(raw)
        if len(phone) < 10:
            return "Нужен корректный телефон, например 89625507832."
        payload["phone"] = phone
        if not (user.phone or "").strip():
            user.phone = phone
            user.save(update_fields=["phone", "last_seen_at"])
        return _ask_locality(user, payload, pending)

    if step == "locality":
        if len(raw) < 2:
            return "Укажите населённый пункт."
        payload["locality"] = raw[:255]
        payload["step"] = "bank"
        pending.pending_payload = payload
        pending.save(update_fields=["pending_payload", "updated_at"])
        return (
            "Укажите банк, на который переводить оплату за работу "
            "(например: Сбер, Тинькофф, Альфа)."
        )

    if step == "bank":
        if len(raw) < 2:
            return "Напишите название банка для перевода."
        payload["bank_name"] = raw[:255]
        payload["step"] = "payout_phone"
        pending.pending_payload = payload
        pending.save(update_fields=["pending_payload", "updated_at"])
        contact = payload.get("phone") or user.phone or ""
        return (
            "Укажите номер телефона для перевода денег "
            f"(или «тот же», если совпадает с {contact or 'телефоном для связи'})."
        )

    if step == "payout_phone":
        low = raw.lower()
        if low in _SKIP or low in _YES:
            payload["payout_phone"] = payload.get("phone") or user.phone or ""
        else:
            phone = normalize_phone(raw)
            if len(phone) < 10:
                return (
                    "Нужен телефон для перевода или напишите «тот же», "
                    "если совпадает с телефоном для связи."
                )
            payload["payout_phone"] = phone
        if role.requires_qualification_docs:
            payload["step"] = "qual_doc"
            pending.pending_payload = payload
            pending.save(update_fields=["pending_payload", "updated_at"])
            return (
                "Для этой роли нужны подтверждающие документы о квалификации.\n"
                "Пришлите фото документа (диплом, удостоверение, сертификат)."
            )
        return _finish(user, pending, payload, role)

    if step == "qual_doc":
        return (
            "Жду фото документа о квалификации. "
            "Пришлите изображение в чат."
        )

    return start_contractor_registration(user, pending)


def handle_contractor_qual_doc_photo(
    user: BotUser,
    pending: PendingAction,
    *,
    image_bytes: bytes,
    filename: str = "doc.jpg",
) -> str:
    payload = dict(pending.pending_payload or {})
    if payload.get("step") != "qual_doc":
        return handle_contractor_registration_step(user, "", pending)
    role = ExecutorRole.objects.filter(pk=payload.get("role_id")).first()
    if not role:
        pending.clear_pending()
        return "Ошибка — начните регистрацию снова."
    payload["_qual_bytes"] = True  # marker; actual bytes saved in finish via temp
    # Store file immediately on a draft profile field after finish — keep in pending as b64? 
    # Better: save to profile now via _finish with ContentFile
    import base64

    payload["qual_b64"] = base64.b64encode(image_bytes).decode("ascii")
    payload["qual_filename"] = (filename or "doc.jpg")[:120]
    pending.pending_payload = {k: v for k, v in payload.items() if k != "_qual_bytes"}
    # remove huge from being double - actually we need b64 in payload which can be large
    # Alternative: write temp file. For MVP use ContentFile in _finish from b64.
    pending.pending_payload = payload
    pending.save(update_fields=["pending_payload", "updated_at"])
    return _finish(user, pending, payload, role)


def _ask_phone_or_locality(user: BotUser, payload: dict, pending: PendingAction) -> str:
    if (user.phone or "").strip():
        payload["phone"] = user.phone.strip()
        return _ask_locality(user, payload, pending)
    payload["step"] = "phone"
    pending.pending_payload = payload
    pending.save(update_fields=["pending_payload", "updated_at"])
    return "Отправьте телефон для связи (например 89625507832)."


def _ask_locality(user: BotUser, payload: dict, pending: PendingAction) -> str:
    payload["step"] = "locality"
    pending.pending_payload = payload
    pending.save(update_fields=["pending_payload", "updated_at"])
    hint = (user.locality or "").strip()
    extra = f"\nСейчас в анкете: {hint}" if hint else ""
    return "Укажите населённый пункт, где работаете." + extra


def _finish(
    user: BotUser,
    pending: PendingAction,
    payload: dict,
    role: ExecutorRole,
) -> str:
    contact = (payload.get("phone") or user.phone or "")[:32]
    payout = (payload.get("payout_phone") or contact)[:32]
    # Одна роль = одна запись; повторная регистрация той же роли обновляет анкету,
    # другие роли того же пользователя сохраняются.
    profile, _created = ContractorProfile.objects.update_or_create(
        user=user,
        equipment_type=role.code,
        defaults={
            "role": role,
            "equipment_label": (payload.get("equipment_label") or "")[:255],
            "plate_number": (payload.get("plate_number") or "")[:32],
            "phone": contact,
            "payout_phone": payout,
            "bank_name": (payload.get("bank_name") or "")[:255],
            "locality": (payload.get("locality") or user.locality or "")[:255],
            "status": ContractorStatus.PENDING_REVIEW,
            "submitted_at": timezone.now(),
            "verified_at": None,
        },
    )
    qual_b64 = payload.get("qual_b64")
    if qual_b64:
        import base64

        raw = base64.b64decode(qual_b64)
        fname = payload.get("qual_filename") or "doc.jpg"
        profile.qualification_doc.save(fname, ContentFile(raw), save=True)

    if not (user.locality or "").strip() and profile.locality:
        user.locality = profile.locality
        user.save(update_fields=["locality", "last_seen_at"])

    pending.clear_pending()
    ActivityLog.objects.create(
        user=user,
        kind=ActivityKind.CONTRACTOR_REGISTER,
        title="Анкета исполнителя отправлена",
        detail=f"{role.name}: {profile.equipment_label}",
        meta={"contractor_id": profile.id, "role": role.code},
    )
    try:
        upsert_task(
            kind=AdminTaskKind.CONTRACTOR_REVIEW,
            title=f"Проверить исполнителя: {user}",
            description=(
                f"{role.name}\n"
                f"{profile.equipment_label}\n"
                f"Госномер: {profile.plate_number or '—'}\n"
                f"Связь: {profile.phone or '—'}\n"
                f"Перевод: {profile.payout_phone or '—'} / {profile.bank_name or '—'}\n"
                f"НП: {profile.locality or '—'}\n"
                f"Документ: {'есть' if profile.qualification_doc else 'нет'}"
            ),
            user=user,
            action_url="/panel/contractors/",
            source_model="ContractorProfile",
            source_id=profile.id,
            priority=30,
        )
    except Exception:
        pass
    doc_line = ""
    if role.requires_qualification_docs:
        doc_line = f"\nДокумент: {'получен' if profile.qualification_doc else 'не приложен'}"
    all_roles = list(
        ContractorProfile.objects.filter(user=user).select_related("role").order_by("id")
    )
    roles_line = ""
    if len(all_roles) > 1:
        roles_line = "\nВсе ваши роли: " + ", ".join(p.role_label for p in all_roles)
    return (
        "Анкета отправлена на проверку.\n"
        f"Роль: {role.name}\n"
        f"Описание: {profile.equipment_label or '—'}\n"
        f"Телефон для перевода: {profile.payout_phone or profile.phone or '—'}"
        f"{doc_line}{roles_line}\n"
        "После проверки начнёте получать заказы."
    )
