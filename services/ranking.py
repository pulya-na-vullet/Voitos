from __future__ import annotations

from dataclasses import dataclass

from django.db.models import Count, Q

from database.models import (
    BotUser,
    InviteStatus,
    ServiceCategory,
    VolunteerReplyStatus,
)


@dataclass
class CitizenRank:
    user: BotUser
    score: int
    help_asked: int
    help_yes: int
    help_ratio: float
    playground_helps: int
    road_helps: int
    offered: int
    paid: int
    payment_ratio: float
    label: str

    @property
    def ratio(self) -> float:
        """Совместимость со старыми шаблонами: основной показатель — баллы."""
        return float(self.score)


def rank_label(score: float) -> str:
    if score >= 80:
        return "Образцовый гражданин"
    if score >= 60:
        return "Хороший гражданин"
    if score >= 40:
        return "Пассивный гражданин"
    return "Неактивный гражданин"


def _annotate_users(qs=None):
    qs = qs if qs is not None else BotUser.objects.all()
    return qs.select_related("family_payer").annotate(
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
        help_asked=Count("volunteer_asks", distinct=True),
        help_yes=Count(
            "volunteer_asks",
            filter=Q(volunteer_asks__status=VolunteerReplyStatus.YES),
            distinct=True,
        ),
        playground_helps=Count(
            "volunteer_asks",
            filter=Q(
                volunteer_asks__status=VolunteerReplyStatus.YES,
                volunteer_asks__campaign__category=ServiceCategory.PLAYGROUND,
            ),
            distinct=True,
        ),
        road_helps=Count(
            "volunteer_asks",
            filter=Q(
                volunteer_asks__status=VolunteerReplyStatus.YES,
                volunteer_asks__campaign__category=ServiceCategory.ROAD,
            ),
            distinct=True,
        ),
    )


def _row_from_user(u: BotUser) -> CitizenRank:
    offered = int(getattr(u, "offered", None) or 0)
    paid = int(getattr(u, "paid", None) or 0)
    help_asked = int(getattr(u, "help_asked", None) or 0)
    help_yes = int(getattr(u, "help_yes", None) or 0)
    playground_helps = int(getattr(u, "playground_helps", None) or 0)
    road_helps = int(getattr(u, "road_helps", None) or 0)
    score = int(getattr(u, "citizen_score", None) or 100)
    payment_ratio = (paid / offered * 100.0) if offered else 0.0
    help_ratio = (help_yes / help_asked * 100.0) if help_asked else 0.0
    return CitizenRank(
        user=u,
        score=score,
        help_asked=help_asked,
        help_yes=help_yes,
        help_ratio=help_ratio,
        playground_helps=playground_helps,
        road_helps=road_helps,
        offered=offered,
        paid=paid,
        payment_ratio=payment_ratio,
        label=rank_label(score),
    )


def citizen_stats(user: BotUser) -> CitizenRank:
    u = _annotate_users(BotUser.objects.filter(pk=user.pk)).first()
    if u is None:
        return CitizenRank(
            user=user,
            score=int(getattr(user, "citizen_score", 100) or 100),
            help_asked=0,
            help_yes=0,
            help_ratio=0.0,
            playground_helps=0,
            road_helps=0,
            offered=0,
            paid=0,
            payment_ratio=0.0,
            label=rank_label(int(getattr(user, "citizen_score", 100) or 100)),
        )
    return _row_from_user(u)


def ranking_list(
    locality: str = "",
    q: str = "",
    *,
    only_with_offers: bool = False,
    user_ids: set[int] | list[int] | None = None,
) -> list[CitizenRank]:
    """Все пользователи бота. По умолчанию балл 100 (образцовый)."""
    qs = _annotate_users()
    if user_ids is not None:
        qs = qs.filter(id__in=user_ids)
    if only_with_offers:
        qs = qs.filter(Q(offered__gt=0) | Q(help_asked__gt=0))
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
            -r.score,
            -r.help_ratio,
            -r.help_yes,
            (r.user.real_name or r.user.display_name or "").lower(),
        )
    )
    return rows


def sort_ranking_rows(rows: list[CitizenRank], sort: str = "-rating") -> list[CitizenRank]:
    reverse = sort.startswith("-")
    key = sort.lstrip("-") or "rating"

    def sort_key(r: CitizenRank):
        if key == "rating":
            return (r.score, r.help_ratio, r.help_yes)
        if key == "help":
            return (r.help_ratio, r.help_yes, r.score)
        if key == "playground":
            return (r.playground_helps, r.score)
        if key == "road":
            return (r.road_helps, r.score)
        if key == "offered":
            return (r.offered, r.score)
        if key == "paid":
            return (r.paid, r.score)
        if key == "locality":
            return ((r.user.locality or "").lower(),)
        if key == "name":
            return ((r.user.real_name or r.user.display_name or "").lower(),)
        if key == "seen":
            return (r.user.last_seen_at.timestamp() if r.user.last_seen_at else 0,)
        return (r.score,)

    return sorted(rows, key=sort_key, reverse=reverse)
