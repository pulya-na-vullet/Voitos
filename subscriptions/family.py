from __future__ import annotations

import logging
from typing import Iterable

from django.db import transaction
from django.utils import timezone

from database.models import ActivityKind, ActivityLog, AdminTask, BotUser, ServiceGroup

logger = logging.getLogger(__name__)


def pick_family_payer(members: Iterable[BotUser]) -> BotUser | None:
    """Prefer the member with the farthest active/own subscription_until."""
    people = [m for m in members if m is not None]
    if not people:
        return None
    now = timezone.now()

    def sort_key(u: BotUser):
        until = u.subscription_until
        active = 1 if until and until > now else 0
        ts = until.timestamp() if until else 0.0
        return (active, ts, -u.id)

    return max(people, key=sort_key)


@transaction.atomic
def link_family_members(
    members: Iterable[BotUser | int],
    *,
    payer: BotUser | int | None = None,
    note: str = "",
) -> BotUser | None:
    """
    Link household members so dependents inherit the payer's subscription.

    Returns the chosen payer (or None if fewer than 2 members).
    """
    ids: list[int] = []
    for m in members:
        if m is None:
            continue
        ids.append(m if isinstance(m, int) else m.id)
    # Preserve order, unique
    seen: set[int] = set()
    uniq_ids: list[int] = []
    for i in ids:
        if i in seen:
            continue
        seen.add(i)
        uniq_ids.append(i)
    if len(uniq_ids) < 2:
        return None

    people = list(BotUser.objects.filter(id__in=uniq_ids).select_related("family_payer"))
    by_id = {u.id: u for u in people}
    ordered = [by_id[i] for i in uniq_ids if i in by_id]
    if len(ordered) < 2:
        return None

    if payer is not None:
        payer_id = payer if isinstance(payer, int) else payer.id
        chosen = by_id.get(payer_id) or pick_family_payer(ordered)
    else:
        chosen = pick_family_payer(ordered)
    if chosen is None:
        return None

    # Clear payer link on the sponsor; point everyone else at them.
    if chosen.family_payer_id:
        chosen.family_payer = None
        chosen.save(update_fields=["family_payer", "last_seen_at"])

    for u in ordered:
        if u.id == chosen.id:
            continue
        if u.family_payer_id == chosen.id:
            continue
        u.family_payer = chosen
        u.save(update_fields=["family_payer", "last_seen_at"])
        ActivityLog.objects.create(
            user=u,
            kind=ActivityKind.PROFILE_VERIFIED,
            title="Семейная подписка",
            detail=(
                f"Доступ через {chosen} до "
                f"{timezone.localtime(chosen.subscription_until).strftime('%d.%m.%Y') if chosen.subscription_until else '—'}."
                + (f" {note}" if note else "")
            ),
            meta={"family_payer_id": chosen.id, "member_ids": uniq_ids},
        )

    _ensure_service_group(ordered, chosen)
    try:
        from subscriptions.renewal_reminders import schedule_subscription_renewal_reminders

        # Пересоздать напоминания плательщику и всем прикреплённым (в т.ч. Елене).
        schedule_subscription_renewal_reminders(chosen)
    except Exception:
        logger.exception("Failed to schedule family renewal reminders for %s", chosen.id)
    return chosen


def _ensure_service_group(members: list[BotUser], payer: BotUser) -> None:
    """Put household into one ServiceGroup for collections when possible."""
    try:
        existing = None
        for u in members:
            g = u.service_groups.order_by("id").first()
            if g:
                existing = g
                break
        if existing is None:
            locality = next((m.locality for m in members if m.locality), "")
            name = f"Семья: {payer}"
            if locality:
                name = f"{name} ({locality})"
            existing = ServiceGroup.objects.create(
                name=name[:255],
                description="Автогруппа по подтверждённой семейной заявке",
            )
        for u in members:
            existing.members.add(u)
    except Exception:
        logger.exception("Failed to sync ServiceGroup for family of %s", payer.id)


def confirm_family_from_task(task: AdminTask) -> BotUser | None:
    """
    Apply family link from a FAMILY_CLAIM admin task.

    Only links when the user claimed to be family (claimed_family=True).
    """
    meta = task.meta or {}
    if not meta.get("claimed_family"):
        return None
    claimant = task.user
    if claimant is None:
        return None
    candidate_ids = list(meta.get("candidate_user_ids") or [])
    members = [claimant.id, *candidate_ids]
    return link_family_members(
        members,
        note=f"Подтверждено задачей #{task.id}",
    )


def unlink_family_member(user: BotUser) -> None:
    user.family_payer = None
    user.save(update_fields=["family_payer", "last_seen_at"])
    ActivityLog.objects.create(
        user=user,
        kind=ActivityKind.SETTINGS,
        title="Семейная подписка снята",
        detail="Отвязан от оплаты члена семьи",
    )
    try:
        from subscriptions.renewal_reminders import cancel_subscription_renewal_reminders

        cancel_subscription_renewal_reminders(user)
    except Exception:
        logger.exception("Failed to cancel renewal reminders after unlink for %s", user.id)
