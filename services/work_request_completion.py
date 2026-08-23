"""Завершение заявки: отчёт исполнителя → опрос клиента через 20 мин → комиссия 10%."""

from __future__ import annotations

import base64
import logging
import re
from decimal import Decimal, InvalidOperation
from datetime import timedelta

from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone

from database.models import (
    ActivityKind,
    ActivityLog,
    AdminTaskKind,
    AppSettings,
    BotUser,
    ContractorProfile,
    PendingAction,
    ScheduledBotMessage,
    WorkRequest,
    WorkRequestCommissionStatus,
    WorkRequestPayMethod,
    WorkRequestStatus,
)
from panel.admin_tasks import close_task_for_source, upsert_task

logger = logging.getLogger(__name__)

CLIENT_CONFIRM_DELAY_MINUTES = 20
COMMISSION_RATE = Decimal("0.10")

COMPLETE_PENDING = "work_request_complete"
CLIENT_CONFIRM_PENDING = "work_request_client_confirm"
COMMISSION_PENDING = "work_request_commission"
SCHEDULED_KIND_CLIENT_CONFIRM = "work_request_client_confirm"

_YES = {"да", "yes", "y", "+", "ага", "угу", "ок", "верно", "подтверждаю", "1"}
_NO = {"нет", "no", "n", "-", "неверно", "не правильно", "неправильно", "2"}
_TRANSFER = {
    "перевод",
    "переводом",
    "безнал",
    "безналичные",
    "карта",
    "на карту",
    "чек",
    "1",
}
_CASH = {"наличные", "наличка", "наличными", "кэш", "cash", "2"}


def parse_money(text: str) -> Decimal | None:
    raw = (text or "").strip().lower().replace(" ", "").replace(",", ".")
    raw = re.sub(r"[^\d.]", "", raw)
    if not raw:
        return None
    try:
        val = Decimal(raw)
    except InvalidOperation:
        return None
    if val <= 0 or val > Decimal("10000000"):
        return None
    return val.quantize(Decimal("0.01"))


def commission_for_amount(amount: Decimal) -> Decimal:
    return (amount * COMMISSION_RATE).quantize(Decimal("0.01"))


def executor_net_earned(confirmed: Decimal, commission: Decimal | None = None) -> Decimal:
    """Чистый заработок исполнителя: сумма клиента минус комиссия 10%."""
    confirmed = Decimal(confirmed or 0).quantize(Decimal("0.01"))
    if commission is None:
        commission = commission_for_amount(confirmed)
    else:
        commission = Decimal(commission or 0).quantize(Decimal("0.01"))
    net = confirmed - commission
    if net < 0:
        return Decimal("0.00")
    return net.quantize(Decimal("0.01"))


def platform_payee_lines() -> str:
    cfg = AppSettings.load()
    phone = (cfg.payment_phone or cfg.service_payee_phone or "").strip()
    name = (cfg.payment_name or cfg.service_payee_name or "").strip()
    status = (cfg.service_payee_status or "Самозанятый").strip()
    return (
        f"Получатель: {name or '—'}\n"
        f"Телефон: {phone or '—'}\n"
        f"Статус: {status}"
    )


def active_job_for_contractor(contractor: ContractorProfile) -> WorkRequest | None:
    return (
        WorkRequest.objects.filter(
            assigned_contractor=contractor,
            status__in=[WorkRequestStatus.SCHEDULING, WorkRequestStatus.IN_PROGRESS],
        )
        .select_related("user", "role", "assigned_contractor")
        .order_by("-updated_at")
        .first()
    )


def active_job_for_user(user: BotUser) -> WorkRequest | None:
    """Любая активная заявка по любому профилю исполнителя этого пользователя."""
    return (
        WorkRequest.objects.filter(
            assigned_contractor__user=user,
            status__in=[WorkRequestStatus.SCHEDULING, WorkRequestStatus.IN_PROGRESS],
        )
        .select_related("user", "role", "assigned_contractor", "assigned_contractor__user")
        .order_by("-updated_at")
        .first()
    )


def awaiting_commission_for_user(user: BotUser) -> WorkRequest | None:
    return (
        WorkRequest.objects.filter(
            assigned_contractor__user=user,
            status=WorkRequestStatus.AWAITING_COMMISSION,
            commission_status__in=[
                WorkRequestCommissionStatus.AWAITING,
                WorkRequestCommissionStatus.REJECTED,
            ],
        )
        .select_related("role", "assigned_contractor", "assigned_contractor__user")
        .order_by("-updated_at")
        .first()
    )


def awaiting_client_for_user(user: BotUser) -> WorkRequest | None:
    return (
        WorkRequest.objects.filter(
            assigned_contractor__user=user,
            status=WorkRequestStatus.AWAITING_CLIENT,
        )
        .select_related("role", "assigned_contractor")
        .order_by("-updated_at")
        .first()
    )


def route_contractor_receipt_photo(
    user: BotUser,
    pending: PendingAction,
    *,
    image_bytes: bytes,
    filename: str = "receipt.jpg",
) -> str | None:
    """
    Пока у исполнителя открыта заявка — чек только по работе/комиссии, не подписка.
    Возвращает текст ответа или None, если можно принимать чек подписки/сбора.
    """
    # Уже в нужном диалоге — пусть вызывающий код обработает сам.
    if pending.pending_kind in {COMPLETE_PENDING, COMMISSION_PENDING}:
        return None

    commission_req = awaiting_commission_for_user(user)
    if commission_req is not None:
        pending.pending_kind = COMMISSION_PENDING
        pending.pending_payload = {"work_request_id": commission_req.id}
        pending.save(update_fields=["pending_kind", "pending_payload", "updated_at"])
        reply = handle_commission_receipt_photo(
            user, pending, image_bytes=image_bytes, filename=filename
        )
        return (
            "Это чек комиссии по заявке "
            f"#{commission_req.id} (не чек подписки).\n\n"
            + reply
        )

    waiting = awaiting_client_for_user(user)
    if waiting is not None:
        return (
            f"По заявке #{waiting.id} отчёт уже отправлен — ждём подтверждения клиента.\n"
            "Сейчас чек подписки или сбора не принимаем.\n"
            "Когда заявка закроется, можно будет прислать чек подписки отдельно."
        )

    job = active_job_for_user(user)
    if job is None:
        return None

    # Фото при открытой работе → начинаем отчёт, чек буферизуем (не в подписку).
    if job.status == WorkRequestStatus.SCHEDULING:
        job.status = WorkRequestStatus.IN_PROGRESS
        job.save(update_fields=["status", "updated_at"])

    pending.pending_kind = COMPLETE_PENDING
    pending.pending_payload = {
        "step": "method",
        "work_request_id": job.id,
        "receipt_b64": base64.b64encode(image_bytes).decode("ascii"),
        "receipt_filename": (filename or "receipt.jpg")[:120],
    }
    pending.save(update_fields=["pending_kind", "pending_payload", "updated_at"])
    return (
        f"Чек принят как отчёт по работе (заявка #{job.id}: {job.role.name}), "
        "а не как оплата подписки.\n\n"
        "Как клиент оплатил?\n"
        "1 / перевод — используем этот чек\n"
        "2 / наличные — укажете сумму без чека"
    )


def contractor_blocked_for_new_offers(contractor: ContractorProfile) -> bool:
    """Новые заявки закрыты, пока не закрыта комиссия / активная работа / доплата."""
    user_id = contractor.user_id
    if WorkRequest.objects.filter(
        assigned_contractor__user_id=user_id,
        amount_mismatch_due__gt=0,
    ).exclude(amount_mismatch_message="").exists():
        return True
    return WorkRequest.objects.filter(
        assigned_contractor__user_id=user_id,
        commission_status__in=[
            WorkRequestCommissionStatus.AWAITING,
            WorkRequestCommissionStatus.PENDING_REVIEW,
            WorkRequestCommissionStatus.REJECTED,
        ],
    ).exists() or WorkRequest.objects.filter(
        assigned_contractor__user_id=user_id,
        status__in=[
            WorkRequestStatus.SCHEDULING,
            WorkRequestStatus.IN_PROGRESS,
            WorkRequestStatus.AWAITING_CLIENT,
            WorkRequestStatus.AWAITING_COMMISSION,
        ],
    ).exists()


def both_parties_marked_done(req: WorkRequest) -> bool:
    return bool(req.client_marked_done_at and req.executor_marked_done_at)


def can_mark_work_done(req: WorkRequest) -> bool:
    if req.status == WorkRequestStatus.IN_PROGRESS:
        return True
    if req.status == WorkRequestStatus.SCHEDULING and (req.agreed_slot or "").strip():
        return True
    return False


def mark_work_done(user: BotUser, req: WorkRequest) -> dict:
    """Клиент или исполнитель жмёт «Работа выполнена». Оба → опрос оплаты у мастера.

    Если клиент и мастер — один человек (тест / ошибка назначения), одна кнопка
    отмечает обе стороны сразу.
    """
    is_client = req.user_id == user.id
    is_executor = bool(
        req.assigned_contractor_id and req.assigned_contractor.user_id == user.id
    )
    if not (is_client or is_executor):
        raise ValueError("forbidden")
    if not can_mark_work_done(req):
        raise ValueError("not_in_progress")

    now = timezone.now()
    update_fields = ["updated_at"]
    if req.status == WorkRequestStatus.SCHEDULING:
        req.status = WorkRequestStatus.IN_PROGRESS
        update_fields.append("status")

    marked_any = False
    # Один аккаунт = и клиент, и мастер: отмечаем обе стороны одним нажатием.
    if is_client and not req.client_marked_done_at:
        req.client_marked_done_at = now
        update_fields.append("client_marked_done_at")
        marked_any = True
    if is_executor and not req.executor_marked_done_at:
        req.executor_marked_done_at = now
        update_fields.append("executor_marked_done_at")
        marked_any = True
    if not marked_any:
        raise ValueError("already_marked")

    req.save(update_fields=update_fields)
    both = both_parties_marked_done(req)
    same_person = is_client and is_executor
    payload = {
        "ok": True,
        "both_done": both,
        "client_marked_done": bool(req.client_marked_done_at),
        "executor_marked_done": bool(req.executor_marked_done_at),
        "needs_executor_payment_report": False,
        "same_person": same_person,
        "message": "",
    }
    if not both:
        waiting = "клиента" if (is_executor and not is_client) else "мастера"
        payload["message"] = (
            f"Отметили заявку #{req.id} как выполненную с вашей стороны. "
            f"Ждём отметку {waiting}."
        )
        return payload

    contractor = req.assigned_contractor
    if contractor:
        try:
            pending, _ = PendingAction.objects.get_or_create(user=contractor.user)
            ask = start_completion(contractor.user, pending, require_both_done=False)
            from services.work_request_dispatch import _default_send_fn

            _default_send_fn()(contractor.user, ask)
        except Exception:
            logger.exception("prompt executor payment after both done WR %s", req.id)
    if is_executor:
        payload["needs_executor_payment_report"] = True
        payload["message"] = (
            f"Оба отметили заявку #{req.id} выполненной.\n"
            "Как клиент рассчитался?\n"
            "1 / перевод  или  2 / наличные — укажите в приложении или в MAX."
        )
    else:
        payload["message"] = (
            f"Оба отметили заявку #{req.id} выполненной. "
            "Мастеру отправлен запрос, как с ним рассчитались."
        )
    return payload


def mismatch_topup_amount(req: WorkRequest) -> Decimal | None:
    reported = req.reported_amount
    confirmed = req.confirmed_amount
    if reported is None or confirmed is None:
        return None
    if Decimal(confirmed) <= Decimal(reported):
        return None
    due = commission_for_amount(Decimal(confirmed)) - commission_for_amount(
        Decimal(reported)
    )
    if due <= 0:
        return None
    return due.quantize(Decimal("0.01"))


def notify_executor_amount_mismatch(req: WorkRequest, *, note: str = "") -> str:
    due = mismatch_topup_amount(req)
    if due is None:
        raise ValueError("no_mismatch")
    body = (
        f"Сумма расходится, просьба доплатить разницу {due:.0f} ₽ "
        "чтобы вернуть доступ к новым заказам."
    )
    if note.strip():
        body = f"{body}\n{note.strip()}"
    body = f"{body}\n\n{platform_payee_lines()}"
    req.amount_mismatch_due = due
    req.amount_mismatch_message = body
    req.save(
        update_fields=["amount_mismatch_due", "amount_mismatch_message", "updated_at"]
    )
    if req.assigned_contractor:
        try:
            from services.work_request_dispatch import _default_send_fn

            _default_send_fn()(
                req.assigned_contractor.user,
                f"Работа: {body}",
            )
        except Exception:
            logger.exception("notify mismatch WR %s", req.id)
    return body


def clear_amount_mismatch(req: WorkRequest) -> None:
    req.amount_mismatch_due = None
    req.amount_mismatch_message = ""
    req.save(
        update_fields=["amount_mismatch_due", "amount_mismatch_message", "updated_at"]
    )
    close_task_for_source(AdminTaskKind.WORK_AMOUNT_MISMATCH, "WorkRequest", req.id)


def work_screen_notices_for_user(user: BotUser) -> list[dict]:
    rows = (
        WorkRequest.objects.filter(assigned_contractor__user=user)
        .exclude(amount_mismatch_message="")
        .filter(amount_mismatch_due__gt=0)
        .order_by("-updated_at")[:5]
    )
    return [
        {
            "work_request_id": r.id,
            "message": r.amount_mismatch_message,
            "amount_due": float(r.amount_mismatch_due or 0),
        }
        for r in rows
    ]


def schedule_message(
    *,
    user: BotUser,
    kind: str,
    text: str,
    send_at,
    meta: dict | None = None,
) -> ScheduledBotMessage:
    return ScheduledBotMessage.objects.create(
        user=user,
        kind=kind,
        text=text,
        send_at=send_at,
        meta=meta or {},
    )


def cancel_scheduled_for_request(req: WorkRequest, kind: str = SCHEDULED_KIND_CLIENT_CONFIRM) -> int:
    qs = ScheduledBotMessage.objects.filter(
        kind=kind,
        sent_at__isnull=True,
        cancelled_at__isnull=True,
        meta__work_request_id=req.id,
    )
    n = qs.count()
    qs.update(cancelled_at=timezone.now())
    return n


def start_completion(
    user: BotUser, pending: PendingAction, *, require_both_done: bool = True
) -> str:
    req = active_job_for_user(user)
    if not req:
        if not ContractorProfile.objects.filter(user=user).exists():
            return "Вы не зарегистрированы как исполнитель."
        return (
            "Нет заявки в статусе «в работе». "
            "Сначала примите предложение и выполните заказ."
        )
    now = timezone.now()
    if not req.executor_marked_done_at:
        req.executor_marked_done_at = now
        req.save(update_fields=["executor_marked_done_at", "updated_at"])
    if require_both_done and not req.client_marked_done_at:
        return (
            f"Заявка #{req.id}: отметили выполнениеку с вашей стороны.\n"
            "Когда клиент нажмёт «Работа выполнена» в приложении — "
            "спросим, как с вами рассчитались."
        )
    pending.pending_kind = COMPLETE_PENDING
    pending.pending_payload = {"step": "method", "work_request_id": req.id}
    pending.save(update_fields=["pending_kind", "pending_payload", "updated_at"])
    return (
        f"Заявка #{req.id} ({req.role.name}).\n"
        "Как клиент оплатил?\n"
        "1 / перевод — был перевод (нужен чек)\n"
        "2 / наличные — укажете сумму"
    )


def handle_completion_step(user: BotUser, text: str, pending: PendingAction) -> str:
    payload = dict(pending.pending_payload or {})
    req = (
        WorkRequest.objects.select_related("user", "role", "assigned_contractor")
        .filter(pk=payload.get("work_request_id"))
        .first()
    )
    if not req or not req.assigned_contractor or req.assigned_contractor.user_id != user.id:
        pending.clear_pending()
        return "Заявка не найдена. Напишите «заявка выполнена» снова."
    if req.status not in {
        WorkRequestStatus.IN_PROGRESS,
        WorkRequestStatus.SCHEDULING,
    }:
        pending.clear_pending()
        return f"Заявка #{req.id} уже не в работе (статус: {req.get_status_display()})."

    step = payload.get("step") or "method"
    raw = (text or "").strip().lower().replace("ё", "е")

    if step == "method":
        if raw in _TRANSFER or any(x in raw for x in ("перевод", "безнал", "карта")):
            payload["pay_method"] = WorkRequestPayMethod.TRANSFER
            payload["step"] = "amount"
            pending.pending_payload = payload
            pending.save(update_fields=["pending_payload", "updated_at"])
            return "Укажите сумму перевода в рублях (например: 2500)."
        if raw in _CASH or "налич" in raw:
            payload["pay_method"] = WorkRequestPayMethod.CASH
            # Наличные — буфер чека не нужен
            payload.pop("receipt_b64", None)
            payload.pop("receipt_filename", None)
            payload["step"] = "amount"
            pending.pending_payload = payload
            pending.save(update_fields=["pending_payload", "updated_at"])
            return "Укажите сумму наличными в рублях (например: 2500)."
        return "Ответьте: 1 / перевод  или  2 / наличные."

    if step == "amount":
        amount = parse_money(text)
        if amount is None:
            return "Не понял сумму. Напишите число, например: 2500"
        payload["amount"] = str(amount)
        method = payload.get("pay_method")
        if method == WorkRequestPayMethod.TRANSFER:
            receipt_b64 = payload.get("receipt_b64")
            if receipt_b64:
                try:
                    receipt_bytes = base64.b64decode(receipt_b64)
                except Exception:
                    receipt_bytes = None
                if receipt_bytes:
                    return _finalize_executor_report(
                        user,
                        pending,
                        payload,
                        req,
                        receipt_bytes=receipt_bytes,
                        filename=str(payload.get("receipt_filename") or "receipt.jpg"),
                    )
            payload["step"] = "receipt"
            pending.pending_payload = payload
            pending.save(update_fields=["pending_payload", "updated_at"])
            return (
                f"Сумма перевода: {amount} ₽.\n"
                "Пришлите фото или PDF чека перевода от клиента "
                "(это чек по работе, не по подписке)."
            )
        return _finalize_executor_report(user, pending, payload, req, receipt_bytes=None)

    if step == "receipt":
        return (
            "Жду фото или PDF чека перевода по заявке. "
            "Это не чек подписки — пришлите файл в чат."
        )

    return start_completion(user, pending)


def handle_completion_receipt_photo(
    user: BotUser,
    pending: PendingAction,
    *,
    image_bytes: bytes,
    filename: str = "receipt.jpg",
) -> str:
    payload = dict(pending.pending_payload or {})
    if pending.pending_kind != COMPLETE_PENDING or payload.get("step") != "receipt":
        return handle_completion_step(user, "", pending)
    req = WorkRequest.objects.filter(pk=payload.get("work_request_id")).first()
    if not req:
        pending.clear_pending()
        return "Заявка не найдена."
    return _finalize_executor_report(
        user,
        pending,
        payload,
        req,
        receipt_bytes=image_bytes,
        filename=filename,
    )


def _finalize_executor_report(
    user: BotUser,
    pending: PendingAction,
    payload: dict,
    req: WorkRequest,
    *,
    receipt_bytes: bytes | None,
    filename: str = "receipt.jpg",
) -> str:
    amount = parse_money(str(payload.get("amount") or ""))
    method = payload.get("pay_method") or ""
    if amount is None or method not in WorkRequestPayMethod.values:
        pending.clear_pending()
        return "Ошибка данных отчёта. Напишите «заявка выполнена» снова."

    now = timezone.now()
    commission = commission_for_amount(amount)
    earned = executor_net_earned(amount, commission)
    req.pay_method = method
    req.reported_amount = amount
    req.executor_reported_at = now
    req.client_confirm_due_at = now + timedelta(minutes=CLIENT_CONFIRM_DELAY_MINUTES)
    req.commission_amount = commission
    req.executor_earned_amount = earned
    req.commission_status = WorkRequestCommissionStatus.AWAITING
    req.status = WorkRequestStatus.AWAITING_COMMISSION
    if not req.executor_marked_done_at:
        req.executor_marked_done_at = now
    if receipt_bytes:
        req.job_receipt.save(
            (filename or "receipt.jpg")[:120],
            ContentFile(receipt_bytes),
            save=False,
        )
    req.save()

    client_text = _client_confirm_message(req)
    schedule_message(
        user=req.user,
        kind=SCHEDULED_KIND_CLIENT_CONFIRM,
        text=client_text,
        send_at=req.client_confirm_due_at,
        meta={"work_request_id": req.id},
    )

    # Сразу просим комиссию 10% по сумме мастера.
    ask = (
        f"Отчёт по заявке #{req.id} принят: {amount} ₽ "
        f"({req.get_pay_method_display()}).\n"
        f"Комиссия 10%: {commission} ₽. Вам остаётся: {earned} ₽.\n\n"
        f"Переведите комиссию:\n{platform_payee_lines()}\n\n"
        "Пришлите фото или PDF чека.\n"
        "Пока чек не принят — новые заявки не приходят.\n"
        f"Через {CLIENT_CONFIRM_DELAY_MINUTES} мин спросим клиента про оплату."
    )
    pending.pending_kind = COMMISSION_PENDING
    pending.pending_payload = {"work_request_id": req.id}
    pending.save(update_fields=["pending_kind", "pending_payload", "updated_at"])
    ActivityLog.objects.create(
        user=user,
        kind=ActivityKind.CONTRACTOR_REPLY,
        title=f"Заявка #{req.id}: выполнена",
        detail=f"{method} {amount} ₽",
        meta={"work_request_id": req.id},
    )
    return ask


def _client_confirm_message(req: WorkRequest) -> str:
    return (
        f"Заявка #{req.id} ({req.role.name}): "
        "пожалуйста, актуализируйте информацию по заказу.\n\n"
        "Как вы рассчитались с мастером?\n"
        "1 / перевод\n"
        "2 / наличные\n\n"
        "Затем укажите сумму, которую оплатили, числом (например: 2500)."
    )


def on_client_confirm_message_sent(msg: ScheduledBotMessage) -> None:
    """После отправки отложенного сообщения — ждём ответ клиента (способ + сумма)."""
    req_id = (msg.meta or {}).get("work_request_id")
    if not req_id:
        return
    pending, _ = PendingAction.objects.get_or_create(user=msg.user)
    pending.pending_kind = CLIENT_CONFIRM_PENDING
    pending.pending_payload = {"work_request_id": req_id, "step": "method"}
    pending.save(update_fields=["pending_kind", "pending_payload", "updated_at"])
    try:
        from api.emit import emit_app_event

        emit_app_event(
            msg.user,
            ntype="work_request.confirm_amount",
            title="Актуализируйте оплату заказа",
            body=(msg.text or "")[:500],
            entity_type="work_request",
            entity_id=int(req_id),
        )
    except Exception:
        logger.exception("app inbox confirm_amount WR %s", req_id)


def handle_client_confirm_step(user: BotUser, text: str, pending: PendingAction) -> str:
    payload = dict(pending.pending_payload or {})
    req = (
        WorkRequest.objects.select_related(
            "assigned_contractor", "assigned_contractor__user", "role"
        )
        .filter(pk=payload.get("work_request_id"), user=user)
        .first()
    )
    if not req:
        pending.clear_pending()
        return "Заявка не найдена."
    if req.confirmed_amount is not None:
        pending.clear_pending()
        return "Подтверждение по этой заявке уже не требуется."
    if req.status not in {
        WorkRequestStatus.AWAITING_CLIENT,
        WorkRequestStatus.AWAITING_COMMISSION,
        WorkRequestStatus.DONE,
    }:
        pending.clear_pending()
        return "Подтверждение по этой заявке уже не требуется."

    step = payload.get("step") or "method"
    raw = (text or "").strip().lower().replace("ё", "е")

    if step == "method":
        if raw in _TRANSFER or any(x in raw for x in ("перевод", "безнал", "карта")):
            payload["pay_method"] = WorkRequestPayMethod.TRANSFER
            payload["step"] = "amount"
            pending.pending_payload = payload
            pending.save(update_fields=["pending_payload", "updated_at"])
            return "Укажите сумму перевода в рублях (например: 2500)."
        if raw in _CASH or "налич" in raw:
            payload["pay_method"] = WorkRequestPayMethod.CASH
            payload["step"] = "amount"
            pending.pending_payload = payload
            pending.save(update_fields=["pending_payload", "updated_at"])
            return "Укажите сумму наличными в рублях (например: 2500)."
        # Совместимость: «да» = сумма мастера; число = сразу сумма
        amount = parse_money(text)
        if raw in _YES and req.reported_amount is not None:
            payload["pay_method"] = req.pay_method or WorkRequestPayMethod.CASH
            return _apply_client_confirmation(
                req, Decimal(req.reported_amount), pending, pay_method=payload["pay_method"]
            )
        if amount is not None:
            payload["pay_method"] = req.pay_method or WorkRequestPayMethod.CASH
            return _apply_client_confirmation(
                req, amount, pending, pay_method=payload["pay_method"]
            )
        return "Ответьте: 1 / перевод  или  2 / наличные.\n" + _client_confirm_message(req)

    if step == "amount":
        amount = parse_money(text)
        if amount is None:
            return "Не понял сумму. Напишите число, например: 2500"
        method = payload.get("pay_method") or req.pay_method or WorkRequestPayMethod.CASH
        return _apply_client_confirmation(req, amount, pending, pay_method=method)

    return _client_confirm_message(req)


def _apply_client_confirmation(
    req: WorkRequest,
    amount: Decimal,
    pending: PendingAction,
    *,
    pay_method: str = "",
) -> str:
    now = timezone.now()
    method = pay_method or req.pay_method or ""
    req.confirmed_amount = amount
    req.client_confirmed_at = now
    if method in WorkRequestPayMethod.values:
        req.client_pay_method = method
    # Комиссия уже могла быть выставлена по сумме мастера — пересчёт при расхождении.
    reported = req.reported_amount
    update_fields = [
        "confirmed_amount",
        "client_confirmed_at",
        "client_pay_method",
        "updated_at",
    ]
    mismatch_due = None
    if reported is not None and Decimal(amount) > Decimal(reported):
        mismatch_due = (
            commission_for_amount(Decimal(amount))
            - commission_for_amount(Decimal(reported))
        ).quantize(Decimal("0.01"))
        if mismatch_due > 0:
            req.amount_mismatch_due = mismatch_due
            update_fields.append("amount_mismatch_due")
            method_label = dict(WorkRequestPayMethod.choices).get(method, method or "—")
            try:
                upsert_task(
                    kind=AdminTaskKind.WORK_AMOUNT_MISMATCH,
                    title=f"Расхождение суммы: заявка #{req.id}",
                    description=(
                        f"Роль: {req.role.name}\n"
                        f"Описание: {(req.description or '')[:400]}\n"
                        f"Мастер указал: {reported} ₽"
                        f" ({req.get_pay_method_display() or '—'})\n"
                        f"Клиент указал: {amount} ₽ ({method_label})\n"
                        f"Доплата комиссии: {mismatch_due} ₽"
                    ),
                    user=(
                        req.assigned_contractor.user
                        if req.assigned_contractor
                        else req.user
                    ),
                    action_url=f"/panel/work-requests/{req.id}/",
                    source_model="WorkRequest",
                    source_id=req.id,
                    priority=15,
                )
            except Exception:
                logger.exception("admin task mismatch WR %s", req.id)

    req.save(update_fields=update_fields)
    pending.clear_pending()

    try:
        from services.work_request_rating import ask_client_for_rating, rating_ask_message

        ask_client_for_rating(req, send=False)
        rating_line = "\n\n" + rating_ask_message(req)
    except Exception:
        logger.exception("Failed to ask rating after confirm WR %s", req.id)
        rating_line = "\n\nОцените работу исполнителя от 1 до 5 (5 — отлично)."

    extra = ""
    if mismatch_due and mismatch_due > 0:
        extra = (
            f"\nСумма выше отчёта мастера — администратор проверит расхождение "
            f"(возможная доплата комиссии {mismatch_due} ₽)."
        )
    return (
        f"Спасибо! Зафиксировали сумму {amount} ₽ по заявке #{req.id}."
        + extra
        + rating_line
    )


def handle_commission_receipt_photo(
    user: BotUser,
    pending: PendingAction,
    *,
    image_bytes: bytes,
    filename: str = "commission.jpg",
) -> str:
    if pending.pending_kind != COMMISSION_PENDING:
        return "Сейчас чек комиссии не ожидается."
    payload = dict(pending.pending_payload or {})
    req = (
        WorkRequest.objects.select_related("role", "assigned_contractor")
        .filter(pk=payload.get("work_request_id"))
        .first()
    )
    if not req or not req.assigned_contractor or req.assigned_contractor.user_id != user.id:
        pending.clear_pending()
        return "Заявка для комиссии не найдена."
    if req.commission_status not in {
        WorkRequestCommissionStatus.AWAITING,
        WorkRequestCommissionStatus.REJECTED,
    }:
        pending.clear_pending()
        return "Комиссия по этой заявке уже на проверке или принята."

    req.commission_receipt.save(
        (filename or "commission.jpg")[:120],
        ContentFile(image_bytes),
        save=False,
    )
    req.commission_status = WorkRequestCommissionStatus.PENDING_REVIEW
    req.commission_submitted_at = timezone.now()
    req.save(
        update_fields=[
            "commission_receipt",
            "commission_status",
            "commission_submitted_at",
            "updated_at",
        ]
    )
    pending.clear_pending()
    try:
        upsert_task(
            kind=AdminTaskKind.WORK_COMMISSION,
            title=f"Комиссия 10%: заявка #{req.id} — {user}",
            description=(
                f"Роль: {req.role.name}\n"
                f"Сумма работы: {req.confirmed_amount} ₽\n"
                f"Комиссия: {req.commission_amount} ₽"
            ),
            user=user,
            action_url=f"/panel/work-requests/{req.id}/",
            source_model="WorkRequest",
            source_id=req.id,
            priority=20,
        )
    except Exception:
        logger.exception("admin task commission WR %s", req.id)
    return (
        f"Чек комиссии по заявке #{req.id} отправлен администратору.\n"
        f"Сумма к проверке: {req.commission_amount} ₽.\n"
        "После подтверждения снова сможете получать заявки."
    )


def handle_commission_text(user: BotUser, text: str, pending: PendingAction) -> str:
    if pending.pending_kind != COMMISSION_PENDING:
        return "Сейчас ожидается фото чека комиссии 10%."
    return (
        "Пришлите фото или PDF чека перевода комиссии 10% "
        "самозанятому (реквизиты уже отправляли)."
    )


def approve_commission(req: WorkRequest, *, note: str = "") -> None:
    if req.commission_status not in {
        WorkRequestCommissionStatus.PENDING_REVIEW,
        WorkRequestCommissionStatus.AWAITING,
        WorkRequestCommissionStatus.REJECTED,
    }:
        raise ValueError(
            f"Комиссию нельзя принять в статусе «{req.get_commission_status_display()}»."
        )
    if not req.commission_amount:
        raise ValueError("У заявки нет суммы комиссии.")
    req.commission_status = WorkRequestCommissionStatus.APPROVED
    req.commission_reviewed_at = timezone.now()
    req.commission_admin_note = (note or "").strip()
    req.status = WorkRequestStatus.DONE
    req.save(
        update_fields=[
            "commission_status",
            "commission_reviewed_at",
            "commission_admin_note",
            "status",
            "updated_at",
        ]
    )
    close_task_for_source(AdminTaskKind.WORK_COMMISSION, "WorkRequest", req.id)
    close_task_for_source(AdminTaskKind.WORK_REQUEST, "WorkRequest", req.id)
    if req.assigned_contractor:
        try:
            from services.work_request_dispatch import _default_send_fn

            _default_send_fn()(
                req.assigned_contractor.user,
                f"Комиссия по заявке #{req.id} принята. "
                "Можете снова получать новые заказы.",
            )
        except Exception:
            logger.exception("notify commission approved WR %s", req.id)


def reject_commission(req: WorkRequest, *, note: str = "") -> None:
    req.commission_status = WorkRequestCommissionStatus.REJECTED
    req.commission_reviewed_at = timezone.now()
    req.commission_admin_note = (note or "").strip()
    req.status = WorkRequestStatus.AWAITING_COMMISSION
    req.save(
        update_fields=[
            "commission_status",
            "commission_reviewed_at",
            "commission_admin_note",
            "status",
            "updated_at",
        ]
    )
    close_task_for_source(AdminTaskKind.WORK_COMMISSION, "WorkRequest", req.id)
    if req.assigned_contractor:
        c_user = req.assigned_contractor.user
        pending, _ = PendingAction.objects.get_or_create(user=c_user)
        pending.pending_kind = COMMISSION_PENDING
        pending.pending_payload = {"work_request_id": req.id}
        pending.save(update_fields=["pending_kind", "pending_payload", "updated_at"])
        try:
            from services.work_request_dispatch import _default_send_fn

            _default_send_fn()(
                c_user,
                f"Чек комиссии по заявке #{req.id} отклонён."
                + (f"\n{note}" if note else "")
                + "\nПришлите корректный чек перевода 10%.\n"
                + platform_payee_lines(),
            )
        except Exception:
            logger.exception("notify commission rejected WR %s", req.id)


def process_due_scheduled_messages(*, send_fn=None) -> int:
    """Отправить отложенные сообщения, у которых наступил send_at."""
    from services.work_request_dispatch import _default_send_fn

    send_fn = send_fn or _default_send_fn()
    now = timezone.now()
    ids = list(
        ScheduledBotMessage.objects.filter(
            sent_at__isnull=True,
            cancelled_at__isnull=True,
            send_at__lte=now,
        )
        .order_by("send_at", "id")
        .values_list("id", flat=True)[:100]
    )
    n = 0
    for msg_id in ids:
        try:
            with transaction.atomic():
                msg = (
                    ScheduledBotMessage.objects.select_for_update()
                    .select_related("user")
                    .filter(pk=msg_id)
                    .first()
                )
                if not msg or msg.sent_at or msg.cancelled_at:
                    continue
                send_fn(msg.user, msg.text)
                msg.sent_at = timezone.now()
                msg.save(update_fields=["sent_at"])
                claimed = msg
            n += 1
            if claimed.kind == SCHEDULED_KIND_CLIENT_CONFIRM:
                on_client_confirm_message_sent(claimed)
        except Exception:
            logger.exception("Failed scheduled message %s", msg_id)
    return n
