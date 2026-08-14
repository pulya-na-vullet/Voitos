"""Прогноз заработка по подпискам на следующий месяц."""
from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from django.db.models import Count, Prefetch, Sum
from django.utils import timezone

from database.models import (
    AppSettings,
    BotUser,
    ServiceGroup,
    WorkRequest,
    WorkRequestCommissionStatus,
)
from services.clients_map import _expand_with_family, build_households, is_paying_user


ZERO = Decimal("0.00")


def _money(value: Decimal | int | float | None) -> Decimal:
    if value is None:
        return ZERO
    return Decimal(value).quantize(Decimal("0.01"))


def subscription_price() -> Decimal:
    cfg = AppSettings.load()
    return _money(cfg.subscription_price_rub or 100)


def next_month_bounds(now: datetime | None = None) -> tuple[datetime, datetime, str]:
    """Начало и конец следующего календарного месяца (локальное время)."""
    local = timezone.localtime(now or timezone.now())
    if local.month == 12:
        year, month = local.year + 1, 1
    else:
        year, month = local.year, local.month + 1
    start = local.replace(
        year=year, month=month, day=1, hour=0, minute=0, second=0, microsecond=0
    )
    last_day = monthrange(year, month)[1]
    end = start.replace(day=last_day, hour=23, minute=59, second=59, microsecond=999999)
    label = start.strftime("%B %Y")
    # Русские названия месяцев
    months_ru = (
        "",
        "январь",
        "февраль",
        "март",
        "апрель",
        "май",
        "июнь",
        "июль",
        "август",
        "сентябрь",
        "октябрь",
        "ноябрь",
        "декабрь",
    )
    label = f"{months_ru[month]} {year}"
    return start, end, label


@dataclass
class PayerRow:
    user: BotUser
    until: datetime
    labels: list[str]
    group_names: list[str]
    renews_next_month: bool


def _payer_rows() -> list[PayerRow]:
    now = timezone.now()
    groups = list(
        ServiceGroup.objects.prefetch_related(
            Prefetch(
                "members",
                queryset=BotUser.objects.select_related("family_payer").order_by("id"),
            )
        ).order_by("name", "id")
    )
    group_names_by_user: dict[int, list[str]] = {}
    all_users: dict[int, BotUser] = {}

    for group in groups:
        members = list(group.members.all())
        users = _expand_with_family(members)
        for u in users:
            all_users[int(u.id)] = u
            group_names_by_user.setdefault(int(u.id), [])
            if group.name not in group_names_by_user[int(u.id)]:
                group_names_by_user[int(u.id)].append(group.name)

    # Плательщики вне групп тоже учитываем.
    for u in BotUser.objects.filter(is_active=True, family_payer__isnull=True).select_related(
        "family_payer"
    ):
        all_users.setdefault(int(u.id), u)

    # Подтянуть dependents для подписей.
    expanded = _expand_with_family(list(all_users.values()))
    households = build_households(expanded)
    start, end, _ = next_month_bounds()

    rows: list[PayerRow] = []
    for root_id, hh in households.items():
        root = next((m for m in hh["members"] if int(m.id) == root_id), hh["members"][0])
        if not is_paying_user(root):
            continue
        until = root.subscription_until
        assert until is not None
        gnames: list[str] = []
        for m in hh["members"]:
            for name in group_names_by_user.get(int(m.id), []):
                if name not in gnames:
                    gnames.append(name)
        renews = start <= until <= end
        rows.append(
            PayerRow(
                user=root,
                until=until,
                labels=hh["labels"],
                group_names=gnames or ["Без группы"],
                renews_next_month=renews,
            )
        )

    rows.sort(key=lambda r: (r.until, r.labels[0].lower() if r.labels else ""))
    return rows


def build_commission_stats() -> dict[str, Any]:
    """Статистика комиссий 10% с исполнителей по заявкам."""
    qs = WorkRequest.objects.exclude(
        commission_status=WorkRequestCommissionStatus.NONE
    ).exclude(commission_amount__isnull=True)

    def _agg(status: str) -> tuple[Decimal, int]:
        row = qs.filter(commission_status=status).aggregate(
            total=Sum("commission_amount"),
            cnt=Count("id"),
        )
        return _money(row["total"]), int(row["cnt"] or 0)

    approved_total, approved_count = _agg(WorkRequestCommissionStatus.APPROVED)
    pending_total, pending_count = _agg(WorkRequestCommissionStatus.PENDING_REVIEW)
    awaiting_total, awaiting_count = _agg(WorkRequestCommissionStatus.AWAITING)
    rejected_total, rejected_count = _agg(WorkRequestCommissionStatus.REJECTED)
    open_total = _money(pending_total + awaiting_total + rejected_total)
    open_count = pending_count + awaiting_count + rejected_count

    by_executor_map: dict[int, dict[str, Any]] = {}
    for wr in (
        qs.select_related(
            "assigned_contractor",
            "assigned_contractor__user",
            "assigned_contractor__role",
            "role",
        )
        .order_by("-updated_at")
    ):
        contractor = wr.assigned_contractor
        if contractor is None:
            key = 0
            name = "Без исполнителя"
            role_name = wr.role.name if wr.role_id else "—"
        else:
            key = int(contractor.id)
            name = (
                (contractor.user.real_name or "").strip()
                or (contractor.user.username or "").strip()
                or f"#{contractor.user_id}"
            )
            role_name = (
                contractor.role.name
                if getattr(contractor, "role_id", None)
                else (wr.role.name if wr.role_id else "—")
            )
        bucket = by_executor_map.setdefault(
            key,
            {
                "contractor_id": key or None,
                "name": name,
                "role": role_name,
                "approved_total": ZERO,
                "approved_count": 0,
                "pending_total": ZERO,
                "pending_count": 0,
                "awaiting_total": ZERO,
                "awaiting_count": 0,
                "rejected_total": ZERO,
                "rejected_count": 0,
            },
        )
        amount = _money(wr.commission_amount)
        st = wr.commission_status
        if st == WorkRequestCommissionStatus.APPROVED:
            bucket["approved_total"] = _money(bucket["approved_total"] + amount)
            bucket["approved_count"] += 1
        elif st == WorkRequestCommissionStatus.PENDING_REVIEW:
            bucket["pending_total"] = _money(bucket["pending_total"] + amount)
            bucket["pending_count"] += 1
        elif st == WorkRequestCommissionStatus.AWAITING:
            bucket["awaiting_total"] = _money(bucket["awaiting_total"] + amount)
            bucket["awaiting_count"] += 1
        elif st == WorkRequestCommissionStatus.REJECTED:
            bucket["rejected_total"] = _money(bucket["rejected_total"] + amount)
            bucket["rejected_count"] += 1

    by_executor = sorted(
        by_executor_map.values(),
        key=lambda x: (
            -(x["approved_total"] + x["pending_total"] + x["awaiting_total"]),
            x["name"].lower(),
        ),
    )

    recent: list[dict[str, Any]] = []
    for wr in (
        qs.select_related(
            "assigned_contractor",
            "assigned_contractor__user",
            "role",
            "user",
        )
        .order_by("-updated_at")[:30]
    ):
        contractor = wr.assigned_contractor
        if contractor is None:
            exec_name = "—"
        else:
            exec_name = (
                (contractor.user.real_name or "").strip()
                or (contractor.user.username or "").strip()
                or f"#{contractor.user_id}"
            )
        when = wr.commission_reviewed_at or wr.commission_submitted_at or wr.client_confirmed_at or wr.updated_at
        recent.append(
            {
                "id": wr.id,
                "executor": exec_name,
                "role": wr.role.name if wr.role_id else "—",
                "client": (wr.user.real_name or wr.user.username or f"#{wr.user_id}"),
                "amount": _money(wr.commission_amount),
                "confirmed_amount": _money(wr.confirmed_amount),
                "status": wr.commission_status,
                "status_label": wr.get_commission_status_display(),
                "when": when,
                "when_s": timezone.localtime(when).strftime("%d.%m.%Y %H:%M") if when else "—",
            }
        )

    return {
        "approved_total": approved_total,
        "approved_count": approved_count,
        "pending_total": pending_total,
        "pending_count": pending_count,
        "awaiting_total": awaiting_total,
        "awaiting_count": awaiting_count,
        "rejected_total": rejected_total,
        "rejected_count": rejected_count,
        "open_total": open_total,
        "open_count": open_count,
        "by_executor": by_executor,
        "recent": recent,
    }


def build_earnings_forecast() -> dict[str, Any]:
    """
    Прогноз на следующий месяц.

    - Все текущие платящие вершины × цена месяца = база при полном продлении.
    - Подписки, истекающие в следующем месяце × цена = ожидаемые продления.
    """
    price = subscription_price()
    start, end, month_label = next_month_bounds()
    rows = _payer_rows()
    payer_count = len(rows)
    renew_rows = [r for r in rows if r.renews_next_month]
    renew_count = len(renew_rows)

    full_renewal = _money(price * payer_count)
    next_month_renewals = _money(price * renew_count)

    by_group: dict[str, dict[str, Any]] = {}
    for r in rows:
        for gname in r.group_names:
            bucket = by_group.setdefault(
                gname,
                {"name": gname, "payers": 0, "renew_next_month": 0},
            )
            bucket["payers"] += 1
            if r.renews_next_month:
                bucket["renew_next_month"] += 1

    group_rows = sorted(
        [
            {
                **b,
                "full_forecast": _money(price * b["payers"]),
                "renew_forecast": _money(price * b["renew_next_month"]),
            }
            for b in by_group.values()
        ],
        key=lambda x: (-x["payers"], x["name"].lower()),
    )

    detail_rows = [
        {
            "user": r.user,
            "label": " · ".join(r.labels),
            "until": r.until,
            "until_s": timezone.localtime(r.until).strftime("%d.%m.%Y"),
            "groups": ", ".join(r.group_names),
            "renews_next_month": r.renews_next_month,
            "amount": price,
        }
        for r in rows
    ]

    return {
        "price": price,
        "month_label": month_label,
        "month_start": start,
        "month_end": end,
        "payer_count": payer_count,
        "renew_count": renew_count,
        "full_renewal_forecast": full_renewal,
        "next_month_renewal_forecast": next_month_renewals,
        "groups": group_rows,
        "payers": detail_rows,
        "commissions": build_commission_stats(),
    }
