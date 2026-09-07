"""Голосование по пожеланиям группы: раз в 2 недели топ-3 → 2 дня → задача на сбор."""
from __future__ import annotations

import logging
from collections import Counter
from datetime import timedelta
from typing import Any, Callable

from django.db.models import Count
from django.utils import timezone

from database.models import (
    AdminTaskKind,
    BotUser,
    NeighborhoodWish,
    PendingAction,
    ServiceCategory,
    ServiceGroup,
    WishBallot,
    WishBallotStatus,
    WishPeriod,
    WishPeriodStatus,
    WishTopic,
    WishVote,
)

logger = logging.getLogger(__name__)

PENDING_KIND = "wish_ballot_vote"
BALLOT_INTERVAL = timedelta(days=14)
VOTING_DURATION = timedelta(days=2)
TOP_OPTIONS = 3

_NO = {
    "нет",
    "no",
    "n",
    "-",
    "0",
    "не надо",
    "не нужно",
    "отказ",
    "пока нет",
    "не делаем",
}

# Тема пожелания → категория сбора (где есть прямое соответствие).
TOPIC_TO_CATEGORY = {
    WishTopic.ROAD: ServiceCategory.ROAD,
    WishTopic.PLAYGROUND: ServiceCategory.PLAYGROUND,
    WishTopic.LIGHTING: ServiceCategory.LIGHTING,
    WishTopic.SNOW: ServiceCategory.SNOW,
}


def ensure_open_period(group: ServiceGroup) -> WishPeriod:
    period = (
        WishPeriod.objects.filter(group=group, status=WishPeriodStatus.OPEN)
        .order_by("-opened_at")
        .first()
    )
    if period is not None:
        return period
    return WishPeriod.objects.create(group=group, status=WishPeriodStatus.OPEN)


def topic_to_category(topic: str) -> str:
    return TOPIC_TO_CATEGORY.get(topic, ServiceCategory.ROAD)


def build_top_options(period: WishPeriod, *, limit: int = TOP_OPTIONS) -> list[dict[str, Any]]:
    rows = (
        NeighborhoodWish.objects.filter(period=period)
        .values("topic")
        .annotate(count=Count("id"))
        .order_by("-count", "topic")[:limit]
    )
    labels = dict(WishTopic.choices)
    options: list[dict[str, Any]] = []
    for row in rows:
        topic = row["topic"]
        samples = list(
            NeighborhoodWish.objects.filter(period=period, topic=topic)
            .order_by("-created_at")
            .values_list("text", flat=True)[:3]
        )
        options.append(
            {
                "topic": topic,
                "label": labels.get(topic, topic),
                "wish_count": int(row["count"]),
                "samples": samples,
            }
        )
    return options


def ballot_message(ballot: WishBallot) -> str:
    lines = [
        f"Голосование по пожеланиям группы «{ballot.group.name}».",
        "",
        "Будем ли делать работу? Выберите один вариант:",
        "",
    ]
    for i, opt in enumerate(ballot.options or [], start=1):
        lines.append(f"{i}. {opt.get('label', opt.get('topic'))} ({opt.get('wish_count', 0)} пожеланий)")
        for sample in (opt.get("samples") or [])[:2]:
            lines.append(f"   — {str(sample)[:100]}")
        lines.append("")
    lines.append("0 / нет — пока не делаем")
    lines.append("")
    ends = timezone.localtime(ballot.voting_ends_at).strftime("%d.%m %H:%M")
    lines.append(f"Голосование до {ends}. Ответьте номером варианта.")
    return "\n".join(lines)


def _can_start_ballot(group: ServiceGroup, period: WishPeriod, now) -> bool:
    if WishBallot.objects.filter(
        group=group,
        status__in=[WishBallotStatus.VOTING, WishBallotStatus.WON],
    ).exists():
        return False
    options = build_top_options(period)
    if not options:
        return False
    last = (
        WishBallot.objects.filter(group=group)
        .exclude(status=WishBallotStatus.CANCELLED)
        .order_by("-started_at")
        .first()
    )
    if last is None:
        # Первый раунд — не раньше чем через 2 недели после открытия периода,
        # либо сразу если пожеланий уже достаточно и период «созрел».
        return (now - period.opened_at) >= BALLOT_INTERVAL
    return (now - last.started_at) >= BALLOT_INTERVAL


def start_ballot(
    group: ServiceGroup,
    *,
    send_fn: Callable | None = None,
    now=None,
    force: bool = False,
) -> WishBallot | None:
    now = now or timezone.now()
    period = ensure_open_period(group)
    if not force and not _can_start_ballot(group, period, now):
        return None
    options = build_top_options(period)
    if not options:
        return None
    if WishBallot.objects.filter(
        group=group, status__in=[WishBallotStatus.VOTING, WishBallotStatus.WON]
    ).exists():
        return None

    ballot = WishBallot.objects.create(
        group=group,
        period=period,
        status=WishBallotStatus.VOTING,
        options=options,
        voting_ends_at=now + VOTING_DURATION,
    )
    from services.outbox import KIND_WISH_BALLOT, deliver

    members = list(group.members.all())
    text = ballot_message(ballot)
    for user in members:
        pending, _ = PendingAction.objects.get_or_create(user=user)
        # Не перетираем занятый диалог — голос всё равно можно отдать позже,
        # когда pending освободится; текст уходит всегда.
        if not pending.pending_kind:
            pending.pending_kind = PENDING_KIND
            pending.pending_payload = {"ballot_id": ballot.id}
            pending.save(update_fields=["pending_kind", "pending_payload", "updated_at"])
        elif pending.pending_kind == PENDING_KIND:
            pending.pending_payload = {"ballot_id": ballot.id}
            pending.save(update_fields=["pending_payload", "updated_at"])
        deliver(
            user,
            text,
            kind=KIND_WISH_BALLOT,
            meta={"ballot_id": ballot.id, "group_id": group.id},
            send_fn=send_fn,
        )
    return ballot


def parse_vote_reply(text: str, options: list[dict]) -> tuple[bool, str] | None:
    """Вернуть (will_do, choice_topic) или None если не распознано."""
    raw = (text or "").strip().lower()
    if not raw:
        return None
    first = raw.split()[0].strip(".).,:;")
    if raw in _NO or first in _NO:
        return False, ""
    if first.isdigit():
        n = int(first)
        if n == 0:
            return False, ""
        if 1 <= n <= len(options):
            return True, str(options[n - 1].get("topic") or "")
    # По совпадению с подписью темы
    for opt in options:
        label = str(opt.get("label") or "").lower()
        topic = str(opt.get("topic") or "")
        if label and label in raw:
            return True, topic
        if topic and topic in raw:
            return True, topic
    return None


def handle_wish_ballot_vote(
    user: BotUser,
    text: str,
    pending: PendingAction,
) -> str | None:
    payload = pending.pending_payload or {}
    ballot_id = payload.get("ballot_id")
    if not ballot_id:
        pending.clear_pending()
        return "Голосование уже недоступно."
    ballot = (
        WishBallot.objects.select_related("group")
        .filter(pk=ballot_id)
        .first()
    )
    if ballot is None or ballot.status != WishBallotStatus.VOTING:
        pending.clear_pending()
        return "Это голосование уже завершено. Спасибо!"
    if timezone.now() > ballot.voting_ends_at:
        pending.clear_pending()
        return "Срок голосования истёк. Спасибо!"

    parsed = parse_vote_reply(text, ballot.options or [])
    if parsed is None:
        return (
            "Ответьте номером варианта (1, 2, 3) или «нет» / 0, "
            "если пока не делаем работу."
        )
    will_do, choice = parsed
    WishVote.objects.update_or_create(
        ballot=ballot,
        user=user,
        defaults={
            "choice_topic": choice,
            "will_do": will_do,
            "raw_reply": (text or "")[:255],
        },
    )
    pending.clear_pending()
    if not will_do:
        return "Принято: пока не делаем. Спасибо за ответ!"
    label = next(
        (o.get("label") for o in (ballot.options or []) if o.get("topic") == choice),
        choice,
    )
    return f"Ваш голос: «{label}». Спасибо!"


def vote_summary_table(ballot: WishBallot) -> list[dict[str, Any]]:
    """Сводная таблица голосов для панели."""
    options = list(ballot.options or [])
    counts = Counter(
        v.choice_topic
        for v in ballot.votes.filter(will_do=True)
        if v.choice_topic
    )
    no_count = ballot.votes.filter(will_do=False).count()
    rows = []
    for opt in options:
        topic = opt.get("topic")
        rows.append(
            {
                "topic": topic,
                "label": opt.get("label", topic),
                "wish_count": opt.get("wish_count", 0),
                "votes": counts.get(topic, 0),
            }
        )
    rows.append(
        {
            "topic": "",
            "label": "Нет / не делаем",
            "wish_count": "—",
            "votes": no_count,
        }
    )
    return rows


def _winner_summary(ballot: WishBallot, topic: str, label: str, votes: int) -> str:
    samples = []
    for opt in ballot.options or []:
        if opt.get("topic") == topic:
            samples = list(opt.get("samples") or [])[:3]
            break
    parts = [
        f"Группа «{ballot.group.name}»: победила тема «{label}» ({votes} голос.).",
    ]
    if samples:
        parts.append("Примеры пожеланий:")
        for s in samples:
            parts.append(f"• {s[:160]}")
    parts.append("Нужно организовать сбор на эти нужды.")
    return "\n".join(parts)


def create_organize_task(ballot: WishBallot) -> None:
    from panel.admin_tasks import upsert_task

    label = ballot.winner_label or ballot.winner_topic
    category = topic_to_category(ballot.winner_topic)
    action = (
        f"/panel/services/?wish_ballot={ballot.id}"
        f"&group_id={ballot.group_id}"
        f"&category={category}"
    )
    upsert_task(
        kind=AdminTaskKind.WISH_BALLOT,
        title=f"Сбор по голосованию: {label}",
        description=ballot.winner_summary,
        action_url=action,
        source_model="WishBallot",
        source_id=ballot.id,
        priority=20,
        meta={
            "ballot_id": ballot.id,
            "group_id": ballot.group_id,
            "topic": ballot.winner_topic,
            "category": category,
        },
        reopen_if_closed=False,
    )


def tally_ballot(ballot: WishBallot, *, now=None) -> WishBallot:
    now = now or timezone.now()
    if ballot.status != WishBallotStatus.VOTING:
        return ballot
    counts = Counter(
        v.choice_topic
        for v in ballot.votes.filter(will_do=True)
        if v.choice_topic
    )
    ballot.tallied_at = now
    if not counts:
        ballot.status = WishBallotStatus.CANCELLED
        ballot.winner_topic = ""
        ballot.winner_label = ""
        ballot.winner_summary = "Никто не выбрал вариант — сбор не назначаем."
        ballot.save(
            update_fields=[
                "status",
                "tallied_at",
                "winner_topic",
                "winner_label",
                "winner_summary",
            ]
        )
        return ballot

    winner_topic, votes = counts.most_common(1)[0]
    label = next(
        (o.get("label") for o in (ballot.options or []) if o.get("topic") == winner_topic),
        dict(WishTopic.choices).get(winner_topic, winner_topic),
    )
    ballot.status = WishBallotStatus.WON
    ballot.winner_topic = winner_topic
    ballot.winner_label = str(label)
    ballot.winner_summary = _winner_summary(ballot, winner_topic, str(label), votes)
    ballot.save(
        update_fields=[
            "status",
            "tallied_at",
            "winner_topic",
            "winner_label",
            "winner_summary",
        ]
    )
    create_organize_task(ballot)
    return ballot


def close_period_after_campaign(ballot: WishBallot, campaign) -> WishPeriod:
    """После создания сбора по итогам голосования — закрыть период и открыть новый."""
    from panel.admin_tasks import close_task_for_source

    now = timezone.now()
    ballot.campaign = campaign
    ballot.status = WishBallotStatus.COMPLETED
    ballot.save(update_fields=["campaign", "status"])
    close_task_for_source(AdminTaskKind.WISH_BALLOT, "WishBallot", ballot.id)

    period = ballot.period
    if period.status != WishPeriodStatus.CLOSED:
        period.status = WishPeriodStatus.CLOSED
        period.closed_at = now
        period.save(update_fields=["status", "closed_at"])
    return WishPeriod.objects.create(group=ballot.group, status=WishPeriodStatus.OPEN)


def process_wish_ballots(*, send_fn: Callable | None = None, now=None) -> dict[str, int]:
    """Тик планировщика: завершить голосования + стартовать новые раз в 2 недели."""
    now = now or timezone.now()
    stats = {"tallied": 0, "started": 0}
    due = WishBallot.objects.filter(
        status=WishBallotStatus.VOTING,
        voting_ends_at__lte=now,
    ).select_related("group", "period")
    for ballot in due:
        tally_ballot(ballot, now=now)
        stats["tallied"] += 1

    for group in ServiceGroup.objects.annotate(n=Count("members")).filter(n__gt=0):
        period = (
            WishPeriod.objects.filter(group=group, status=WishPeriodStatus.OPEN)
            .order_by("-opened_at")
            .first()
        )
        if period is None:
            continue
        ballot = start_ballot(group, send_fn=send_fn, now=now)
        if ballot is not None:
            stats["started"] += 1
    return stats
