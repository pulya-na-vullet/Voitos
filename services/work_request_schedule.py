"""Согласование окон приёма для ролей «мастер принимает на дому»."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta

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
SERVICE_DONE_PENDING = "work_request_service_done"
SCHEDULED_KIND_SERVICE_DONE = "work_request_service_done_ask"

_DONE = {"готово", "готов", "done", "ок", "всё", "все", "отправить", "дальше"}
_YES = {"да", "yes", "y", "+", "ага", "угу", "ок", "верно", "1", "оказана", "да оказана"}
_NO = {"нет", "no", "n", "-", "не", "2", "не оказана", "не было"}

# Fallback, если конец окна не распознан
_DEFAULT_SLOT_DURATION = timedelta(hours=2)


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


def parse_slot_end_at(label: str, *, base: datetime | None = None) -> datetime | None:
    """
    Извлечь конец окна из подписи вида «15.03 10:00–12:00» / «завтра 14:00-16:00».
    Возвращает aware datetime в текущей TZ Django.
    """
    raw = (label or "").strip().lower().replace("ё", "е")
    if not raw:
        return None
    base_local = timezone.localtime(base or timezone.now())
    day = base_local.date()

    if "послезавтра" in raw:
        day = day + timedelta(days=2)
    elif "завтра" in raw:
        day = day + timedelta(days=1)
    elif "сегодня" in raw:
        pass
    else:
        m_date = re.search(r"(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?", raw)
        if m_date:
            dd, mm = int(m_date.group(1)), int(m_date.group(2))
            yy = m_date.group(3)
            if yy:
                year = int(yy)
                if year < 100:
                    year += 2000
            else:
                year = day.year
                # Если дата уже прошла в этом году относительно base — следующий год
                try:
                    candidate = day.replace(year=year, month=mm, day=dd)
                except ValueError:
                    candidate = None
                if candidate is not None and candidate < day:
                    year += 1
            try:
                day = day.replace(year=year, month=mm, day=dd)
            except ValueError:
                pass

    # Конец диапазона: 10:00-12:00 / 10.00–12.00 / 10:00 — 12:00
    times = re.findall(r"(\d{1,2})[:.\-](\d{2})", raw)
    if len(times) >= 2:
        end_h, end_m = int(times[-1][0]), int(times[-1][1])
    elif len(times) == 1:
        # Только одно время — считаем концом (+0), длительность добавит caller
        end_h, end_m = int(times[0][0]), int(times[0][1])
    else:
        return None
    if not (0 <= end_h <= 23 and 0 <= end_m <= 59):
        return None
    naive = datetime(day.year, day.month, day.day, end_h, end_m)
    return timezone.make_aware(naive, timezone.get_current_timezone())


def resolve_slot_end_at(label: str, *, agreed_at=None) -> datetime:
    """Конец окна или fallback: agree + 2 часа."""
    agreed_at = agreed_at or timezone.now()
    parsed = parse_slot_end_at(label, base=agreed_at)
    if parsed is None:
        return agreed_at + _DEFAULT_SLOT_DURATION
    # Если в подписи одно время без диапазона — +2ч от него
    times = re.findall(r"(\d{1,2})[:.\-](\d{2})", (label or "").lower())
    if len(times) == 1:
        parsed = parsed + _DEFAULT_SLOT_DURATION
    # Не планировать в прошлом относительно agree
    if parsed < agreed_at:
        parsed = agreed_at + _DEFAULT_SLOT_DURATION
    return parsed


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
    req.agreed_slot_end_at = None
    req.save(
        update_fields=[
            "status",
            "master_address",
            "proposed_slots",
            "agreed_slot",
            "agreed_slot_end_at",
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
            "Напишите адрес приёма."
        )
    else:
        msg = (
            f"Заявка #{req.id}: укажите окна приёма по адресу: {req.master_address}.\n"
            "Каждое окно с новой строки, с началом и концом, например:\n"
            "15.03 10:00–12:00\n"
            "завтра 14:00–16:00\n\n"
            "Когда перечислите все — напишите «готово»."
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
            f"Адрес: {req.master_address}\n\n"
            "Окна приёма (по одному в строке), затем «готово».\n"
            "Пример: завтра 10:00–12:00"
        )

    slots = list(payload.get("slots") or [])
    if lower in _DONE:
        if not slots:
            return "Добавьте хотя бы одно окно, затем «готово»."
        return _publish_slots_to_client(req, slots, pending)

    new_slots = _parse_slot_lines(raw)
    if not new_slots:
        return (
            "Напишите период, например «завтра 10:00–12:00», "
            "или «готово»."
        )
    slots.extend(new_slots)
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
    return f"Окна:\n{listed}\n\nДобавьте ещё или напишите «готово»."


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

    return "Окна отправлены клиенту. Ждём его выбор."


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
        return "Не распознал окно. Напишите номер:\n" + listed

    return _confirm_agreed_slot(req, chosen, pending)


def service_done_ask_text(req: WorkRequest) -> str:
    slot = (req.agreed_slot or "визит").strip()
    return (
        f"Заявка #{req.id} ({req.role.name}): время «{slot}» закончилось.\n\n"
        "Услуга оказана?\n"
        "1 / да\n"
        "2 / нет"
    )


def schedule_service_done_ask(req: WorkRequest, *, end_at=None) -> None:
    """Поставить опрос клиента на конец окна приёма."""
    from services.work_request_completion import cancel_scheduled_for_request, schedule_message

    cancel_scheduled_for_request(req, kind=SCHEDULED_KIND_SERVICE_DONE)
    send_at = end_at or req.agreed_slot_end_at
    if not send_at:
        return
    # Если конец уже прошёл — спросить через минуту
    now = timezone.now()
    if send_at <= now:
        send_at = now + timedelta(minutes=1)
    schedule_message(
        user=req.user,
        kind=SCHEDULED_KIND_SERVICE_DONE,
        text=service_done_ask_text(req),
        send_at=send_at,
        meta={"work_request_id": req.id},
    )


def on_service_done_ask_sent(msg) -> None:
    """После отправки опроса — ждём ответ клиента."""
    req_id = (msg.meta or {}).get("work_request_id")
    if not req_id:
        return
    req = WorkRequest.objects.filter(pk=req_id).first()
    if not req:
        return
    if req.status != WorkRequestStatus.IN_PROGRESS:
        return
    if req.executor_reported_at:
        return
    req.service_done_asked_at = timezone.now()
    req.save(update_fields=["service_done_asked_at", "updated_at"])
    pending, _ = PendingAction.objects.get_or_create(user=msg.user)
    pending.pending_kind = SERVICE_DONE_PENDING
    pending.pending_payload = {"work_request_id": req.id}
    pending.save(update_fields=["pending_kind", "pending_payload", "updated_at"])


def handle_service_done_step(user: BotUser, text: str, pending: PendingAction) -> str:
    """Клиент отвечает: оказана ли услуга после окна приёма."""
    if pending.pending_kind != SERVICE_DONE_PENDING:
        return "Сейчас подтверждение услуги не ожидается."
    payload = dict(pending.pending_payload or {})
    req = (
        WorkRequest.objects.select_related(
            "role", "assigned_contractor", "assigned_contractor__user", "user"
        )
        .filter(pk=payload.get("work_request_id"), user=user)
        .first()
    )
    if not req:
        pending.clear_pending()
        return "Заявка не найдена."
    if req.status != WorkRequestStatus.IN_PROGRESS:
        pending.clear_pending()
        return f"Заявка #{req.id} уже не в работе."

    raw = (text or "").strip().lower().replace("ё", "е")
    if raw in _YES or raw.startswith("да"):
        return _start_finalization_after_service(req, pending)
    if raw in _NO or raw.startswith("нет"):
        pending.clear_pending()
        # Мастер может всё равно завершить вручную
        try:
            send_fn = _send()
            if req.assigned_contractor:
                send_fn(
                    req.assigned_contractor.user,
                    f"Клиент по заявке #{req.id} ответил, что услуга не оказана.\n"
                    "Если работа всё же была — напишите «заявка выполнена».",
                )
        except Exception:
            logger.exception("notify master service not done WR %s", req.id)
        return (
            "Понял. Если услуга всё же была — свяжитесь с мастером "
            "или администратором.\n"
            "Мастер может завершить заявку командой «заявка выполнена»."
        )
    return "Ответьте: 1 / да  или  2 / нет."


def _start_finalization_after_service(req: WorkRequest, client_pending: PendingAction) -> str:
    """Да → сразу спрашиваем мастера про оплату (дальше через 20 мин — клиент)."""
    from services.work_request_completion import start_completion

    now = timezone.now()
    req.service_provided_at = now
    req.save(update_fields=["service_provided_at", "updated_at"])
    client_pending.clear_pending()

    contractor = req.assigned_contractor
    if not contractor:
        return "Мастер не назначен — обратитесь к администратору."

    master = contractor.user
    m_pending, _ = PendingAction.objects.get_or_create(user=master)
    prompt = start_completion(master, m_pending, req=req)
    try:
        _send()(
            master,
            f"Клиент подтвердил, что услуга по заявке #{req.id} оказана.\n\n{prompt}",
        )
    except Exception:
        logger.exception("notify master start completion WR %s", req.id)
    return (
        "Спасибо! Мастер сейчас укажет оплату.\n"
        "Через 20 минут после его отчёта спросим у вас сумму."
    )


def _confirm_agreed_slot(req: WorkRequest, slot: str, pending: PendingAction) -> str:
    send_fn = _send()
    now = timezone.now()
    end_at = resolve_slot_end_at(slot, agreed_at=now)
    req.agreed_slot = slot[:255]
    req.schedule_agreed_at = now
    req.agreed_slot_end_at = end_at
    req.status = WorkRequestStatus.IN_PROGRESS
    req.save(
        update_fields=[
            "agreed_slot",
            "schedule_agreed_at",
            "agreed_slot_end_at",
            "status",
            "updated_at",
        ]
    )
    pending.clear_pending()
    schedule_service_done_ask(req, end_at=end_at)

    addr = master_visit_address(req) or "—"
    master = req.assigned_contractor.user
    client = req.user
    from services.contractors import format_executor_contacts_block

    contacts = format_executor_contacts_block(req.assigned_contractor)
    end_s = timezone.localtime(end_at).strftime("%d.%m %H:%M")
    common = (
        f"Согласовано по заявке #{req.id}.\n"
        f"Время: {slot}\n"
        f"Адрес: {addr}\n"
        f"Мастер: {master}\n"
        f"{contacts}\n"
        f"Клиент: {client}"
    )
    try:
        send_fn(
            client,
            common
            + f"\n\nЖдём вас в указанное время.\n"
            f"После {end_s} спросим, оказана ли услуга.",
        )
    except Exception:
        logger.exception("confirm client WR %s", req.id)
    try:
        send_fn(
            master,
            common
            + "\n\nКогда закончите — напишите: заявка выполнена\n"
            f"(или дождитесь ответа клиента после {end_s}).",
        )
    except Exception:
        logger.exception("confirm master WR %s", req.id)
    return f"Зафиксировали визит: {slot}. После окончания окна спросим клиента."
