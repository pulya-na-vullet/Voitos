"""Назначение жителей группы исполнителями (детская площадка и т.п.)."""
from __future__ import annotations

import logging

from django.db.models import Max
from django.utils import timezone

from database.models import (
    ActivityKind,
    ActivityLog,
    BotUser,
    CampaignResidentHelper,
    ResidentHelperStatus,
    ServiceCampaign,
    ServiceCategory,
    VolunteerReplyStatus,
)
from services.contractors import max_profile_link

logger = logging.getLogger(__name__)

RESIDENT_HELPER_CATEGORIES = {
    ServiceCategory.PLAYGROUND,
}


def uses_resident_helpers(campaign: ServiceCampaign) -> bool:
    return campaign.category in RESIDENT_HELPER_CATEGORIES


def _fmt_dt(dt) -> str:
    if not dt:
        return "—"
    return timezone.localtime(dt).strftime("%d.%m.%Y %H:%M")


def group_members_for_helper_pick(campaign: ServiceCampaign) -> list[BotUser]:
    """Участники группы сбора, ещё не назначенные активными исполнителями."""
    if not campaign.group_id:
        return []
    assigned_ids = set(
        campaign.resident_helpers.filter(status=ResidentHelperStatus.ASSIGNED).values_list(
            "user_id", flat=True
        )
    )
    members = list(
        campaign.group.members.exclude(id__in=assigned_ids).order_by(
            "real_name", "display_name", "id"
        )
    )
    # Сначала те, кто ответил «помогу».
    yes_ids = set(
        campaign.volunteer_asks.filter(status=VolunteerReplyStatus.YES).values_list(
            "user_id", flat=True
        )
    )
    members.sort(key=lambda u: (0 if u.id in yes_ids else 1, str(u).lower()))
    return members


def helper_assign_message(helper: CampaignResidentHelper) -> str:
    c = helper.campaign
    when = _fmt_dt(c.event_at)
    return (
        f"Вас назначили исполнителем на мероприятие группы.\n"
        f"Задача: {c.title}\n"
        f"Категория: {c.get_category_display()}\n"
        f"Время: {when}\n"
        f"{(c.description or '').strip()}"
    ).strip()


def _helper_contact_block(helper: CampaignResidentHelper) -> list[str]:
    user = helper.user
    phone = (user.phone or "").strip()
    uname = (user.username or "").strip().lstrip("@")
    link = max_profile_link(user)
    lines = [str(user)]
    if phone:
        lines.append(f"Телефон: {phone}")
    if uname:
        lines.append(f"MAX: @{uname}")
    if link:
        lines.append(f"Ссылка MAX: {link}")
    return lines


def notify_resident_helper_peers(campaign: ServiceCampaign, *, send_fn=None) -> int:
    active = list(
        campaign.resident_helpers.filter(status=ResidentHelperStatus.ASSIGNED)
        .select_related("user")
        .order_by("sort_order", "id")
    )
    if len(active) < 2 or not send_fn:
        return 0
    sent = 0
    for helper in active:
        peers = [h for h in active if h.id != helper.id]
        lines = [
            f"На задаче «{campaign.title}» несколько исполнителей из группы.",
            "Контакты коллег для связи:",
            "",
        ]
        for i, peer in enumerate(peers, start=1):
            block = _helper_contact_block(peer)
            lines.append(f"{i}. {block[0]}")
            lines.extend(block[1:])
            lines.append("")
        text = "\n".join(lines).rstrip()
        try:
            send_fn(helper.user, text)
            sent += 1
        except Exception:
            logger.exception("Failed peer notify for resident helper %s", helper.id)
    return sent


def assign_resident_helper(
    campaign: ServiceCampaign,
    user: BotUser,
    *,
    send_fn=None,
) -> CampaignResidentHelper:
    if not uses_resident_helpers(campaign):
        raise ValueError("Для этой категории назначают исполнителей с техникой")
    if not campaign.group_id:
        raise ValueError("У сбора нет группы")
    if not campaign.group.members.filter(pk=user.pk).exists():
        raise ValueError("Пользователь не состоит в группе этого сбора")

    order = (
        campaign.resident_helpers.aggregate(m=Max("sort_order")).get("m") or 0
    ) + 1
    helper, created = CampaignResidentHelper.objects.get_or_create(
        campaign=campaign,
        user=user,
        defaults={
            "status": ResidentHelperStatus.ASSIGNED,
            "sort_order": order,
        },
    )
    if not created:
        if helper.status == ResidentHelperStatus.ASSIGNED:
            raise ValueError("Этот житель уже назначен исполнителем")
        helper.status = ResidentHelperStatus.ASSIGNED
        helper.sort_order = order
        helper.cancelled_at = None
        helper.save(update_fields=["status", "sort_order", "cancelled_at"])

    ActivityLog.objects.create(
        user=user,
        kind=ActivityKind.SERVICE_NOTICE,
        title="Назначен исполнителем из группы",
        detail=campaign.title,
        meta={"campaign_id": campaign.id, "helper_id": helper.id},
    )
    if send_fn:
        try:
            send_fn(user, helper_assign_message(helper))
        except Exception:
            logger.exception("Failed to notify resident helper %s", user.id)
        try:
            notify_resident_helper_peers(campaign, send_fn=send_fn)
        except Exception:
            logger.exception("Failed resident helper peer share")
    return helper


def cancel_resident_helper(
    helper: CampaignResidentHelper,
    *,
    send_fn=None,
) -> None:
    helper.status = ResidentHelperStatus.CANCELLED
    helper.cancelled_at = timezone.now()
    helper.save(update_fields=["status", "cancelled_at"])
    if send_fn:
        try:
            send_fn(
                helper.user,
                f"Назначение исполнителем на «{helper.campaign.title}» снято администратором.",
            )
        except Exception:
            logger.exception("Failed to notify cancelled resident helper")
