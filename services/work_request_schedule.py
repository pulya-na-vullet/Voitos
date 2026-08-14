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
    parts = []
    if (contractor.locality or "").strip():
        parts.append(contractor.locality.strip())
    elif (user.locality or "").strip():
        parts.append(user.locality.strip())
    if (user.address or "").strip():
        parts.append(user.address.strip())
    return ", ".join(parts)


def start_master_scheduling(req: WorkRequest, *, send_fn=None) -> None:
    """После accept: спросить у мастера окна и адрес приёма."""
    send_fn = send_fn or _send()
    req.status = WorkRequestStatus.SCHEDULING
    contractor = req.assigned_contractor
    addr = master_visit_address(req)
    if addr and not req.master_address:
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
    step = "address" if not (req.master_address or "").strip() else "slots"
    pending.pending_kind = MASTER_SCHEDULE_PENDING
    pending.pending_payload = {
        "work_request_id": req.id,
        "step": step,
        "slots": [],
    }
    pending.save(update_fields=["pending_kind", "pending_payload", "updated_at"])

    if step == "address":
        msg = (
            f"Заявка #{req.id}: вы принимаете клиентов на дому.\n"
            "Напишите адрес, по которому готовы принять клиента."
        )
    else:
        msg = (
            f"Заявка #{req.id}: укажите, в какое время можете принять клиента "
            f"по адресу: {req.master_address}.\n"
            "Можно несколько периодов — каждый с новой строки, например:\n"
            "15.03 10:00–12:00\n"
            "15.03 14:00–16:00\n"
            "16.03 11:00–13:00\n\n"
            "Когда перечислите все окна, напишите «готово»."
        )
    try:
        send_fn(c_user, msg)
    except Exception:
        logger.exception("start_master_scheduling WR %s", req.id)


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

    c_pending, _ = PendingAction.objects.get_or_create(user=req.user)
    c_pending.pending_kind = CLIENT_SCHEDULE_PENDING
    c_pending.pending_payload = {"work_request_id": req.id}
    c_pending.save(update_fields=["pending_kind", "pending_payload", "updated_at"])

    return (
        "Окна отправлены клиенту. Ждём его выбор."
    )


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


def _confirm_agreed_slot(req: WorkRequest, slot: str, pending: PendingAction) -> str:
    send_fn = _send()
    now = timezone.now()
    req.agreed_slot = slot[:255]
    req.schedule_agreed_at = now
    req.status = WorkRequestStatus.IN_PROGRESS
    req.save(
        update_fields=["agreed_slot", "schedule_agreed_at", "status", "updated_at"]
    )
    pending.clear_pending()

    addr = master_visit_address(req) or "—"
    master = req.assigned_contractor.user
    client = req.user
    from services.contractors import format_executor_contacts_block

    contacts = format_executor_contacts_block(req.assigned_contractor)
    common = (
        f"Согласовано по заявке #{req.id}.\n"
        f"Время: {slot}\n"
        f"Адрес: {addr}\n"
        f"Мастер: {master}\n"
        f"{contacts}\n"
        f"Клиент: {client}"
    )
    try:
        send_fn(client, common + "\n\nЖдём вас в указанное время.")
    except Exception:
        logger.exception("confirm client WR %s", req.id)
    try:
        send_fn(
            master,
            common
            + "\n\nКогда закончите работу, напишите: заявка выполнена",
        )
    except Exception:
        logger.exception("confirm master WR %s", req.id)
    return f"Отлично! Зафиксировали визит: {slot}. Мастеру отправлено подтверждение."
