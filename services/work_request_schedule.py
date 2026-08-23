"""Согласование окон приёма для ролей «мастер принимает на дому»."""

from __future__ import annotations

import logging
import re

from django.utils import timezone

from database.models import (
    BotUser,
    PendingAction,
    WorkRequest,
    WorkRequestStatus,
)

logger = logging.getLogger(__name__)

MASTER_SCHEDULE_PENDING = "work_request_schedule_master"
CLIENT_SCHEDULE_PENDING = "work_request_schedule_client"

_DONE = {"готово", "готов", "done", "ок", "всё", "все", "отправить", "дальше"}


def role_accepts_at_home(req: WorkRequest) -> bool:
    role = getattr(req, "role", None)
    return bool(role and getattr(role, "accepts_at_home", False))


def master_visit_address(req: WorkRequest) -> str:
    if (req.master_address or "").strip():
        return req.master_address.strip()
    contractor = req.assigned_contractor
    if not contractor:
        return ""
    user = contractor.user
    locality = (contractor.locality or user.locality or "").strip()
    street = (user.address or "").strip()
    if not street:
        return locality
    if not locality:
        return street
    # Не дублировать НП, если адрес уже начинается с него («Куюки, Куюки, ул.…»).
    street_l = street.casefold()
    loc_l = locality.casefold()
    if street_l == loc_l or street_l.startswith(loc_l + ",") or street_l.startswith(loc_l + " "):
        return street
    return f"{locality}, {street}"


def start_master_scheduling(req: WorkRequest, *, send_fn=None) -> None:
    """После accept: спросить у мастера окна (на дому или выезд к клиенту)."""
    send_fn = send_fn or _send()
    req.status = WorkRequestStatus.SCHEDULING
    contractor = req.assigned_contractor
    home = role_accepts_at_home(req)
    addr = master_visit_address(req) if home else (req.user.address or "").strip()
    if home and addr and not req.master_address:
        req.master_address = addr[:512]
    req.proposed_slots = []
    req.agreed_slot = ""
    req.save(
        update_fields=[
            "status",
            "master_address",
            "proposed_slots",
            "agreed_slot",
            "updated_at",
        ]
    )
    c_user = contractor.user
    pending, _ = PendingAction.objects.get_or_create(user=c_user)
    if home:
        step = "address" if not (req.master_address or "").strip() else "slots"
    else:
        step = "slots"
    pending.pending_kind = MASTER_SCHEDULE_PENDING
    pending.pending_payload = {
        "work_request_id": req.id,
        "step": step,
        "slots": [],
    }
    pending.save(update_fields=["pending_kind", "pending_payload", "updated_at"])

    client_addr = (req.user.address or "").strip() or "адрес клиента уточните у жителя"
    if step == "address":
        msg = (
            f"Заявка #{req.id}: вы принимаете клиентов на дому.\n"
            "Напишите адрес, по которому готовы принять клиента."
        )
    elif home:
        msg = (
            f"Заявка #{req.id}: укажите, в какое время можете принять клиента "
            f"по адресу: {req.master_address}.\n"
            "Можно несколько периодов — каждый с новой строки, например:\n"
            "15.03 10:00–12:00\n"
            "15.03 14:00–16:00\n"
            "16.03 11:00–13:00\n\n"
            "Когда перечислите все окна, напишите «готово»."
        )
    else:
        msg = (
            f"Заявка #{req.id}: согласуйте время визита к клиенту.\n"
            f"Адрес: {client_addr}\n"
            f"Клиент: {req.user}\n"
            f"Телефон: {(req.user.phone or '—')}\n\n"
            "Укажите варианты времени — каждый с новой строки, например:\n"
            "15.03 10:00–12:00\n"
            "15.03 14:00–16:00\n\n"
            "Когда перечислите все окна, напишите «готово»."
        )
    try:
        send_fn(c_user, msg)
    except Exception:
        logger.exception("start_master_scheduling WR %s", req.id)
    try:
        from api.emit import emit_app_event

        emit_app_event(
            c_user,
            ntype="work_request.schedule_master",
            title=f"Согласуйте время — заявка #{req.id}",
            body="Предложите клиенту окна визита в приложении или в чате бота.",
            entity_type="work_request",
            entity_id=req.id,
        )
    except Exception:
        logger.exception("app inbox schedule_master WR %s", req.id)


def _send():
    from services.work_request_dispatch import _default_send_fn

    return _default_send_fn()


def _parse_slot_lines(text: str) -> list[str]:
    lines = []
    for part in re.split(r"[\n;]+", text or ""):
        line = part.strip(" \t-–—")
        if not line:
            continue
        if line.lower() in _DONE:
            continue
        lines.append(line[:200])
    return lines


def handle_master_schedule_step(user: BotUser, text: str, pending: PendingAction) -> str:
    if pending.pending_kind != MASTER_SCHEDULE_PENDING:
        return "Сейчас согласование времени не ожидается."
    payload = dict(pending.pending_payload or {})
    req = (
        WorkRequest.objects.select_related(
            "user", "role", "assigned_contractor", "assigned_contractor__user"
        )
        .filter(pk=payload.get("work_request_id"))
        .first()
    )
    if not req or not req.assigned_contractor or req.assigned_contractor.user_id != user.id:
        pending.clear_pending()
        return "Заявка не найдена."
    if req.status not in {WorkRequestStatus.SCHEDULING, WorkRequestStatus.IN_PROGRESS}:
        if req.status == WorkRequestStatus.CANCELLED:
            pending.clear_pending()
            return "Заявка отменена."

    step = payload.get("step") or "slots"
    raw = (text or "").strip()
    lower = raw.lower()

    if step == "address":
        if len(raw) < 5:
            return "Укажите полный адрес приёма (улица, дом)."
        req.master_address = raw[:512]
        req.save(update_fields=["master_address", "updated_at"])
        payload["step"] = "slots"
        payload["slots"] = []
        pending.pending_payload = payload
        pending.save(update_fields=["pending_payload", "updated_at"])
        return (
            f"Адрес сохранён: {req.master_address}\n\n"
            "Теперь укажите окна приёма (по одному в строке). "
            "Когда закончите — напишите «готово»."
        )

    slots = list(payload.get("slots") or [])
    if lower in _DONE:
        if not slots:
            return "Добавьте хотя бы одно окно, затем напишите «готово»."
        return _publish_slots_to_client(req, slots, pending)

    new_slots = _parse_slot_lines(raw)
    if not new_slots:
        return (
            "Напишите период, например «завтра 10:00–12:00», "
            "или «готово», если окна уже перечислены."
        )
    slots.extend(new_slots)
    # unique preserve order
    seen = set()
    uniq = []
    for s in slots:
        key = s.lower()
        if key in seen:
            continue
        seen.add(key)
        uniq.append(s)
    payload["slots"] = uniq[:20]
    pending.pending_payload = payload
    pending.save(update_fields=["pending_payload", "updated_at"])
    listed = "\n".join(f"{i}. {s}" for i, s in enumerate(uniq, 1))
    return (
        f"Текущие окна:\n{listed}\n\n"
        "Можете добавить ещё или напишите «готово»."
    )


def _publish_slots_to_client(req: WorkRequest, slots: list[str], pending: PendingAction) -> str:
    send_fn = _send()
    req.proposed_slots = [{"label": s} for s in slots]
    req.status = WorkRequestStatus.SCHEDULING
    req.save(update_fields=["proposed_slots", "status", "updated_at"])
    pending.clear_pending()

    addr = master_visit_address(req) or "адрес уточнит мастер"
    master_name = str(req.assigned_contractor.user)
    from services.contractors import format_executor_contacts_block

    contacts = format_executor_contacts_block(req.assigned_contractor)
    lines = "\n".join(f"{i}. {s}" for i, s in enumerate(slots, 1))
    client_msg = (
        f"Мастер {master_name} готов принять вас (заявка #{req.id}).\n"
        f"Адрес: {addr}\n"
        f"{contacts}\n\n"
        f"Выберите время — напишите номер:\n{lines}"
    )
    try:
        send_fn(req.user, client_msg)
    except Exception:
        logger.exception("notify client slots WR %s", req.id)
    try:
        from api.emit import emit_app_event

        emit_app_event(
            req.user,
            ntype="work_request.slots_ready",
            title=f"Выберите время — заявка #{req.id}",
            body="Мастер предложил окна. Откройте заявку и выберите удобное.",
            entity_type="work_request",
            entity_id=req.id,
        )
    except Exception:
        logger.exception("app inbox slots_ready WR %s", req.id)

    c_pending, _ = PendingAction.objects.get_or_create(user=req.user)
    c_pending.pending_kind = CLIENT_SCHEDULE_PENDING
    c_pending.pending_payload = {"work_request_id": req.id}
    c_pending.save(update_fields=["pending_kind", "pending_payload", "updated_at"])

    return (
        "Окна отправлены клиенту. Ждём его выбор."
    )


def publish_slots_for_master(req: WorkRequest, master: BotUser, slots: list[str]) -> str:
    """Мастер в приложении публикует окна клиенту (без бота)."""
    if req.status != WorkRequestStatus.SCHEDULING:
        raise ValueError("Сейчас нельзя предложить окна по этой заявке.")
    if not req.assigned_contractor_id or req.assigned_contractor.user_id != master.id:
        raise ValueError("Вы не назначены на эту заявку.")
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in slots or []:
        for part in _parse_slot_lines(str(raw)):
            key = part.casefold()
            if key in seen:
                continue
            seen.add(key)
            cleaned.append(part)
    if not cleaned:
        raise ValueError("Укажите хотя бы одно окно времени.")
    cleaned = cleaned[:20]

    pending = PendingAction.objects.filter(user=master).first()
    if (
        pending
        and pending.pending_kind == MASTER_SCHEDULE_PENDING
        and (pending.pending_payload or {}).get("work_request_id") == req.id
    ):
        return _publish_slots_to_client(req, cleaned, pending)

    # Pending мог отсутствовать (мастер только в приложении) — создаём временный.
    pending, _ = PendingAction.objects.get_or_create(user=master)
    pending.pending_kind = MASTER_SCHEDULE_PENDING
    pending.pending_payload = {
        "work_request_id": req.id,
        "step": "slots",
        "slots": cleaned,
    }
    pending.save(update_fields=["pending_kind", "pending_payload", "updated_at"])
    return _publish_slots_to_client(req, cleaned, pending)


def handle_client_schedule_step(user: BotUser, text: str, pending: PendingAction) -> str:
    if pending.pending_kind != CLIENT_SCHEDULE_PENDING:
        return "Сейчас выбор времени не ожидается."
    payload = dict(pending.pending_payload or {})
    req = (
        WorkRequest.objects.select_related(
            "assigned_contractor", "assigned_contractor__user", "role", "user"
        )
        .filter(pk=payload.get("work_request_id"))
        .first()
    )
    if not req or req.user_id != user.id:
        pending.clear_pending()
        return "Заявка не найдена."
    slots = []
    for item in req.proposed_slots or []:
        if isinstance(item, dict):
            label = str(item.get("label") or "").strip()
        else:
            label = str(item).strip()
        if label:
            slots.append(label)
    if not slots:
        pending.clear_pending()
        return "Мастер ещё не предложил окна. Подождите сообщения."

    raw = (text or "").strip()
    chosen = None
    if re.fullmatch(r"\d{1,2}", raw):
        idx = int(raw) - 1
        if 0 <= idx < len(slots):
            chosen = slots[idx]
    if chosen is None:
        lower = raw.lower()
        for s in slots:
            if s.lower() == lower or lower in s.lower() or s.lower() in lower:
                chosen = s
                break
    if chosen is None:
        listed = "\n".join(f"{i}. {s}" for i, s in enumerate(slots, 1))
        return (
            "Не распознал окно. Напишите номер из списка:\n" + listed
        )

    return _confirm_agreed_slot(req, chosen, pending)


def _confirm_agreed_slot(
    req: WorkRequest, slot: str, pending: PendingAction | None
) -> str:
    send_fn = _send()
    now = timezone.now()
    req.agreed_slot = slot[:255]
    req.schedule_agreed_at = now
    req.status = WorkRequestStatus.IN_PROGRESS
    req.save(
        update_fields=["agreed_slot", "schedule_agreed_at", "status", "updated_at"]
    )
    if pending is not None:
        pending.clear_pending()

    addr = master_visit_address(req) or (req.user.address or "").strip() or "—"
    master = req.assigned_contractor.user if req.assigned_contractor_id else None
    client = req.user
    from services.contractors import format_executor_contacts_block

    contacts = (
        format_executor_contacts_block(req.assigned_contractor)
        if req.assigned_contractor_id
        else ""
    )
    common = (
        f"Согласовано по заявке #{req.id}.\n"
        f"Время: {slot}\n"
        f"Адрес: {addr}\n"
        f"Мастер: {master or '—'}\n"
        f"{contacts}\n"
        f"Клиент: {client}\n"
        f"Телефон клиента: {(client.phone or '—')}"
    )
    try:
        send_fn(client, common + "\n\nСтатус заявки: в работе.")
    except Exception:
        logger.exception("confirm client WR %s", req.id)
    if master is not None:
        try:
            send_fn(
                master,
                common
                + "\n\nСтатус: в работе. Когда закончите, напишите: заявка выполнена",
            )
        except Exception:
            logger.exception("confirm master WR %s", req.id)
    try:
        from api.emit import emit_app_event

        emit_app_event(
            client,
            ntype="work_request.in_progress",
            title="Заявка в работе",
            body=f"Время согласовано: {slot}",
            entity_type="work_request",
            entity_id=req.id,
        )
    except Exception:
        logger.exception("app inbox in_progress WR %s", req.id)
    return f"Отлично! Зафиксировали: {slot}. Статус — «В работе»."


def confirm_slot_for_client(req: WorkRequest, *, slot: str = "") -> str:
    """Клиент в приложении выбирает окно или подтверждает договорённость."""
    if req.status != WorkRequestStatus.SCHEDULING:
        raise ValueError("Сейчас нельзя подтвердить время по этой заявке.")
    slots: list[str] = []
    for item in req.proposed_slots or []:
        if isinstance(item, dict):
            label = str(item.get("label") or "").strip()
        else:
            label = str(item).strip()
        if label:
            slots.append(label)

    chosen = (slot or "").strip()
    if chosen and slots and chosen not in slots:
        # номер из списка
        if chosen.isdigit():
            idx = int(chosen) - 1
            if 0 <= idx < len(slots):
                chosen = slots[idx]
            else:
                raise ValueError("Некорректный номер окна.")
        else:
            raise ValueError("Выберите одно из предложенных окон.")
    if not chosen:
        if slots:
            raise ValueError("Выберите одно из предложенных окон.")
        raise ValueError(
            "Мастер ещё не предложил окна. Дождитесь вариантов или "
            "попросите мастера отправить их в приложении."
        )

    pending = PendingAction.objects.filter(user=req.user).first()
    if not (
        pending
        and pending.pending_kind == CLIENT_SCHEDULE_PENDING
        and (pending.pending_payload or {}).get("work_request_id") == req.id
    ):
        pending = None
    return _confirm_agreed_slot(req, chosen, pending)
