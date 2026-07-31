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


def approve_receipt(receipt: PaymentReceipt, comment: str = "") -> PaymentReceipt:
    cfg = AppSettings.load()
    months = receipt.calc_months(cfg.subscription_price_rub)
    if months < 1 and receipt.amount and receipt.amount >= cfg.subscription_price_rub:
        months = int(receipt.amount // cfg.subscription_price_rub)
    if months < 1:
        months = 1 if receipt.details_match else 0
    if months < 1:
        raise ValueError("Нельзя принять чек: сумма меньше стоимости месяца или не распознана.")

    user = receipt.user
    user.extend_subscription(months)
    receipt.status = ReceiptStatus.APPROVED
    receipt.months_granted = months
    receipt.admin_comment = comment
    receipt.reviewed_at = timezone.now()
    receipt.save()
    ActivityLog.objects.create(
        user=user,
        kind=ActivityKind.RECEIPT_APPROVED,
        title="Чек принят",
        detail=f"+{months} мес. до {user.subscription_until}",
        meta={"receipt_id": receipt.id},
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


def approved_user_message(receipt: PaymentReceipt) -> str:
    until = receipt.user.subscription_until
    until_s = timezone.localtime(until).strftime("%d.%m.%Y") if until else "—"
    return (
        f"Чек принят. Подписка активна на {receipt.months_granted} мес.\n"
        f"Доступ открыт до {until_s}."
    )


def rejected_user_message(receipt: PaymentReceipt) -> str:
    extra = f"\nКомментарий: {receipt.admin_comment}" if receipt.admin_comment else ""
    return (
        "Чек отклонён администратором."
        f"{extra}\n\n"
        f"{payment_help_text()}"
    )
