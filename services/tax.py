from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal
from typing import Any

from django.db.models import Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from database.models import AppSettings, PaymentReceipt, ReceiptStatus, ServiceReceipt

logger = logging.getLogger(__name__)

ZERO = Decimal("0.00")


def _year_bounds(year: int) -> tuple[datetime, datetime]:
    tz = timezone.get_current_timezone()
    start = timezone.make_aware(datetime(year, 1, 1, 0, 0, 0), tz)
    end = timezone.make_aware(datetime(year + 1, 1, 1, 0, 0, 0), tz)
    return start, end


def _as_money(value: Decimal | int | float | None) -> Decimal:
    if value is None:
        return ZERO
    return Decimal(value).quantize(Decimal("0.01"))


def compute_self_employed_collected(*, year: int | None = None) -> dict[str, Any]:
    """
    Sum approved transfers that go through the self-employed payee.

    Includes subscription receipts + service campaign receipts for the
    calendar year (НПД limit is annual).
    """
    year = int(year or timezone.localdate().year)
    start, end = _year_bounds(year)

    sub_qs = (
        PaymentReceipt.objects.filter(status=ReceiptStatus.APPROVED)
        .annotate(paid_at=Coalesce("reviewed_at", "created_at"))
        .filter(paid_at__gte=start, paid_at__lt=end)
    )
    svc_qs = (
        ServiceReceipt.objects.filter(status=ReceiptStatus.APPROVED)
        .annotate(paid_at=Coalesce("reviewed_at", "created_at"))
        .filter(paid_at__gte=start, paid_at__lt=end)
    )
    subscriptions = _as_money(sub_qs.aggregate(total=Sum("amount"))["total"])
    services = _as_money(svc_qs.aggregate(total=Sum("amount"))["total"])
    total = _as_money(subscriptions + services)
    return {
        "year": year,
        "subscriptions": subscriptions,
        "services": services,
        "total": total,
        "subscription_count": sub_qs.count(),
        "service_count": svc_qs.count(),
    }


def sync_self_employed_tax_collected(
    cfg: AppSettings | None = None,
    *,
    year: int | None = None,
) -> dict[str, Any]:
    """Recompute and persist AppSettings.service_tax_collected from approved receipts."""
    cfg = cfg or AppSettings.load()
    stats = compute_self_employed_collected(year=year)
    total = stats["total"]
    current = _as_money(cfg.service_tax_collected)
    if current != total:
        cfg.service_tax_collected = total
        cfg.save(update_fields=["service_tax_collected", "updated_at"])
        logger.info(
            "Synced self-employed tax collected: %s → %s ₽ (year=%s, sub=%s, svc=%s)",
            current,
            total,
            stats["year"],
            stats["subscriptions"],
            stats["services"],
        )
    return stats
