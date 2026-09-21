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


def _annotate_users(qs=None):
    qs = qs if qs is not None else BotUser.objects.all()
    return qs.annotate(
        offered=Count(
            "service_invites",
            filter=~Q(service_invites__status=InviteStatus.CANCELLED),
            distinct=True,
        ),
        paid=Count(
            "service_invites",
            filter=Q(service_invites__status=InviteStatus.PAID),
            distinct=True,
        ),
    )


def _row_from_user(u: BotUser) -> CitizenRank:
    offered = int(getattr(u, "offered", None) or 0)
    paid = int(getattr(u, "paid", None) or 0)
    ratio = (paid / offered * 100.0) if offered else 0.0
    return CitizenRank(
        user=u,
        offered=offered,
        paid=paid,
        ratio=ratio,
        label=rank_label(ratio),
    )


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


def ranking_list(
    locality: str = "",
    q: str = "",
    *,
    only_with_offers: bool = False,
) -> list[CitizenRank]:
    """All bot users by default (0 предложений → 0%, неактивный)."""
    qs = _annotate_users()
    if only_with_offers:
        qs = qs.filter(offered__gt=0)
    if locality:
        qs = qs.filter(locality__icontains=locality.strip())
    if q:
        qs = qs.filter(
            Q(display_name__icontains=q)
            | Q(username__icontains=q)
            | Q(real_name__icontains=q)
            | Q(locality__icontains=q)
            | Q(max_user_id__icontains=q)
            | Q(phone__icontains=q)
        )
    rows = [_row_from_user(u) for u in qs]
    rows.sort(
        key=lambda r: (
            -r.ratio,
            -r.paid,
            -r.offered,
            (r.user.real_name or r.user.display_name or "").lower(),
        )
    )
    return rows


def sort_ranking_rows(rows: list[CitizenRank], sort: str = "-rating") -> list[CitizenRank]:
    reverse = sort.startswith("-")
    key = sort.lstrip("-") or "rating"

    def sort_key(r: CitizenRank):
        if key == "rating":
            return (r.ratio, r.paid, r.offered)
        if key == "offered":
            return (r.offered, r.ratio)
        if key == "paid":
            return (r.paid, r.ratio)
        if key == "locality":
            return ((r.user.locality or "").lower(),)
        if key == "name":
            return ((r.user.real_name or r.user.display_name or "").lower(),)
        if key == "seen":
            return (r.user.last_seen_at.timestamp() if r.user.last_seen_at else 0,)
        return (r.ratio,)

    return sorted(rows, key=sort_key, reverse=reverse)
