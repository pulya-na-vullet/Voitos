"""Оценка работы исполнителя жителем (1–5) после подтверждения суммы."""

from __future__ import annotations

import logging
import re

from django.db.models import Avg, Count
from django.utils import timezone

from database.models import (
    BotUser,
    ContractorProfile,
    PendingAction,
    WorkRequest,
    WorkRequestRating,
    WorkRequestStatus,
)

logger = logging.getLogger(__name__)

RATING_PENDING = "work_request_rating"
RATING_BACKFILL_BATCH = 15


def parse_score(text: str) -> int | None:
    raw = (text or "").strip().lower().replace(",", ".")
    if not raw:
        return None
    if re.fullmatch(r"[1-5]", raw):
        return int(raw)
    if re.fullmatch(r"[1-5]\s*(балл(а|ов)?|из\s*5)?", raw):
        return int(raw[0])
    m = re.search(r"(?<!\d)([1-5])(?!\d)", raw)
    if m and not re.search(r"\d+[.,]\d+", raw):
        return int(m.group(1))
    return None


def rating_ask_message(req: WorkRequest) -> str:
    name = "исполнителя"
    if req.assigned_contractor_id:
        name = str(req.assigned_contractor.user)
    return (
        f"Оцените работу {name} по заявке #{req.id}.\n"
        "Напишите оценку от 1 до 5, где 5 — отлично."
    )


def ask_client_for_rating(
    req: WorkRequest, *, send_fn=None, send: bool = True
) -> bool:
    """Поставить pending оценки; опционально отправить отдельное сообщение."""
    req = (
        WorkRequest.objects.select_related(
            "assigned_contractor", "assigned_contractor__user", "user"
        )
        .filter(pk=req.pk)
        .first()
    )
    if not req or not req.assigned_contractor_id:
        return False
    if WorkRequestRating.objects.filter(work_request_id=req.id).exists():
        return False

    client = req.user
    if send:
        text = rating_ask_message(req)
        if send_fn is None:
            from services.work_request_dispatch import _default_send_fn

            send_fn = _default_send_fn()
        try:
            send_fn(client, text)
        except Exception:
            logger.exception("Failed to ask rating WR %s", req.id)
            return False

    pending, _ = PendingAction.objects.get_or_create(user=client)
    if not pending.pending_kind or pending.pending_kind in {
        RATING_PENDING,
        "work_request_client_confirm",
    }:
        pending.pending_kind = RATING_PENDING
        pending.pending_payload = {"work_request_id": req.id, "step": "score"}
        pending.save(update_fields=["pending_kind", "pending_payload", "updated_at"])

    WorkRequest.objects.filter(pk=req.id).update(rating_asked_at=timezone.now())
    return True


def handle_rating_step(user: BotUser, text: str, pending: PendingAction) -> str:
    if pending.pending_kind != RATING_PENDING:
        return "Сейчас оценка работы не ожидается."
    payload = dict(pending.pending_payload or {})
    req = (
        WorkRequest.objects.select_related(
            "assigned_contractor", "assigned_contractor__user"
        )
        .filter(pk=payload.get("work_request_id"))
        .first()
    )
    if not req or req.user_id != user.id or not req.assigned_contractor_id:
        pending.clear_pending()
        return "Заявка для оценки не найдена."
    if WorkRequestRating.objects.filter(work_request_id=req.id).exists():
        pending.clear_pending()
        return "Вы уже оценили эту заявку. Спасибо!"

    step = payload.get("step") or "score"
    raw = (text or "").strip()

    if step == "score":
        score = parse_score(raw)
        if score is None:
            return "Нужна оценка числом от 1 до 5 (5 — максимум)."
        payload["score"] = score
        payload["step"] = "comment"
        pending.pending_payload = payload
        pending.save(update_fields=["pending_payload", "updated_at"])
        return (
            f"Оценка {score}/5 принята.\n"
            "Напишите короткий комментарий о работе "
            "(или «пропустить», если без комментария)."
        )

    if step == "comment":
        score = int(payload.get("score") or 0)
        if score < 1 or score > 5:
            payload["step"] = "score"
            pending.pending_payload = payload
            pending.save(update_fields=["pending_payload", "updated_at"])
            return "Сначала напишите оценку от 1 до 5."
        lower = raw.lower()
        if lower in {"пропустить", "skip", "-", "нет", "без комментария", "не надо"}:
            comment = ""
        else:
            comment = raw[:2000]
        WorkRequestRating.objects.create(
            work_request=req,
            contractor=req.assigned_contractor,
            client=user,
            score=score,
            comment=comment,
        )
        pending.clear_pending()
        return (
            f"Спасибо! Сохранили оценку {score}/5"
            + (" и комментарий." if comment else ".")
        )

    pending.clear_pending()
    return "Оценка завершена."


def contractor_rating_stats(contractor: ContractorProfile) -> dict:
    agg = contractor.work_ratings.aggregate(avg=Avg("score"), cnt=Count("id"))
    avg = agg["avg"]
    return {
        "avg": round(float(avg), 2) if avg is not None else None,
        "count": int(agg["cnt"] or 0),
        "ratings": list(
            contractor.work_ratings.select_related(
                "client", "work_request", "work_request__role"
            ).order_by("-created_at")[:50]
        ),
    }


def closed_requests_needing_rating_ask(limit: int = RATING_BACKFILL_BATCH):
    return (
        WorkRequest.objects.filter(
            assigned_contractor__isnull=False,
            confirmed_amount__isnull=False,
            rating_asked_at__isnull=True,
            status__in=[
                WorkRequestStatus.AWAITING_COMMISSION,
                WorkRequestStatus.DONE,
            ],
            rating__isnull=True,
        )
        .select_related("user", "assigned_contractor", "assigned_contractor__user")
        .order_by("id")[:limit]
    )


def backfill_rating_asks(*, send_fn=None, limit: int = RATING_BACKFILL_BATCH) -> int:
    """Спросить у клиентов оценку по уже закрытым заявкам (до фичи)."""
    n = 0
    for req in closed_requests_needing_rating_ask(limit=limit):
        if ask_client_for_rating(req, send_fn=send_fn):
            n += 1
    return n
