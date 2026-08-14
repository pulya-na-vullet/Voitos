"""Завершение заявки: отчёт исполнителя → опрос клиента через 20 мин → комиссия 10%."""

from __future__ import annotations

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


def contractor_blocked_for_new_offers(contractor: ContractorProfile) -> bool:
    """Новые заявки закрыты, пока не закрыта комиссия 10% по прошлым работам."""
    return WorkRequest.objects.filter(
        assigned_contractor=contractor,
        commission_status__in=[
            WorkRequestCommissionStatus.AWAITING,
            WorkRequestCommissionStatus.PENDING_REVIEW,
            WorkRequestCommissionStatus.REJECTED,
        ],
    ).exists() or WorkRequest.objects.filter(
        assigned_contractor=contractor,
        status__in=[
            WorkRequestStatus.SCHEDULING,
            WorkRequestStatus.IN_PROGRESS,
            WorkRequestStatus.AWAITING_CLIENT,
            WorkRequestStatus.AWAITING_COMMISSION,
        ],
    ).exists()


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


def start_completion(user: BotUser, pending: PendingAction) -> str:
    profile = getattr(user, "contractor_profile", None)
    if profile is None:
        try:
            profile = ContractorProfile.objects.get(user=user)
        except ContractorProfile.DoesNotExist:
            return "Вы не зарегистрированы как исполнитель."
    req = active_job_for_contractor(profile)
    if not req:
        return (
            "Нет заявки в статусе «в работе». "
            "Сначала примите предложение и выполните заказ."
        )
    pending.pending_kind = COMPLETE_PENDING
    pending.pending_payload = {"step": "method", "work_request_id": req.id}
    pending.save(update_fields=["pending_kind", "pending_payload", "updated_at"])
    return (
        f"Заявка #{req.id} ({req.role.name}).\n"
        "Как клиент оплатил работу?\n"
        "1 / перевод — был перевод денег (нужен чек)\n"
        "2 / наличные — оплата наличными (укажете сумму)"
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
    if req.status != WorkRequestStatus.IN_PROGRESS:
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
            payload["step"] = "receipt"
            pending.pending_payload = payload
            pending.save(update_fields=["pending_payload", "updated_at"])
            return (
                f"Сумма перевода: {amount} ₽.\n"
                "Пришлите фото или PDF чека перевода от клиента."
            )
        return _finalize_executor_report(user, pending, payload, req, receipt_bytes=None)

    if step == "receipt":
        return "Жду фото или PDF чека перевода. Пришлите файл в чат."

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
    req.pay_method = method
    req.reported_amount = amount
    req.executor_reported_at = now
    req.client_confirm_due_at = now + timedelta(minutes=CLIENT_CONFIRM_DELAY_MINUTES)
    req.status = WorkRequestStatus.AWAITING_CLIENT
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
    pending.clear_pending()
    ActivityLog.objects.create(
        user=user,
        kind=ActivityKind.CONTRACTOR_REPLY,
        title=f"Заявка #{req.id}: выполнена",
        detail=f"{method} {amount} ₽",
        meta={"work_request_id": req.id},
    )
    return (
        f"Отчёт по заявке #{req.id} принят.\n"
        f"Способ: {req.get_pay_method_display()}, сумма: {amount} ₽.\n"
        f"Через {CLIENT_CONFIRM_DELAY_MINUTES} мин спросим клиента о сумме.\n"
        "После его подтверждения нужно будет перевести комиссию 10%."
    )


def _client_confirm_message(req: WorkRequest) -> str:
    amount = req.reported_amount or Decimal("0")
    if req.pay_method == WorkRequestPayMethod.TRANSFER:
        return (
            f"По заявке #{req.id} ({req.role.name}) исполнитель указал, "
            f"что вы перевели {amount} ₽.\n\n"
            "Подтвердите:\n"
            "1 / да — сумма верна, перевод получен\n"
            "2 / нет — напишите фактическую сумму перевода числом\n"
            "Или сразу отправьте сумму числом."
        )
    return (
        f"По заявке #{req.id} ({req.role.name}) исполнитель указал оплату "
        f"наличными {amount} ₽.\n\n"
        "Подтвердите:\n"
        "1 / да — сумма верна\n"
        "2 / нет — укажите, сколько вы реально выплатили исполнителю (числом)\n"
        "Или сразу отправьте сумму числом."
    )


def on_client_confirm_message_sent(msg: ScheduledBotMessage) -> None:
    """После отправки отложенного сообщения — ждём ответ клиента."""
    req_id = (msg.meta or {}).get("work_request_id")
    if not req_id:
        return
    pending, _ = PendingAction.objects.get_or_create(user=msg.user)
    pending.pending_kind = CLIENT_CONFIRM_PENDING
    pending.pending_payload = {"work_request_id": req_id}
    pending.save(update_fields=["pending_kind", "pending_payload", "updated_at"])


def handle_client_confirm_step(user: BotUser, text: str, pending: PendingAction) -> str:
    payload = dict(pending.pending_payload or {})
    req = (
        WorkRequest.objects.select_related("assigned_contractor", "assigned_contractor__user", "role")
        .filter(pk=payload.get("work_request_id"), user=user)
        .first()
    )
    if not req:
        pending.clear_pending()
        return "Заявка не найдена."
    if req.status != WorkRequestStatus.AWAITING_CLIENT:
        pending.clear_pending()
        return "Подтверждение по этой заявке уже не требуется."

    raw = (text or "").strip().lower().replace("ё", "е")
    amount = parse_money(text)
    if raw in _YES:
        amount = req.reported_amount
    elif raw in _NO:
        pending.pending_payload = {
            "work_request_id": req.id,
            "step": "amount_only",
        }
        pending.save(update_fields=["pending_payload", "updated_at"])
        if req.pay_method == WorkRequestPayMethod.CASH:
            return "Напишите сумму, которую вы выплатили исполнителю наличными (₽)."
        return "Напишите фактическую сумму перевода (₽)."
    elif payload.get("step") == "amount_only" and amount is None:
        return "Нужна сумма числом, например: 2500"
    elif amount is None:
        return (
            "Ответьте «да», если сумма верна, или укажите сумму числом.\n"
            + _client_confirm_message(req)
        )

    return _apply_client_confirmation(req, amount, pending)


def _apply_client_confirmation(
    req: WorkRequest, amount: Decimal, pending: PendingAction
) -> str:
    now = timezone.now()
    commission = commission_for_amount(amount)
    earned = executor_net_earned(amount, commission)
    req.confirmed_amount = amount
    req.client_confirmed_at = now
    req.commission_amount = commission
    req.executor_earned_amount = earned
    req.commission_status = WorkRequestCommissionStatus.AWAITING
    req.status = WorkRequestStatus.AWAITING_COMMISSION
    req.save(
        update_fields=[
            "confirmed_amount",
            "client_confirmed_at",
            "commission_amount",
            "executor_earned_amount",
            "commission_status",
            "status",
            "updated_at",
        ]
    )
    pending.clear_pending()

    contractor = req.assigned_contractor
    if contractor:
        ask = (
            f"Клиент подтвердил оплату по заявке #{req.id}: {amount} ₽.\n"
            f"Комиссия сервиса 10%: {commission} ₽.\n"
            f"Ваш заработок по заявке: {earned} ₽.\n\n"
            "Переведите эту сумму самозанятому, закреплённому в системе:\n"
            f"{platform_payee_lines()}\n\n"
            "После перевода пришлите фото/PDF чека в этот чат.\n"
            "Пока администратор не подтвердит оплату комиссии, "
            "новые заявки вам предлагаться не будут."
        )
        try:
            from services.work_request_dispatch import _default_send_fn

            _default_send_fn()(contractor.user, ask)
        except Exception:
            logger.exception("Failed to ask commission for WR %s", req.id)
        c_pending, _ = PendingAction.objects.get_or_create(user=contractor.user)
        c_pending.pending_kind = COMMISSION_PENDING
        c_pending.pending_payload = {"work_request_id": req.id}
        c_pending.save(update_fields=["pending_kind", "pending_payload", "updated_at"])

    try:
        from services.work_request_rating import ask_client_for_rating, rating_ask_message

        ask_client_for_rating(req, send=False)
        rating_line = "\n\n" + rating_ask_message(req)
    except Exception:
        logger.exception("Failed to ask rating after confirm WR %s", req.id)
        rating_line = (
            "\n\nОцените работу исполнителя от 1 до 5 (5 — отлично)."
        )

    return (
        f"Спасибо! Зафиксировали сумму {amount} ₽ по заявке #{req.id}.\n"
        "Исполнителю отправлен запрос на комиссию сервиса."
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
