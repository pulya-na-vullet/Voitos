from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from django.db.models import Count, Sum
from django.db.models.functions import Coalesce, TruncDate
from django.utils import timezone

from database.models import (
    AccessState,
    AiUsageLog,
    BotUser,
    PaymentReceipt,
    ReceiptStatus,
    YandexBillingEntry,
)


ZERO = Decimal("0.00")


def _as_money(value: Decimal | int | float | None) -> Decimal:
    if value is None:
        return ZERO
    return Decimal(value).quantize(Decimal("0.01"))


def period_bounds() -> dict[str, tuple[datetime, datetime]]:
    """Calendar week (Mon–now), month, year in local timezone."""
    now = timezone.localtime()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start - timedelta(days=today_start.weekday())
    month_start = today_start.replace(day=1)
    year_start = today_start.replace(month=1, day=1)
    end = now
    return {
        "week": (week_start, end),
        "month": (month_start, end),
        "year": (year_start, end),
    }


def _approved_qs():
    return PaymentReceipt.objects.filter(status=ReceiptStatus.APPROVED).annotate(
        paid_at=Coalesce("reviewed_at", "created_at")
    )


def collected_between(start: datetime, end: datetime) -> tuple[Decimal, int]:
    qs = _approved_qs().filter(paid_at__gte=start, paid_at__lte=end)
    agg = qs.aggregate(total=Sum("amount"), n=Count("id"))
    return _as_money(agg["total"]), int(agg["n"] or 0)


def ai_estimated_between(start: datetime, end: datetime) -> Decimal:
    agg = AiUsageLog.objects.filter(created_at__gte=start, created_at__lte=end).aggregate(
        total=Sum("estimated_cost_rub")
    )
    return _as_money(agg["total"])


def ai_billed_between(start: datetime, end: datetime) -> Decimal:
    start_d = timezone.localtime(start).date()
    end_d = timezone.localtime(end).date()
    agg = YandexBillingEntry.objects.filter(for_date__gte=start_d, for_date__lte=end_d).aggregate(
        total=Sum("amount_rub")
    )
    return _as_money(agg["total"])


def new_users_between(start: datetime, end: datetime) -> int:
    return BotUser.objects.filter(first_seen_at__gte=start, first_seen_at__lte=end).count()


def user_access_stats() -> dict[str, int]:
    total = BotUser.objects.count()
    active = grace = blocked = 0
    # Small admin panels: fine to classify in Python for accurate grace rules.
    for user in BotUser.objects.only(
        "subscription_until", "grace_until", "first_seen_at"
    ).iterator(chunk_size=500):
        state = user.access_state()
        if state == AccessState.ACTIVE:
            active += 1
        elif state == AccessState.GRACE:
            grace += 1
        else:
            blocked += 1
    return {
        "total": total,
        "active": active,
        "grace": grace,
        "blocked": blocked,
    }


def _period_block(start: datetime, end: datetime) -> dict[str, Any]:
    collected, approved_count = collected_between(start, end)
    estimated = ai_estimated_between(start, end)
    billed = ai_billed_between(start, end)
    ai_used = billed if billed > 0 else estimated
    return {
        "collected": collected,
        "approved_count": approved_count,
        "ai_estimated": estimated,
        "ai_billed": billed,
        "ai_used": ai_used,
        "ai_source": "billing" if billed > 0 else "estimate",
        "net": _as_money(collected - ai_used),
        "new_users": new_users_between(start, end),
        "start": start,
        "end": end,
    }


def daily_series(days: int = 30) -> dict[str, Any]:
    now = timezone.localtime()
    start = (now - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
    labels: list[str] = []
    day_keys: list[date] = []
    for i in range(days):
        d = (start + timedelta(days=i)).date()
        day_keys.append(d)
        labels.append(d.strftime("%d.%m"))

    revenue_map = {d: ZERO for d in day_keys}
    for row in (
        _approved_qs()
        .filter(paid_at__gte=start, paid_at__lte=now)
        .annotate(day=TruncDate("paid_at"))
        .values("day")
        .annotate(total=Sum("amount"))
    ):
        if row["day"]:
            revenue_map[row["day"]] = _as_money(row["total"])

    users_map = {d: 0 for d in day_keys}
    for row in (
        BotUser.objects.filter(first_seen_at__gte=start, first_seen_at__lte=now)
        .annotate(day=TruncDate("first_seen_at"))
        .values("day")
        .annotate(n=Count("id"))
    ):
        if row["day"]:
            users_map[row["day"]] = int(row["n"] or 0)

    ai_map = {d: ZERO for d in day_keys}
    for row in (
        AiUsageLog.objects.filter(created_at__gte=start, created_at__lte=now)
        .annotate(day=TruncDate("created_at"))
        .values("day")
        .annotate(total=Sum("estimated_cost_rub"))
    ):
        if row["day"]:
            ai_map[row["day"]] = _as_money(row["total"])

    return {
        "labels": labels,
        "revenue": [float(revenue_map[d]) for d in day_keys],
        "users": [users_map[d] for d in day_keys],
        "ai": [float(ai_map[d]) for d in day_keys],
        "labels_json": json.dumps(labels, ensure_ascii=False),
        "revenue_json": json.dumps([float(revenue_map[d]) for d in day_keys]),
        "users_json": json.dumps([users_map[d] for d in day_keys]),
        "ai_json": json.dumps([float(ai_map[d]) for d in day_keys]),
    }


def build_finance_snapshot() -> dict[str, Any]:
    bounds = period_bounds()
    periods = {name: _period_block(start, end) for name, (start, end) in bounds.items()}
    pending = PaymentReceipt.objects.filter(status=ReceiptStatus.PENDING)
    pending_agg = pending.aggregate(total=Sum("amount"), n=Count("id"))
    return {
        "periods": periods,
        "users": user_access_stats(),
        "chart": daily_series(30),
        "pending_count": int(pending_agg["n"] or 0),
        "pending_amount": _as_money(pending_agg["total"]),
        "billing_entries": list(YandexBillingEntry.objects.all()[:20]),
    }
