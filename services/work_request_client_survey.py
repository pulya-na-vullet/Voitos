"""Опрос клиента после ручного перевода заявки в «Ждём подтверждения»."""

from __future__ import annotations

import logging

from database.models import (
    BotUser,
    PendingAction,
    WorkRequest,
    WorkRequestCommissionStatus,
    WorkRequestStatus,
)

logger = logging.getLogger(__name__)

SERVICE_SURVEY_PENDING = "work_request_service_survey"

_YES = {"да", "yes", "y", "+", "ага", "угу", "ок", "верно", "1", "оказана", "была"}
_NO = {"нет", "no", "n", "-", "не", "2", "не оказана", "не было"}


def _send():
    from services.work_request_dispatch import _default_send_fn

    return _default_send_fn()


def _send_or_raise(user: BotUser, text: str, *, send_fn=None) -> None:
    """Отправка в MAX; без токена / id — явная ошибка (не тихий skip)."""
    if send_fn is not None:
        send_fn(user, text)
        return

    from ai.factory import get_runtime_settings

    cfg = get_runtime_settings()
    token = (cfg.max_bot_token or "").strip()
    if not token:
        raise RuntimeError("Не задан токен бота MAX (Настройки).")
    if not (user.chat_id or "").strip() and not (user.max_user_id or "").strip():
        raise RuntimeError(
            f"У жителя {user} нет chat_id / max_user_id — бот не знает, куда писать."
        )
    _send()(user, text)


def service_survey_message(req: WorkRequest) -> str:
    role = req.role.name if req.role_id else "мастер"
    return (
        f"Заявка #{req.id} ({role}).\n\n"
        "Услуга была оказана?\n"
        "1 / да\n"
        "2 / нет"
    )


def start_client_service_survey(
    req: WorkRequest, *, send_fn=None
) -> tuple[bool, str]:
    """
    Сразу спросить клиента: оказана ли услуга.
    Возвращает (ok, человекочитаемый статус).
    """
    req = (
        WorkRequest.objects.select_related("user", "role", "assigned_contractor")
        .filter(pk=req.pk)
        .first()
    )
    if not req:
        return False, "Заявка не найдена."
    if req.status != WorkRequestStatus.AWAITING_CLIENT:
        return (
            False,
            "Опрос отправляется только в статусе «Ждём подтверждения клиента».",
        )

    # Не дублировать отложенный опрос суммы от мастера — сначала «услуга оказана?»
    try:
        from services.work_request_completion import (
            SCHEDULED_KIND_CLIENT_CONFIRM,
            cancel_scheduled_for_request,
        )

        cancel_scheduled_for_request(req, kind=SCHEDULED_KIND_CLIENT_CONFIRM)
    except Exception:
        logger.exception("cancel scheduled client confirm WR %s", req.id)

    text = service_survey_message(req)
    try:
        _send_or_raise(req.user, text, send_fn=send_fn)
    except Exception as exc:
        logger.exception("Failed to send service survey WR %s", req.id)
        # Pending всё равно ставим — клиент может ответить, если сообщение дойдёт иначе
        pending, _ = PendingAction.objects.get_or_create(user=req.user)
        pending.pending_kind = SERVICE_SURVEY_PENDING
        pending.pending_payload = {"work_request_id": req.id}
        pending.save(update_fields=["pending_kind", "pending_payload", "updated_at"])
        return False, f"Ожидание ответа включено, но MAX не отправил: {exc}"

    pending, _ = PendingAction.objects.get_or_create(user=req.user)
    pending.pending_kind = SERVICE_SURVEY_PENDING
    pending.pending_payload = {"work_request_id": req.id}
    pending.save(update_fields=["pending_kind", "pending_payload", "updated_at"])
    return True, "Клиенту в MAX отправлен вопрос: оказана ли услуга."


def handle_service_survey_step(user: BotUser, text: str, pending: PendingAction) -> str:
    if pending.pending_kind != SERVICE_SURVEY_PENDING:
        return "Сейчас ответ по услуге не ожидается."
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
    if req.status != WorkRequestStatus.AWAITING_CLIENT:
        pending.clear_pending()
        return "По этой заявке подтверждение уже не нужно."

    raw = (text or "").strip().lower().replace("ё", "е")
    if raw in _YES or raw.startswith("да"):
        return _on_service_yes(req, pending)
    if raw in _NO or raw.startswith("нет"):
        return _on_service_no(req, pending)
    return "Ответьте: 1 / да  или  2 / нет."


def _on_service_yes(req: WorkRequest, pending: PendingAction) -> str:
    """Услуга оказана → обычный флоу суммы."""
    from services.work_request_completion import (
        CLIENT_CONFIRM_PENDING,
        _client_confirm_message,
    )

    pending.pending_kind = CLIENT_CONFIRM_PENDING
    if req.reported_amount is not None:
        pending.pending_payload = {"work_request_id": req.id}
        pending.save(update_fields=["pending_kind", "pending_payload", "updated_at"])
        return "Спасибо! Уточним оплату.\n\n" + _client_confirm_message(req)
    pending.pending_payload = {
        "work_request_id": req.id,
        "step": "amount_only",
    }
    pending.save(update_fields=["pending_kind", "pending_payload", "updated_at"])
    return (
        "Спасибо! Напишите сумму, которую вы заплатили мастеру (₽), "
        "например: 2500."
    )


def _on_service_no(req: WorkRequest, pending: PendingAction) -> str:
    """Услуга не оказана → оценка качества сервиса, заявка закрывается."""
    note = "Клиент ответил: услуга не оказана (опрос из панели)."
    admin_note = (req.admin_note or "").strip()
    if note not in admin_note:
        admin_note = f"{admin_note}\n{note}".strip() if admin_note else note
    req.status = WorkRequestStatus.CANCELLED
    req.commission_status = WorkRequestCommissionStatus.NONE
    req.admin_note = admin_note
    req.save(
        update_fields=[
            "status",
            "commission_status",
            "admin_note",
            "updated_at",
        ]
    )
    pending.clear_pending()

    try:
        if req.assigned_contractor_id:
            _send_or_raise(
                req.assigned_contractor.user,
                f"Клиент по заявке #{req.id} ответил, что услуга не оказана.\n"
                "Заявка закрыта. Новые заказы снова доступны.",
            )
    except Exception:
        logger.exception("notify master service not provided WR %s", req.id)

    try:
        from database.models import AdminTaskKind
        from panel.admin_tasks import close_task_for_source

        close_task_for_source(AdminTaskKind.WORK_REQUEST, "WorkRequest", req.id)
        close_task_for_source(AdminTaskKind.WORK_COMMISSION, "WorkRequest", req.id)
    except Exception:
        logger.exception("close admin tasks after survey no WR %s", req.id)

    rating_line = (
        "\n\nОцените качество сервиса по этой заявке от 1 до 5 "
        "(5 — отлично)."
    )
    try:
        from services.work_request_rating import (
            ask_client_for_rating,
            rating_ask_message,
        )

        if ask_client_for_rating(
            req,
            send=False,
            service_provided=False,
            allow_without_confirm=True,
        ):
            rating_line = "\n\n" + rating_ask_message(req, service_provided=False)
    except Exception:
        logger.exception("ask service rating after survey no WR %s", req.id)

    return "Понял, услуга не оказана. Заявку закрыли." + rating_line
