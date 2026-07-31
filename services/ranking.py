from __future__ import annotations

from dataclasses import dataclass

from django.db.models import Count, Q

from database.models import BotUser, InviteStatus


@dataclass
class CitizenRank:
    user: BotUser
    offered: int
    paid: int
    ratio: float
    label: str


def rank_label(ratio_percent: float) -> str:
    if ratio_percent >= 80:
        return "Образцовый гражданин"
    if ratio_percent >= 60:
        return "Хороший гражданин"
    if ratio_percent >= 40:
        return "Пассивный гражданин"
    return "Неактивный гражданин"


def citizen_stats(user: BotUser) -> CitizenRank:
    offered = user.service_invites.exclude(status=InviteStatus.CANCELLED).count()
    paid = user.service_invites.filter(status=InviteStatus.PAID).count()
    ratio = (paid / offered * 100.0) if offered else 0.0
    return CitizenRank(
        user=user,
        offered=offered,
        paid=paid,
        ratio=ratio,
        label=rank_label(ratio),
    )


def ranking_list(locality: str = "") -> list[CitizenRank]:
    qs = BotUser.objects.annotate(
        offered=Count(
            "service_invites",
            filter=~Q(service_invites__status=InviteStatus.CANCELLED),
        ),
        paid=Count(
            "service_invites",
            filter=Q(service_invites__status=InviteStatus.PAID),
        ),
    ).filter(offered__gt=0)
    if locality:
        qs = qs.filter(locality__iexact=locality.strip())
    rows: list[CitizenRank] = []
    for u in qs:
        offered = int(u.offered or 0)
        paid = int(u.paid or 0)
        ratio = (paid / offered * 100.0) if offered else 0.0
        rows.append(
            CitizenRank(
                user=u,
                offered=offered,
                paid=paid,
                ratio=ratio,
                label=rank_label(ratio),
            )
        )
    rows.sort(key=lambda r: (-r.ratio, -r.paid, r.user.real_name or ""))
    return rows
