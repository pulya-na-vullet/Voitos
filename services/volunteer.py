"""Обязательный ответ жителей: поможет ли на площадке / ремонте дороги."""
from __future__ import annotations

import logging

from django.utils import timezone

from database.models import (
    ActivityKind,
    ActivityLog,
    BotUser,
    PendingAction,
    ServiceCampaign,
    ServiceCategory,
    VolunteerHelpAsk,
    VolunteerReplyStatus,
)

logger = logging.getLogger(__name__)

VOLUNTEER_CATEGORIES = {
    ServiceCategory.PLAYGROUND,
    ServiceCategory.ROAD,
}

PENDING_KIND = "volunteer_help_reply"

_YES = {"да", "yes", "y", "+", "ага", "угу", "помогу", "согласен", "ок", "1"}
_NO = {"нет", "no", "n", "-", "не помогу", "отказ", "отказаться", "2"}

YES_BONUS = 5


def is_volunteer_category(category: str) -> bool:
    return category in VOLUNTEER_CATEGORIES


def decline_penalty(decline_number: int) -> int:
    """Штраф за N-й отказ (1-based)."""
    if decline_number <= 1:
        return 1
    if decline_number == 2:
        return 3
    if decline_number == 3:
        return 5
    if decline_number == 4:
        return 10
    return 15


def clamp_score(value: int) -> int:
    return max(0, min(100, int(value)))


def volunteer_ask_message(campaign: ServiceCampaign) -> str:
    return (
        f"Мероприятие «{campaign.title}» "
        f"({campaign.get_category_display()}).\n\n"
        "Поможете ли вы в этом мероприятии?\n"
        "Ответ обязателен:\n"
        "1 / да — помогу\n"
        "2 / нет — не смогу"
    )


def ask_volunteer_help(
    campaign: ServiceCampaign,
    user: BotUser,
    *,
    send_fn=None,
) -> VolunteerHelpAsk | None:
    """Создать вопрос о помощи и поставить pending, если категория подходящая."""
    if not is_volunteer_category(campaign.category):
        return None

    ask, created = VolunteerHelpAsk.objects.get_or_create(
        campaign=campaign,
        user=user,
        defaults={"status": VolunteerReplyStatus.PENDING},
    )
    if not created and ask.status != VolunteerReplyStatus.PENDING:
        return ask

    ActivityLog.objects.create(
        user=user,
        kind=ActivityKind.VOLUNTEER_ASK,
        title="Вопрос о помощи на мероприятии",
        detail=campaign.title,
        meta={"campaign_id": campaign.id, "ask_id": ask.id},
    )

    pending, _ = PendingAction.objects.get_or_create(user=user)
    # Не перетираем активный ответ по другому сбору — очередь подхватит позже.
    if pending.pending_kind == PENDING_KIND:
        payload = pending.pending_payload or {}
        if payload.get("ask_id") == ask.id:
            return ask
        queue = list(payload.get("queue") or [])
        if ask.id not in queue and payload.get("ask_id") != ask.id:
            queue.append(ask.id)
            payload["queue"] = queue
            pending.pending_payload = payload
            pending.save(update_fields=["pending_payload", "updated_at"])
        return ask

    # Не перетираем регистрацию / ответ подрядчика / выбор чека.
    if pending.pending_kind and pending.pending_kind not in {"", PENDING_KIND}:
        # Сохраняем ask; активируем диалог, когда текущий pending освободится —
        # пользователь всё равно получит текст вопроса.
        pass
    else:
        pending.pending_kind = PENDING_KIND
        pending.pending_payload = {"ask_id": ask.id, "queue": []}
        pending.save(update_fields=["pending_kind", "pending_payload", "updated_at"])

    text = volunteer_ask_message(campaign)
    if send_fn:
        try:
            send_fn(user, text)
        except Exception:
            logger.exception("Failed to ask volunteer help user %s", user.max_user_id)
    return ask


def ask_volunteer_help_for_users(
    campaign: ServiceCampaign,
    users,
    *,
    send_fn=None,
) -> int:
    n = 0
    for user in users:
        if ask_volunteer_help(campaign, user, send_fn=send_fn):
            n += 1
    return n


def _activate_next_ask(user: BotUser, pending: PendingAction, *, send_fn=None) -> str | None:
    payload = pending.pending_payload or {}
    queue = list(payload.get("queue") or [])
    while queue:
        next_id = queue.pop(0)
        nxt = (
            VolunteerHelpAsk.objects.select_related("campaign")
            .filter(
                pk=next_id,
                user=user,
                status=VolunteerReplyStatus.PENDING,
            )
            .first()
        )
        if nxt is None:
            continue
        pending.pending_kind = PENDING_KIND
        pending.pending_payload = {"ask_id": nxt.id, "queue": queue}
        pending.save(update_fields=["pending_kind", "pending_payload", "updated_at"])
        msg = volunteer_ask_message(nxt.campaign)
        if send_fn:
            try:
                send_fn(user, msg)
            except Exception:
                logger.exception("Failed to send next volunteer ask")
        return msg

    # Есть ли ещё необработанные asks без очереди?
    nxt = (
        VolunteerHelpAsk.objects.select_related("campaign")
        .filter(user=user, status=VolunteerReplyStatus.PENDING)
        .order_by("asked_at", "id")
        .first()
    )
    if nxt:
        pending.pending_kind = PENDING_KIND
        pending.pending_payload = {"ask_id": nxt.id, "queue": []}
        pending.save(update_fields=["pending_kind", "pending_payload", "updated_at"])
        msg = volunteer_ask_message(nxt.campaign)
        if send_fn:
            try:
                send_fn(user, msg)
            except Exception:
                logger.exception("Failed to send next volunteer ask")
        return msg

    pending.clear_pending()
    return None


def apply_volunteer_yes(user: BotUser, ask: VolunteerHelpAsk) -> int:
    delta = YES_BONUS
    user.citizen_score = clamp_score(user.citizen_score + delta)
    user.save(update_fields=["citizen_score"])
    ask.status = VolunteerReplyStatus.YES
    ask.score_delta = delta
    ask.responded_at = timezone.now()
    ask.save(update_fields=["status", "score_delta", "responded_at"])
    return delta


def apply_volunteer_no(user: BotUser, ask: VolunteerHelpAsk) -> int:
    prior = VolunteerHelpAsk.objects.filter(
        user=user, status=VolunteerReplyStatus.NO
    ).count()
    decline_number = prior + 1
    penalty = decline_penalty(decline_number)
    delta = -penalty
    user.citizen_score = clamp_score(user.citizen_score + delta)
    user.save(update_fields=["citizen_score"])
    ask.status = VolunteerReplyStatus.NO
    ask.score_delta = delta
    ask.responded_at = timezone.now()
    ask.save(update_fields=["status", "score_delta", "responded_at"])
    return delta


def handle_volunteer_help_reply(
    user: BotUser,
    text: str,
    pending: PendingAction,
    *,
    send_fn=None,
) -> str | None:
    payload = pending.pending_payload or {}
    ask_id = payload.get("ask_id")
    if not ask_id:
        pending.clear_pending()
        return None
    ask = (
        VolunteerHelpAsk.objects.select_related("campaign")
        .filter(pk=ask_id, user=user)
        .first()
    )
    if ask is None:
        pending.clear_pending()
        return None
    if ask.status != VolunteerReplyStatus.PENDING:
        next_msg = _activate_next_ask(user, pending, send_fn=send_fn)
        if next_msg:
            return "Этот вопрос уже закрыт.\n\n" + next_msg
        return "Этот вопрос уже закрыт."

    raw = (text or "").strip().lower()
    if raw in _YES:
        apply_volunteer_yes(user, ask)
        ActivityLog.objects.create(
            user=user,
            kind=ActivityKind.VOLUNTEER_REPLY,
            title="Согласие помочь на мероприятии",
            detail=ask.campaign.title,
            meta={
                "ask_id": ask.id,
                "campaign_id": ask.campaign_id,
                "answer": "yes",
            },
        )
        base = (
            f"Спасибо! Записали, что вы поможете на «{ask.campaign.title}»."
        )
    elif raw in _NO:
        apply_volunteer_no(user, ask)
        ActivityLog.objects.create(
            user=user,
            kind=ActivityKind.VOLUNTEER_REPLY,
            title="Отказ помочь на мероприятии",
            detail=ask.campaign.title,
            meta={
                "ask_id": ask.id,
                "campaign_id": ask.campaign_id,
                "answer": "no",
            },
        )
        base = (
            f"Принято: на «{ask.campaign.title}» вы не сможете помочь."
        )
    else:
        return (
            "Ответьте, пожалуйста, обязательно:\n"
            "1 / да — помогу\n"
            "2 / нет — не смогу"
        )

    next_msg = _activate_next_ask(user, pending, send_fn=send_fn)
    if next_msg:
        return base + "\n\n" + next_msg
    return base
