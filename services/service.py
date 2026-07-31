from __future__ import annotations

import logging
from decimal import Decimal
from pathlib import Path

from django.core.files.base import ContentFile
from django.db.models import Sum
from django.utils import timezone

from database.models import (
    ActivityKind,
    ActivityLog,
    AppSettings,
    BotUser,
    CampaignStatus,
    InviteStatus,
    ReceiptStatus,
    ServiceCampaign,
    ServiceCategory,
    ServiceGroup,
    ServiceInvite,
    ServiceReceipt,
)
from subscriptions.receipts import analyze_receipt_text, normalize_phone, ocr_image_bytes

logger = logging.getLogger(__name__)


def service_payment_requisites() -> str:
    cfg = AppSettings.load()
    return (
        f"Получатель: {cfg.service_payee_name}\n"
        f"Телефон: {cfg.service_payee_phone}\n"
        f"Статус: {cfg.service_payee_status}"
    )


def progress_bar(percent: int, width: int = 10) -> str:
    percent = max(0, min(100, int(percent)))
    filled = round(width * percent / 100)
    return "█" * filled + "░" * (width - filled)


def campaign_progress_line(campaign: ServiceCampaign) -> str:
    # Fresh aggregate to avoid stale related cache
    paid = (
        campaign.invites.aggregate(s=Sum("amount_paid"))["s"]
        or Decimal("0")
    )
    total = Decimal(campaign.total_amount or 0)
    pct = min(100, int(paid * 100 / total)) if total > 0 else 0
    return (
        f"Собрано: {paid:.0f} / {total:.0f} ₽ "
        f"[{progress_bar(pct)}] {pct}%"
    )


def format_collections_for_user(user: BotUser) -> str:
    invites = (
        ServiceInvite.objects.filter(user=user)
        .exclude(status=InviteStatus.CANCELLED)
        .select_related("campaign")
        .order_by("-offered_at")
    )
    if not invites:
        return (
            "Доступных сервисных сборов пока нет.\n"
            "Когда администратор предложит участие — придёт сообщение."
        )
    lines = ["Сервисные сборы\n────────────"]
    for inv in invites:
        c = inv.campaign
        status = "оплачено" if inv.status == InviteStatus.PAID else "ожидает оплаты"
        if inv.status == InviteStatus.DECLINED:
            status = "отказ"
        group_name = c.group.name if c.group_id else (c.locality or "")
        extra = f"{group_name}\n" if group_name else ""
        lines.append(
            f"\n{c.get_category_display()} — {c.title}\n"
            f"{extra}"
            f"{campaign_progress_line(c)}\n"
            f"Ваш взнос: {inv.amount_due:.0f} ₽ — {status}"
        )
    lines.append("\nРеквизиты для перевода:\n" + service_payment_requisites())
    lines.append("\nПришлите фото чека в этот чат.")
    return "\n".join(lines)


def create_campaign(
    *,
    category: str,
    title: str,
    description: str = "",
    locality: str = "",
    total_amount: Decimal,
    amount_per_user: Decimal | None = None,
    group: ServiceGroup | None = None,
) -> ServiceCampaign:
    if category not in ServiceCategory.values:
        raise ValueError("Неизвестная категория")
    title = title.strip() or dict(ServiceCategory.choices).get(category, "Мероприятие")
    date_s = timezone.localtime().strftime("%d.%m.%Y")
    if "от " not in title.lower():
        title = f"{title} от {date_s}"
    if group and not locality:
        locality = group.name
    return ServiceCampaign.objects.create(
        category=category,
        title=title,
        description=description.strip(),
        locality=(locality or "").strip(),
        group=group,
        total_amount=total_amount,
        amount_per_user=amount_per_user or Decimal("0"),
        status=CampaignStatus.DRAFT,
    )


def offer_message(campaign: ServiceCampaign, amount_per_user: Decimal) -> str:
    date_s = timezone.localtime(campaign.created_at).strftime("%d.%m.%Y")
    group_line = ""
    if campaign.group_id:
        group_line = f"Группа: {campaign.group.name}\n"
    desc = f"{campaign.description}\n" if campaign.description else ""
    return (
        f"Начат сбор: {campaign.get_category_display()}\n"
        f"{campaign.title}\n"
        f"{desc}"
        f"{group_line}"
        f"Дата: {date_s}\n"
        f"Общая сумма: {campaign.total_amount:.0f} ₽\n"
        f"Вам нужно перевести: {amount_per_user:.0f} ₽\n\n"
        f"Реквизиты:\n{service_payment_requisites()}\n\n"
        f"{campaign_progress_line(campaign)}\n\n"
        "Пришлите фото чека о переводе в этот чат.\n"
        "Список сборов — команда «сборы»."
    )


def offer_to_users(
    campaign: ServiceCampaign,
    user_ids: list[int],
    amount_per_user: Decimal,
    send_fn=None,
) -> int:
    """Create invites and notify users via send_fn(user, text)."""
    cfg = AppSettings.load()
    users = BotUser.objects.filter(id__in=user_ids)
    campaign.amount_per_user = amount_per_user
    campaign.save(update_fields=["amount_per_user"])
    sent = 0
    for user in users:
        invite, created = ServiceInvite.objects.get_or_create(
            campaign=campaign,
            user=user,
            defaults={
                "amount_due": amount_per_user,
                "status": InviteStatus.OFFERED,
            },
        )
        if not created and invite.status == InviteStatus.CANCELLED:
            invite.amount_due = amount_per_user
            invite.amount_paid = Decimal("0")
            invite.status = InviteStatus.OFFERED
            invite.save()
        elif not created:
            invite.amount_due = amount_per_user
            invite.save(update_fields=["amount_due"])

        text = offer_message(campaign, amount_per_user)
        ActivityLog.objects.create(
            user=user,
            kind=ActivityKind.SERVICE_OFFER,
            title="Предложение сервисного сбора",
            detail=campaign.title,
            meta={"campaign_id": campaign.id, "invite_id": invite.id},
        )
        if send_fn:
            try:
                send_fn(user, text)
                sent += 1
            except Exception:
                logger.exception("Failed to notify user %s about campaign", user.max_user_id)
        else:
            sent += 1

    campaign.status = CampaignStatus.ACTIVE
    campaign.save(update_fields=["status"])
    warn = cfg.tax_limit_warning()
    if warn:
        logger.warning(warn)
    return sent


def launch_campaign_to_group(
    *,
    category: str,
    title: str,
    description: str,
    group: ServiceGroup,
    total_amount: Decimal,
    amount_per_user: Decimal,
    send_fn=None,
) -> tuple[ServiceCampaign, int]:
    """Create campaign for a group and broadcast to all members."""
    members = list(group.members.values_list("id", flat=True))
    if not members:
        raise ValueError("В группе нет участников")
    if amount_per_user <= 0:
        raise ValueError("Укажите сумму с участника")
    if total_amount <= 0:
        raise ValueError("Укажите общую сумму")
    campaign = create_campaign(
        category=category,
        title=title,
        description=description,
        group=group,
        total_amount=total_amount,
        amount_per_user=amount_per_user,
    )
    sent = offer_to_users(campaign, members, amount_per_user, send_fn=send_fn)
    return campaign, sent


def invite_new_members_to_group_campaigns(
    group: ServiceGroup,
    user_ids: list[int],
    send_fn=None,
) -> int:
    """
    When users are added to a group, send them all active collections of this group.
    Does not notify existing members who already received the offers.
    """
    if not user_ids:
        return 0
    campaigns = ServiceCampaign.objects.filter(
        group=group,
        status=CampaignStatus.ACTIVE,
    )
    if not campaigns.exists():
        return 0

    sent = 0
    users = list(BotUser.objects.filter(id__in=user_ids))
    for campaign in campaigns:
        amount = Decimal(campaign.amount_per_user or 0)
        if amount <= 0:
            continue
        for user in users:
            existing = ServiceInvite.objects.filter(campaign=campaign, user=user).first()
            if existing and existing.status != InviteStatus.CANCELLED:
                continue
            if existing and existing.status == InviteStatus.CANCELLED:
                existing.amount_due = amount
                existing.amount_paid = Decimal("0")
                existing.status = InviteStatus.OFFERED
                existing.save()
                invite = existing
            else:
                invite = ServiceInvite.objects.create(
                    campaign=campaign,
                    user=user,
                    amount_due=amount,
                    status=InviteStatus.OFFERED,
                )
            text = offer_message(campaign, amount)
            ActivityLog.objects.create(
                user=user,
                kind=ActivityKind.SERVICE_OFFER,
                title="Сбор отправлен новому участнику группы",
                detail=campaign.title,
                meta={
                    "campaign_id": campaign.id,
                    "invite_id": invite.id,
                    "group_id": group.id,
                },
            )
            if send_fn:
                try:
                    send_fn(user, text)
                    sent += 1
                except Exception:
                    logger.exception(
                        "Failed to notify new member %s about campaign %s",
                        user.max_user_id,
                        campaign.id,
                    )
            else:
                sent += 1
    return sent


def open_invites_for_user(user: BotUser) -> list[ServiceInvite]:
    return list(
        ServiceInvite.objects.filter(
            user=user,
            status=InviteStatus.OFFERED,
            campaign__status=CampaignStatus.ACTIVE,
        )
        .select_related("campaign")
        .order_by("-offered_at")
    )


def submit_service_receipt(
    user: BotUser,
    image_bytes: bytes,
    *,
    invite: ServiceInvite | None = None,
    filename: str = "receipt.jpg",
) -> ServiceReceipt:
    invites = open_invites_for_user(user)
    if invite is None:
        if len(invites) == 1:
            invite = invites[0]
        elif not invites:
            raise ValueError("Нет активных сервисных сборов для оплаты.")
        else:
            raise ValueError("SEVERAL_INVITES")

    ocr_text = ocr_image_bytes(image_bytes)
    cfg = AppSettings.load()
    parsed = analyze_receipt_text(
        ocr_text,
        expected_phone=cfg.service_payee_phone,
        expected_name=cfg.service_payee_name,
    )
    # Also accept short form «Григорьев Д.В.»
    phone_ok = normalize_phone(parsed.recipient_phone) == normalize_phone(
        cfg.service_payee_phone
    ) or normalize_phone(cfg.service_payee_phone) in normalize_phone(ocr_text)
    name_blob = (parsed.recipient_name + " " + ocr_text).lower()
    name_ok = "григорьев" in name_blob or parsed.details_match
    details_match = bool(phone_ok and name_ok and parsed.amount)

    receipt = ServiceReceipt(
        invite=invite,
        campaign=invite.campaign,
        user=user,
        ocr_text=parsed.ocr_text,
        amount=parsed.amount,
        transfer_date=parsed.transfer_date,
        recipient_phone=parsed.recipient_phone,
        recipient_name=parsed.recipient_name,
        status=ReceiptStatus.PENDING,
        ai_notes=parsed.notes,
        details_match=details_match,
    )
    media_name = f"{user.max_user_id}_{timezone.now().strftime('%Y%m%d_%H%M%S')}_{filename}"
    receipt.image.save(Path(media_name).name, ContentFile(image_bytes), save=False)
    receipt.save()
    ActivityLog.objects.create(
        user=user,
        kind=ActivityKind.SERVICE_RECEIPT,
        title="Чек сервисного сбора",
        detail=f"{invite.campaign.title}: {parsed.amount}",
        meta={"receipt_id": receipt.id, "invite_id": invite.id},
    )
    return receipt


def approve_service_receipt(receipt: ServiceReceipt, comment: str = "") -> ServiceReceipt:
    if receipt.status == ReceiptStatus.APPROVED:
        return receipt
    amount = Decimal(receipt.amount or 0)
    if amount <= 0:
        raise ValueError("Нельзя принять чек без суммы.")

    cfg = AppSettings.load()
    warn = cfg.tax_limit_warning()
    # Still allow but track

    invite = receipt.invite
    invite.amount_paid = Decimal(invite.amount_paid or 0) + amount
    if invite.amount_paid >= invite.amount_due:
        invite.status = InviteStatus.PAID
        invite.paid_at = timezone.now()
    invite.save()

    cfg.service_tax_collected = Decimal(cfg.service_tax_collected or 0) + amount
    cfg.save(update_fields=["service_tax_collected", "updated_at"])

    receipt.status = ReceiptStatus.APPROVED
    receipt.admin_comment = comment
    receipt.reviewed_at = timezone.now()
    receipt.save()

    ActivityLog.objects.create(
        user=receipt.user,
        kind=ActivityKind.SERVICE_PAID,
        title="Сервисный чек принят",
        detail=f"+{amount} ₽ → {invite.campaign.title}",
        meta={"receipt_id": receipt.id},
    )
    return receipt


def reject_service_receipt(receipt: ServiceReceipt, comment: str = "") -> ServiceReceipt:
    receipt.status = ReceiptStatus.REJECTED
    receipt.admin_comment = comment
    receipt.reviewed_at = timezone.now()
    receipt.save()
    return receipt


def approved_service_message(receipt: ServiceReceipt) -> str:
    inv = receipt.invite
    return (
        f"Чек по мероприятию «{inv.campaign.title}» принят.\n"
        f"Зачтено: {receipt.amount} ₽.\n"
        f"{campaign_progress_line(inv.campaign)}\n"
        f"Ваш статус взноса: "
        f"{'оплачено' if inv.status == InviteStatus.PAID else 'частично, можно дослать чек'}."
    )


def rejected_service_message(receipt: ServiceReceipt) -> str:
    extra = f"\nКомментарий: {receipt.admin_comment}" if receipt.admin_comment else ""
    return (
        f"Чек по мероприятию «{receipt.campaign.title}» отклонён.{extra}\n\n"
        f"{service_payment_requisites()}\n"
        "Пришлите корректный чек ещё раз."
    )
