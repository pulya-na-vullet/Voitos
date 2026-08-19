from __future__ import annotations

import logging
import re
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.core.files.base import ContentFile
from django.utils import timezone

from database.models import (
    ActivityKind,
    ActivityLog,
    AccessState,
    AppSettings,
    BotUser,
    PaymentReceipt,
    ReceiptStatus,
)
from subscriptions.receipts import ReceiptParseResult, analyze_receipt_text, ocr_image_bytes

logger = logging.getLogger(__name__)

DAYS_PER_MONTH = 30


def _safe_receipt_filename(filename: str, *, default: str = "receipt.bin") -> str:
    base = Path(filename or default).name.strip() or default
    safe = re.sub(r"[^\w.\-]+", "_", base, flags=re.UNICODE).strip("._") or default
    return safe[:120]


def _parse_receipt_or_empty(image_bytes: bytes, filename: str) -> ReceiptParseResult:
    """OCR + LLM parse; on any failure return empty result for manual admin review."""
    try:
        ocr_text = ocr_image_bytes(image_bytes, filename=filename) or ""
    except Exception:
        logger.exception("OCR failed for %s — saving receipt for manual review", filename)
        ocr_text = ""
    if not ocr_text.strip():
        return ReceiptParseResult(
            amount=None,
            transfer_date=None,
            recipient_phone="",
            recipient_name="",
            ocr_text="",
            notes="ocr_empty_or_failed",
            details_match=False,
        )
    try:
        return analyze_receipt_text(ocr_text)
    except Exception:
        logger.exception("Receipt text analysis failed — saving for manual review")
        return ReceiptParseResult(
            amount=None,
            transfer_date=None,
            recipient_phone="",
            recipient_name="",
            ocr_text=ocr_text[:8000],
            notes="parse_failed",
            details_match=False,
        )


def period_from_amount(amount: Decimal, price: Decimal) -> tuple[int, int]:
    """
    Convert payment amount to (months, days).

    Full months cost `price`; remainder is converted to days as
    remainder/price * 30 (rounded).
    Example: 450 ₽ at 100 ₽/мес → 4 мес. + 15 дн.
    """
    amount = Decimal(amount)
    price = Decimal(price)
    if price <= 0:
        raise ValueError("Некорректная цена подписки")
    if amount <= 0:
        return 0, 0
    months = int(amount // price)
    remainder = amount % price
    days = 0
    if remainder > 0:
        days = int(
            (remainder / price * Decimal(DAYS_PER_MONTH)).quantize(
                Decimal("1"), rounding=ROUND_HALF_UP
            )
        )
        if days >= DAYS_PER_MONTH:
            months += days // DAYS_PER_MONTH
            days = days % DAYS_PER_MONTH
    return months, days


def format_period(months: int, days: int = 0) -> str:
    parts = []
    if months:
        parts.append(f"{months} мес.")
    if days:
        parts.append(f"{days} дн.")
    return " ".join(parts) if parts else "0"


PAYMENT_HELP = (
    "Переведите {price} ₽ / месяц на номер {phone}\n"
    "Получатель: {name}\n"
    "Пришлите фото или PDF чека в этот чат — после проверки доступ продлится."
)


def payment_help_text() -> str:
    cfg = AppSettings.load()
    return PAYMENT_HELP.format(
        phone=(cfg.payment_phone or "—").strip() or "—",
        name=(cfg.payment_name or "—").strip() or "—",
        price=cfg.subscription_price_rub,
    )


def access_message(user: BotUser) -> str | None:
    """Сообщение при ограниченном доступе; None если всё ок."""
    user.ensure_grace_period()
    state = user.access_state()
    cfg = AppSettings.load()
    grace_days = cfg.grace_days or 14
    if state == AccessState.ACTIVE:
        return None
    if state == AccessState.GRACE:
        until = user.grace_until
        until_s = timezone.localtime(until).strftime("%d.%m.%Y") if until else "скоро"
        if not user.subscription_until and not user.subscription_paid_by():
            return (
                f"Пробный период до {until_s} ({grace_days} дн.).\n\n"
                f"{payment_help_text()}\n\n"
                "Оплатите заранее, чтобы доступ не прервался."
            )
        return (
            f"Подписка закончилась. Оплатите до {until_s} "
            f"(ещё {grace_days} дн.).\n\n"
            f"{payment_help_text()}\n\n"
            "Пока бот ещё работает."
        )
    return (
        "Доступ закрыт — нужна оплата.\n\n"
        f"{payment_help_text()}\n\n"
        "После проверки чека доступ откроется."
    )


def can_use_features(user: BotUser) -> bool:
    user.ensure_grace_period()
    return user.has_feature_access()


def submit_receipt(user: BotUser, image_bytes: bytes, filename: str = "receipt.jpg") -> PaymentReceipt:
    """Always create a pending receipt + admin task, even if OCR/AI fails."""
    if not image_bytes:
        raise ValueError("Пустой файл чека")
    filename = _safe_receipt_filename(filename, default="receipt.pdf")
    from subscriptions.duplicates import file_sha256

    content_hash = file_sha256(image_bytes)
    parsed = _parse_receipt_or_empty(image_bytes, filename)
    cfg = AppSettings.load()
    months, days = (0, 0)
    if parsed.amount:
        months, days = period_from_amount(
            Decimal(parsed.amount),
            Decimal(cfg.subscription_price_rub or 100),
        )

    notes = parsed.notes or ""
    details_match = parsed.details_match
    # Soft-flag pixel-identical reuploads before admin review.
    prior = list(
        PaymentReceipt.objects.filter(content_hash=content_hash)
        .select_related("user")
        .order_by("created_at")[:5]
    )
    if prior:
        ids = ", ".join(f"#{r.id}" for r in prior)
        notes = (notes + "; " if notes else "") + f"pixel_duplicate_of={ids}"
        details_match = False

    receipt = PaymentReceipt(
        user=user,
        content_hash=content_hash,
        ocr_text=parsed.ocr_text,
        amount=parsed.amount,
        transfer_date=parsed.transfer_date,
        recipient_phone=parsed.recipient_phone,
        recipient_name=parsed.recipient_name,
        months_granted=months,
        days_granted=days,
        status=ReceiptStatus.PENDING,
        ai_notes=notes,
        details_match=details_match,
    )
    media_name = f"{user.max_user_id}_{timezone.now().strftime('%Y%m%d_%H%M%S')}_{filename}"
    receipt.image.save(media_name, ContentFile(image_bytes), save=False)
    receipt.save()

    ActivityLog.objects.create(
        user=user,
        kind=ActivityKind.RECEIPT_SUBMITTED,
        title="Отправлен чек на проверку",
        detail=(
            f"Сумма={parsed.amount} match={details_match}"
            + (f" DUPLICATE of {ids}" if prior else "")
        ),
        meta={"receipt_id": receipt.id, "months": months, "content_hash": content_hash},
    )
    try:
        from panel.admin_tasks import task_payment_receipt

        task = task_payment_receipt(receipt)
        if task is None:
            logger.error("Admin task was not created for payment receipt %s", receipt.id)
    except Exception:
        logger.exception("Failed to create admin task for payment receipt")
    return receipt


def approve_receipt(
    receipt: PaymentReceipt,
    comment: str = "",
    *,
    amount: Decimal | None = None,
    force_duplicate: bool = False,
) -> PaymentReceipt:
    """
    Accept a subscription receipt.

    Admin must confirm the accepted amount by hand; it becomes the saved
    payment fact and drives how many months are granted.
    """
    from subscriptions.duplicates import approved_identical, ensure_receipt_hash

    with transaction.atomic():
        receipt = PaymentReceipt.objects.select_for_update().select_related("user").get(
            pk=receipt.pk
        )
        if receipt.status == ReceiptStatus.APPROVED:
            return receipt

        ensure_receipt_hash(receipt)
        approved_dupes = approved_identical(receipt)
        if approved_dupes and not force_duplicate:
            first = approved_dupes[0]
            raise ValueError(
                f"Чек #{receipt.id} попиксельно совпадает с уже принятым "
                f"#{first.id} ({first.user}, {first.amount or '—'} ₽, "
                f"{first.period_label()}). Повторное принятие удвоит срок подписки. "
                "Чтобы всё равно принять — отметьте «Принять несмотря на дубль»."
            )

        cfg = AppSettings.load()
        price = Decimal(cfg.subscription_price_rub or 100)

        if amount is None:
            raise ValueError("Укажите сумму, которую принимаете по чеку.")
        amount = Decimal(amount)
        if amount <= 0:
            raise ValueError("Сумма должна быть больше нуля.")

        months, days = period_from_amount(amount, price)
        if months < 1 and days < 1:
            raise ValueError(
                f"Сумма {amount:.0f} ₽ слишком мала для начисления срока "
                f"(цена месяца {price:.0f} ₽)."
            )

        user = receipt.user
        user.extend_subscription(months=months, days=days)
        now = timezone.now()
        receipt.amount = amount
        receipt.status = ReceiptStatus.APPROVED
        receipt.months_granted = months
        receipt.days_granted = days
        receipt.transfer_date = timezone.localdate(now)
        receipt.details_match = True
        receipt.admin_comment = comment
        receipt.reviewed_at = now
        receipt.save()
        period = format_period(months, days)
        ActivityLog.objects.create(
            user=user,
            kind=ActivityKind.RECEIPT_APPROVED,
            title="Чек принят",
            detail=f"{amount:.0f} ₽ → +{period} до {user.subscription_until}",
            meta={
                "receipt_id": receipt.id,
                "amount": str(amount),
                "months": months,
                "days": days,
            },
        )

    try:
        from services.tax import sync_self_employed_tax_collected

        sync_self_employed_tax_collected()
    except Exception:
        logger.exception("Failed to sync self-employed tax after subscription approve")
    try:
        from panel.admin_tasks import task_payment_receipt

        task_payment_receipt(receipt)
    except Exception:
        logger.exception("Failed to close admin task for payment receipt")
    try:
        from subscriptions.renewal_reminders import schedule_subscription_renewal_reminders

        schedule_subscription_renewal_reminders(user)
    except Exception:
        logger.exception("Failed to schedule subscription renewal reminders")
    try:
        from api.emit import emit_app_event

        emit_app_event(
            user,
            ntype="subscription.receipt_approved",
            title="Чек принят",
            body=approved_user_message(receipt),
            entity_type="receipt",
            entity_id=receipt.id,
        )
    except Exception:
        logger.exception("app inbox receipt_approved receipt=%s", receipt.id)
    return receipt


def reject_receipt(receipt: PaymentReceipt, comment: str = "") -> PaymentReceipt:
    receipt.status = ReceiptStatus.REJECTED
    receipt.admin_comment = comment
    receipt.reviewed_at = timezone.now()
    receipt.save()
    ActivityLog.objects.create(
        user=receipt.user,
        kind=ActivityKind.RECEIPT_REJECTED,
        title="Чек отклонён",
        detail=comment or "Без комментария",
        meta={"receipt_id": receipt.id},
    )
    try:
        from services.tax import sync_self_employed_tax_collected

        sync_self_employed_tax_collected()
    except Exception:
        logger.exception("Failed to sync self-employed tax after subscription reject")
    try:
        from panel.admin_tasks import task_payment_receipt

        task_payment_receipt(receipt)
    except Exception:
        logger.exception("Failed to close admin task for rejected receipt")
    try:
        from api.emit import emit_app_event

        emit_app_event(
            receipt.user,
            ntype="subscription.receipt_rejected",
            title="Чек отклонён",
            body=rejected_user_message(receipt),
            entity_type="receipt",
            entity_id=receipt.id,
        )
    except Exception:
        logger.exception("app inbox receipt_rejected receipt=%s", receipt.id)
    return receipt


def recalculate_approved_receipt_periods() -> int:
    """
    Fix legacy approved receipts that stored only whole months.

    Recalculates months/days from amount, extends subscription_until by the
    missing days, fills transfer_date from reviewed_at, marks details_match.
    Idempotent: second run does nothing when periods already match.
    """
    from datetime import timedelta

    cfg = AppSettings.load()
    price = Decimal(cfg.subscription_price_rub or 100)
    fixed = 0
    qs = (
        PaymentReceipt.objects.filter(status=ReceiptStatus.APPROVED)
        .exclude(amount__isnull=True)
        .select_related("user")
    )
    for receipt in qs.iterator():
        amount = Decimal(receipt.amount or 0)
        if amount <= 0:
            continue
        new_months, new_days = period_from_amount(amount, price)
        old_total = 30 * int(receipt.months_granted or 0) + int(receipt.days_granted or 0)
        new_total = 30 * new_months + new_days
        delta = new_total - old_total
        changed_fields: list[str] = []
        if receipt.months_granted != new_months or receipt.days_granted != new_days:
            receipt.months_granted = new_months
            receipt.days_granted = new_days
            changed_fields.extend(["months_granted", "days_granted"])
        if receipt.reviewed_at and not receipt.transfer_date:
            receipt.transfer_date = timezone.localtime(receipt.reviewed_at).date()
            changed_fields.append("transfer_date")
        if not receipt.details_match:
            receipt.details_match = True
            changed_fields.append("details_match")
        if changed_fields:
            receipt.save(update_fields=changed_fields)
        if delta and receipt.user.subscription_until:
            receipt.user.subscription_until = receipt.user.subscription_until + timedelta(
                days=delta
            )
            receipt.user.save(update_fields=["subscription_until"])
        if changed_fields or delta:
            fixed += 1
    if fixed:
        logger.info("Recalculated period for %s approved receipt(s)", fixed)
    return fixed


def revoke_unpaid_subscriptions() -> int:
    """
    Clear gifted/active subscriptions for users without an approved receipt.
    Opens a grace window so the bot asks for payment instead of silent full access.

    Safe to call on every startup: only touches unpaid users who still have
    an active subscription_until in the future.
    """
    from datetime import timedelta

    cfg = AppSettings.load()
    grace_days = cfg.grace_days or 14
    now = timezone.now()
    grace_until = now + timedelta(days=grace_days)
    paid_ids = set(
        PaymentReceipt.objects.filter(status=ReceiptStatus.APPROVED).values_list(
            "user_id", flat=True
        )
    )
    updated = 0
    qs = BotUser.objects.exclude(id__in=paid_ids).filter(subscription_until__gt=now)
    for user in qs.iterator():
        user.subscription_until = None
        user.grace_until = grace_until
        user.last_payment_notice_at = None
        user.save(
            update_fields=["subscription_until", "grace_until", "last_payment_notice_at"]
        )
        updated += 1
    if updated:
        logger.info("Revoked unpaid free access for %s user(s)", updated)
    return updated


def approved_user_message(receipt: PaymentReceipt) -> str:
    until = receipt.user.subscription_until
    until_s = timezone.localtime(until).strftime("%d.%m.%Y") if until else "—"
    amount_s = f"{receipt.amount:.0f} ₽" if receipt.amount is not None else "—"
    period = receipt.period_label()
    return (
        f"Чек принят. Зачтено: {amount_s}.\n"
        f"Подписка продлена на {period}.\n"
        f"Доступ открыт до {until_s}."
    )


def rejected_user_message(receipt: PaymentReceipt) -> str:
    extra = f"\nКомментарий: {receipt.admin_comment}" if receipt.admin_comment else ""
    return (
        "Чек отклонён администратором."
        f"{extra}\n\n"
        f"{payment_help_text()}"
    )


def rebuild_subscription_until(user: BotUser):
    """
    Recalculate subscription_until from remaining approved receipts.

    Receipts are applied in review order; each adds its granted period
    starting from max(receipt time, running cursor).
    """
    from datetime import timedelta

    receipts = list(
        PaymentReceipt.objects.filter(user=user, status=ReceiptStatus.APPROVED)
        .order_by("reviewed_at", "created_at", "id")
    )
    if not receipts:
        user.subscription_until = None
        user.save(update_fields=["subscription_until", "last_seen_at"])
        try:
            from subscriptions.renewal_reminders import schedule_subscription_renewal_reminders

            schedule_subscription_renewal_reminders(user)
        except Exception:
            logger.exception("Failed to clear subscription renewal reminders")
        return None

    cfg = AppSettings.load()
    price = Decimal(cfg.subscription_price_rub or 100)
    cursor = None
    for receipt in receipts:
        start = receipt.reviewed_at or receipt.created_at or timezone.now()
        if cursor is None or cursor < start:
            cursor = start
        months = int(receipt.months_granted or 0)
        days = int(receipt.days_granted or 0)
        if months < 1 and days < 1 and receipt.amount:
            months, days = period_from_amount(Decimal(receipt.amount), price)
        total_days = 30 * max(0, months) + max(0, days)
        if total_days < 1:
            continue
        cursor = cursor + timedelta(days=total_days)
    user.subscription_until = cursor
    user.grace_until = None
    user.save(update_fields=["subscription_until", "grace_until", "last_seen_at"])
    try:
        from subscriptions.renewal_reminders import schedule_subscription_renewal_reminders

        schedule_subscription_renewal_reminders(user)
    except Exception:
        logger.exception("Failed to reschedule subscription renewal reminders")
    return cursor


def delete_receipt(receipt: PaymentReceipt, *, reason: str) -> BotUser:
    """
    Delete a payment receipt after an admin resolution.

    Rebuilds the user's subscription from remaining approved receipts.
    Returns the affected user (for notification).
    """
    reason = (reason or "").strip()
    if not reason:
        raise ValueError("Укажите причину удаления чека.")

    user = receipt.user
    receipt_id = receipt.id
    was_approved = receipt.status == ReceiptStatus.APPROVED
    amount = receipt.amount
    period = receipt.period_label()
    status_label = receipt.get_status_display()

    # Remove file from storage if present
    if receipt.image:
        try:
            receipt.image.delete(save=False)
        except Exception:
            logger.exception("Failed to delete receipt file #%s", receipt_id)

    receipt.delete()

    until = rebuild_subscription_until(user)
    user.refresh_from_db()
    until_s = (
        timezone.localtime(until).strftime("%d.%m.%Y")
        if until
        else "не оформлена / истекла"
    )
    ActivityLog.objects.create(
        user=user,
        kind=ActivityKind.RECEIPT_REJECTED,
        title="Чек удалён администратором",
        detail=reason,
        meta={
            "receipt_id": receipt_id,
            "was_approved": was_approved,
            "amount": str(amount) if amount is not None else "",
            "period": period,
            "status": status_label,
            "subscription_until": until_s,
        },
    )
    try:
        from services.tax import sync_self_employed_tax_collected

        sync_self_employed_tax_collected()
    except Exception:
        logger.exception("Failed to sync tax after receipt delete")
    try:
        from panel.admin_tasks import close_task_for_source
        from database.models import AdminTaskKind

        close_task_for_source(AdminTaskKind.PAYMENT_RECEIPT, "PaymentReceipt", receipt_id)
    except Exception:
        logger.exception("Failed to close admin task after receipt delete")
    return user


def deleted_user_message(*, reason: str, subscription_until=None) -> str:
    until_s = (
        timezone.localtime(subscription_until).strftime("%d.%m.%Y")
        if subscription_until
        else "не оформлена / истекла"
    )
    return (
        "Администратор удалил ваш чек оплаты подписки.\n"
        f"Причина: {reason}\n"
        f"Срок доступа пересчитан. Подписка до: {until_s}.\n\n"
        f"{payment_help_text()}"
    )
