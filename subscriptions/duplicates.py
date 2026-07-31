from __future__ import annotations

import hashlib
import logging
from collections import defaultdict
from typing import Iterable

from django.db.models import Count

from database.models import PaymentReceipt, ReceiptStatus

logger = logging.getLogger(__name__)


def file_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def ensure_receipt_hash(receipt: PaymentReceipt) -> str:
    """Compute and persist content_hash from stored file if missing."""
    if receipt.content_hash:
        return receipt.content_hash
    if not receipt.image:
        return ""
    try:
        receipt.image.open("rb")
        digest = file_sha256(receipt.image.read())
        receipt.image.close()
    except Exception:
        logger.exception("Failed to hash receipt #%s", receipt.pk)
        return ""
    if digest:
        receipt.content_hash = digest
        receipt.save(update_fields=["content_hash"])
    return digest


def backfill_missing_hashes(limit: int = 500) -> int:
    """Fill content_hash for receipts that still miss it. Returns updated count."""
    updated = 0
    qs = PaymentReceipt.objects.filter(content_hash="").exclude(image="")[:limit]
    for receipt in qs:
        before = receipt.content_hash
        ensure_receipt_hash(receipt)
        if receipt.content_hash and receipt.content_hash != before:
            updated += 1
    return updated


def find_identical_receipts(receipt: PaymentReceipt) -> list[PaymentReceipt]:
    """Other receipts with the exact same file bytes (SHA-256)."""
    digest = receipt.content_hash or ensure_receipt_hash(receipt)
    if not digest:
        return []
    return list(
        PaymentReceipt.objects.filter(content_hash=digest)
        .exclude(pk=receipt.pk)
        .select_related("user")
        .order_by("created_at")
    )


def approved_identical(receipt: PaymentReceipt) -> list[PaymentReceipt]:
    return [r for r in find_identical_receipts(receipt) if r.status == ReceiptStatus.APPROVED]


def group_identical_receipts(
    receipts: Iterable[PaymentReceipt] | None = None,
) -> dict[str, list[PaymentReceipt]]:
    """
    Return {hash: [receipts...]} for hashes that appear 2+ times.

    If receipts is None — scan all receipts that have a content_hash.
    """
    backfill_missing_hashes()
    if receipts is None:
        dupe_hashes = (
            PaymentReceipt.objects.exclude(content_hash="")
            .values("content_hash")
            .annotate(n=Count("id"))
            .filter(n__gte=2)
            .values_list("content_hash", flat=True)
        )
        hashes = list(dupe_hashes)
        if not hashes:
            return {}
        qs = (
            PaymentReceipt.objects.filter(content_hash__in=hashes)
            .select_related("user")
            .order_by("content_hash", "created_at")
        )
    else:
        items = list(receipts)
        for r in items:
            if not r.content_hash:
                ensure_receipt_hash(r)
        by_hash: dict[str, list[PaymentReceipt]] = defaultdict(list)
        for r in items:
            if r.content_hash:
                by_hash[r.content_hash].append(r)
        return {h: rows for h, rows in by_hash.items() if len(rows) >= 2}

    by_hash: dict[str, list[PaymentReceipt]] = defaultdict(list)
    for r in qs:
        by_hash[r.content_hash].append(r)
    return dict(by_hash)


def duplicate_labels_for_items(
    items: Iterable[PaymentReceipt],
) -> tuple[dict[int, str], list[dict]]:
    """
    Build per-receipt highlight labels and summary groups for the panel.

    Returns:
      receipt_id -> label like "Дубль A (#12, #15)"
      groups summary for UI cards
    """
    groups = group_identical_receipts(items)
    labels: dict[int, str] = {}
    summaries: list[dict] = []
    for idx, (digest, rows) in enumerate(sorted(groups.items(), key=lambda x: x[1][0].created_at), start=1):
        letter = chr(ord("A") + ((idx - 1) % 26))
        ids = [r.id for r in rows]
        users = sorted({str(r.user) for r in rows})
        statuses = sorted({r.get_status_display() for r in rows})
        label = f"Дубль {letter}"
        for r in rows:
            others = [i for i in ids if i != r.id]
            labels[r.id] = f"{label} (=#{', #'.join(map(str, others))})"
        summaries.append(
            {
                "letter": letter,
                "hash": digest[:12],
                "ids": ids,
                "users": users,
                "statuses": statuses,
                "count": len(rows),
                "has_approved": any(r.status == ReceiptStatus.APPROVED for r in rows),
                "has_pending": any(r.status == ReceiptStatus.PENDING for r in rows),
            }
        )
    return labels, summaries
