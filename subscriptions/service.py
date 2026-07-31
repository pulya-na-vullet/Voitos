from __future__ import annotations

import logging
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from django.conf import settings
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
from subscriptions.receipts import analyze_receipt_text, ocr_image_bytes

logger = logging.getLogger(__name__)

DAYS_PER_MONTH = 30


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
    "Чтобы продлить доступ, переведите оплату на номер {phone}\n"
    "Получатель: {name}\n"
    "Стоимость: {price} ₽ / месяц.\n"
    "Пришлите сюда фото, скрин или PDF чека о переводе."
)


def payment_help_text() -> str:
    cfg = AppSettings.load()
    return PAYMENT_HELP.format(
        phone=cfg.payment_phone,
        name=cfg.payment_name,
        price=cfg.subscription_price_rub,
    )


def access_message(user: BotUser) -> str | None:
    """Return a message if user should be notified / blocked; None if full access OK."""
    user.ensure_grace_period()
    state = user.access_state()
    if state == AccessState.ACTIVE:
        return None
    if state == AccessState.GRACE:
        until = user.grace_until
        until_s = timezone.localtime(until).strftime("%d.%m.%Y") if until else "скоро"
        return (
            f"Срок подписки истёк. Жду оплату в течение 2 дней (до {until_s}).\n\n"
            f"{payment_help_text()}\n\n"
            "Пока льготный период активен, базовые функции ещё доступны."
        )
    return (
        "Доступ к функциям закрыт: оплата не поступила.\n\n"
        f"{payment_help_text()}\n\n"
        "После проверки чека администратором доступ откроется автоматически."
    )


def can_use_features(user: BotUser) -> bool:
    user.ensure_grace_period()
    return user.has_feature_access()


def submit_receipt(user: BotUser, image_bytes: bytes, filename: str = "receipt.jpg") -> PaymentReceipt:
    ocr_text = ocr_image_bytes(image_bytes, filename=filename)
    parsed = analyze_receipt_text(ocr_text)
    cfg = AppSettings.load()
    months, days = (0, 0)
    if parsed.amount:
        months, days = period_from_amount(
            Decimal(parsed.amount),
            Decimal(cfg.subscription_price_rub or 100),
        )

    receipt = PaymentReceipt(
        user=user,
        ocr_text=parsed.ocr_text,
        amount=parsed.amount,
        transfer_date=parsed.transfer_date,
        recipient_phone=parsed.recipient_phone,
        recipient_name=parsed.recipient_name,
        months_granted=months,
        days_granted=days,
        status=ReceiptStatus.PENDING,
        ai_notes=parsed.notes,
        details_match=parsed.details_match,
    )
    # store file
    media_name = f"receipts/{user.max_user_id}_{timezone.now().strftime('%Y%m%d_%H%M%S')}_{filename}"
    receipt.image.save(Path(media_name).name, ContentFile(image_bytes), save=False)
    receipt.save()

    ActivityLog.objects.create(
        user=user,
        kind=ActivityKind.RECEIPT_SUBMITTED,
        title="Отправлен чек на проверку",
        detail=f"Сумма={parsed.amount} match={parsed.details_match}",
        meta={"receipt_id": receipt.id, "months": months},
    )
    try:
        from panel.admin_tasks import task_payment_receipt

        task_payment_receipt(receipt)
    except Exception:
        logger.exception("Failed to create admin task for payment receipt")
    return receipt


def approve_receipt(
    receipt: PaymentReceipt,
    comment: str = "",
    *,
    amount: Decimal | None = None,
) -> PaymentReceipt:
    """
    Accept a subscription receipt.

    Admin must confirm the accepted amount by hand; it becomes the saved
    payment fact and drives how many months are granted.
    """
    if receipt.status == ReceiptStatus.APPROVED:
        return receipt

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
    # Admin confirmation becomes the payment transfer date.
    receipt.transfer_date = timezone.localdate(now)
    # Mark requisites as accepted by admin (shown as «распознано администратором»).
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
        from panel.admin_tasks import task_payment_receipt

        task_payment_receipt(receipt)
    except Exception:
        logger.exception("Failed to close admin task for payment receipt")
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
        from panel.admin_tasks import task_payment_receipt

        task_payment_receipt(receipt)
    except Exception:
        logger.exception("Failed to close admin task for rejected receipt")
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
    grace_days = cfg.grace_days or 2
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
