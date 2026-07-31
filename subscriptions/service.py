from __future__ import annotations

import logging
from decimal import Decimal
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


PAYMENT_HELP = (
    "Чтобы продлить доступ, переведите оплату на номер {phone}\n"
    "Получатель: {name}\n"
    "Стоимость: {price} ₽ / месяц.\n"
    "Пришлите сюда фото или скрин чека о переводе."
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
    ocr_text = ocr_image_bytes(image_bytes)
    parsed = analyze_receipt_text(ocr_text)
    cfg = AppSettings.load()
    months = 0
    if parsed.amount:
        months = max(0, int(parsed.amount // Decimal(cfg.subscription_price_rub or 100)))

    receipt = PaymentReceipt(
        user=user,
        ocr_text=parsed.ocr_text,
        amount=parsed.amount,
        transfer_date=parsed.transfer_date,
        recipient_phone=parsed.recipient_phone,
        recipient_name=parsed.recipient_name,
        months_granted=months,
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

    months = int(amount // price)
    if months < 1:
        raise ValueError(
            f"Сумма {amount:.0f} ₽ меньше стоимости месяца ({price:.0f} ₽)."
        )

    user = receipt.user
    user.extend_subscription(months)
    receipt.amount = amount
    receipt.status = ReceiptStatus.APPROVED
    receipt.months_granted = months
    receipt.admin_comment = comment
    receipt.reviewed_at = timezone.now()
    receipt.save()
    ActivityLog.objects.create(
        user=user,
        kind=ActivityKind.RECEIPT_APPROVED,
        title="Чек принят",
        detail=f"{amount:.0f} ₽ → +{months} мес. до {user.subscription_until}",
        meta={"receipt_id": receipt.id, "amount": str(amount), "months": months},
    )
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
    return receipt


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
    return (
        f"Чек принят. Зачтено: {amount_s}.\n"
        f"Подписка активна на {receipt.months_granted} мес.\n"
        f"Доступ открыт до {until_s}."
    )


def rejected_user_message(receipt: PaymentReceipt) -> str:
    extra = f"\nКомментарий: {receipt.admin_comment}" if receipt.admin_comment else ""
    return (
        "Чек отклонён администратором."
        f"{extra}\n\n"
        f"{payment_help_text()}"
    )
