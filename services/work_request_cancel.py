"""Отмена оформления заявки на мастера в любой момент диалога."""

from __future__ import annotations

import logging
import re

from database.models import (
    BotUser,
    PendingAction,
    WorkRequest,
    WorkRequestOffer,
    WorkRequestOfferStatus,
    WorkRequestStatus,
)

logger = logging.getLogger(__name__)

WORK_FLOW_PENDING_KINDS = frozenset(
    {
        "work_request",
        "work_request_offer_reply",
        "work_request_complete",
        "work_request_client_confirm",
        "work_request_commission",
        "work_request_rating",
        "work_request_schedule_master",
        "work_request_schedule_client",
    }
)

# Явная отмена без ИИ
_CANCEL_EXACT = re.compile(
    r"^\s*("
    r"отмена|отменить|отмени|отбой|стоп|"
    r"не\s+надо|не\s+нужно|не\s+буду|не\s+хочу|"
    r"передумал[а]?|ошибс[яя]|я\s+ошибс[яя]|"
    r"я\s+ошибс[яя]\s+тут|ошибка|"
    r"отменить\s+(заявку|заказ)|отмени\s+(заявку|заказ)|"
    r"хватит|назад|отменить\s+вс[её]"
    r")\s*[.!]?\s*$",
    re.IGNORECASE,
)

# Мягкие маркеры — имеет смысл спросить ИИ
_CANCEL_HINT = re.compile(
    r"(отмен|отбой|стоп|ошиб|передум|не\s+то|не\s+надо|не\s+нужно|"
    r"зря|случайно|хватит|назад|не\s+буду|не\s+хочу)",
    re.IGNORECASE,
)

# Точно НЕ отмена (шаги флоу)
_NOT_CANCEL = re.compile(
    r"^\s*("
    r"\d{1,3}|готово|да|нет|перевод|наличные|"
    r"пропустить|ок|угу|ага"
    r")\s*[.!]?\s*$",
    re.IGNORECASE,
)


def looks_like_cancel_rule(text: str) -> bool:
    return bool(_CANCEL_EXACT.match((text or "").strip()))


def looks_like_cancel_hint(text: str) -> bool:
    raw = (text or "").strip()
    if not raw or _NOT_CANCEL.match(raw):
        return False
    if looks_like_cancel_rule(raw):
        return True
    return bool(_CANCEL_HINT.search(raw))


def ai_says_cancel(text: str) -> bool:
    """ИИ: пользователь хочет прекратить оформление заявки?"""
    try:
        from ai.factory import AINotConfiguredError, get_llm_provider

        llm = get_llm_provider()
        system = (
            "Ты классификатор коротких реплик в чат-боте заказа мастера. "
            "Ответь строго одним словом: YES или NO.\n"
            "YES — если пользователь хочет отменить оформление заявки, "
            "передумал, ошибся, просит остановить процесс.\n"
            "NO — если это обычный ответ по шагам заявки "
            "(описание работ, сумма, время, оценка, да/нет, фото и т.п.)."
        )
        out = (llm.complete(system=system, user=(text or "")[:500]) or "").strip().upper()
        return out.startswith("YES") or out == "Y" or "YES" in out.split()
    except Exception:
        logger.debug("AI cancel check unavailable", exc_info=True)
        return False


def is_cancel_message(text: str, *, use_ai: bool = True) -> bool:
    raw = (text or "").strip()
    if not raw:
        return False
    if looks_like_cancel_rule(raw):
        return True
    if _NOT_CANCEL.match(raw):
        return False
    if use_ai and looks_like_cancel_hint(raw):
        return ai_says_cancel(raw)
    return False


def _work_request_id_from_pending(pending: PendingAction) -> int | None:
    payload = dict(pending.pending_payload or {})
    for key in ("draft_id", "work_request_id", "request_id"):
        val = payload.get(key)
        if val:
            try:
                return int(val)
            except (TypeError, ValueError):
                continue
    offer_id = payload.get("offer_id")
    if offer_id:
        offer = WorkRequestOffer.objects.filter(pk=offer_id).first()
        if offer:
            return int(offer.work_request_id)
    return None


def cancel_work_flow(user: BotUser, pending: PendingAction) -> str:
    """Сбросить pending и при необходимости отменить черновик/раннюю заявку."""
    kind = pending.pending_kind or ""
    req_id = _work_request_id_from_pending(pending)
    pending.clear_pending()

    # Исполнитель на оффере: «отмена» = отказ от предложения, заявку клиента не трогаем
    if kind == "work_request_offer_reply" and req_id:
        offer = WorkRequestOffer.objects.filter(
            work_request_id=req_id,
            contractor__user=user,
            status=WorkRequestOfferStatus.OFFERED,
        ).first()
        if offer:
            offer.status = WorkRequestOfferStatus.DECLINED
            from django.utils import timezone

            offer.responded_at = timezone.now()
            offer.save(update_fields=["status", "responded_at"])
            try:
                from services.work_request_dispatch import try_dispatch_request

                try_dispatch_request(offer.work_request)
            except Exception:
                logger.exception("dispatch after cancel offer %s", offer.id)
        return "Ок, предложение отклонено. Заявку передадим другому исполнителю."

    req = WorkRequest.objects.filter(pk=req_id).first() if req_id else None
    cancelled_request = False
    # Ранние стадии заявки: отмена диалога = заявка не оформляется
    # Любая незавершённая заявка — отменяем целиком (в т.ч. после отчёта исполнителя)
    if req and req.status in {
        WorkRequestStatus.DRAFT,
        WorkRequestStatus.PENDING,
        WorkRequestStatus.OFFERING,
        WorkRequestStatus.SCHEDULING,
        WorkRequestStatus.IN_PROGRESS,
        WorkRequestStatus.AWAITING_CLIENT,
        WorkRequestStatus.AWAITING_COMMISSION,
    }:
        req.status = WorkRequestStatus.CANCELLED
        note = "Отменено пользователем в боте."
        admin_note = (req.admin_note or "").strip()
        if note not in admin_note:
            admin_note = f"{admin_note}\n{note}".strip() if admin_note else note
        req.admin_note = admin_note
        # Сбросить ожидание комиссии, чтобы исполнитель снова мог брать заказы
        from database.models import WorkRequestCommissionStatus

        req.commission_status = WorkRequestCommissionStatus.NONE
        req.save(
            update_fields=[
                "status",
                "admin_note",
                "commission_status",
                "updated_at",
            ]
        )
        WorkRequestOffer.objects.filter(
            work_request=req,
            status=WorkRequestOfferStatus.OFFERED,
        ).update(status=WorkRequestOfferStatus.CANCELLED)
        try:
            from services.work_request_completion import cancel_scheduled_for_request

            cancel_scheduled_for_request(req)
        except Exception:
            logger.exception("cancel scheduled for WR %s", req.id)
        cancelled_request = True

    if cancelled_request:
        return (
            "Ок, отменил.\n"
            "Чтобы снова вызвать мастера — напишите «вызвать мастера»."
        )
    return (
        "Ок, остановил текущий шаг по заявке.\n"
        "Если нужно продолжить позже — напишите снова."
    )


def maybe_cancel_work_flow(
    user: BotUser, text: str, pending: PendingAction, *, use_ai: bool = True
) -> str | None:
    """Если это отмена в диалоге заказа мастера — выполнить и вернуть ответ."""
    if pending.pending_kind not in WORK_FLOW_PENDING_KINDS:
        return None
    if not is_cancel_message(text, use_ai=use_ai):
        return None
    return cancel_work_flow(user, pending)
