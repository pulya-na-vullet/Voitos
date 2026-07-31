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
    CampaignNoticeKind,
    CampaignStatus,
    InviteStatus,
    ReceiptStatus,
    ServiceCampaign,
    ServiceCampaignNotice,
    ServiceCategory,
    ServiceGroup,
    ServiceInvite,
    ServiceReceipt,
)
from subscriptions.receipts import analyze_receipt_text, normalize_phone, ocr_image_bytes

logger = logging.getLogger(__name__)

UNPAID_REMINDER_TEXT = (
    "Вы не оплатили сбор. Мероприятие по сбору может быть не выполнено из-за вас."
)


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


def campaign_collected(campaign: ServiceCampaign) -> Decimal:
    return (
        campaign.invites.aggregate(s=Sum("amount_paid"))["s"] or Decimal("0")
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
        if c.status == CampaignStatus.CLOSED:
            status = "сбор закрыт"
        elif inv.status == InviteStatus.PAID:
            status = "оплачено"
        elif inv.status == InviteStatus.DECLINED:
            status = "отказ"
        else:
            status = "ожидает оплаты"
        group_name = c.group.name if c.group_id else (c.locality or "")
        extra = f"{group_name}\n" if group_name else ""
        event_line = ""
        if c.event_at:
            event_line = f"Дата мероприятия: {timezone.localtime(c.event_at).strftime('%d.%m.%Y %H:%M')}\n"
        lines.append(
            f"\n{c.get_category_display()} — {c.title}\n"
            f"{extra}"
            f"{event_line}"
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
    event_at=None,
) -> ServiceCampaign:
    if category not in ServiceCategory.values:
        raise ValueError("Неизвестная категория")
    title = title.strip() or dict(ServiceCategory.choices).get(category, "Мероприятие")
    if event_at:
        date_s = timezone.localtime(event_at).strftime("%d.%m.%Y")
    else:
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
        event_at=event_at,
        status=CampaignStatus.DRAFT,
    )


def offer_message(campaign: ServiceCampaign, amount_per_user: Decimal) -> str:
    if campaign.event_at:
        date_s = timezone.localtime(campaign.event_at).strftime("%d.%m.%Y %H:%M")
        date_label = "Дата мероприятия"
    else:
        date_s = timezone.localtime(campaign.created_at).strftime("%d.%m.%Y")
        date_label = "Дата"
    group_line = ""
    if campaign.group_id:
        group_line = f"Группа: {campaign.group.name}\n"
    desc = f"{campaign.description}\n" if campaign.description else ""
    return (
        f"Начат сбор: {campaign.get_category_display()}\n"
        f"{campaign.title}\n"
        f"{desc}"
        f"{group_line}"
        f"{date_label}: {date_s}\n"
        f"Общая сумма: {campaign.total_amount:.0f} ₽\n"
        f"Вам нужно перевести: {amount_per_user:.0f} ₽\n\n"
        f"Реквизиты:\n{service_payment_requisites()}\n\n"
        f"{campaign_progress_line(campaign)}\n\n"
        "Пришлите фото чека о переводе в этот чат.\n"
        "Список сборов — команда «сборы»."
    )


def _notice_already_sent(campaign: ServiceCampaign, user: BotUser, kind: str) -> bool:
    return ServiceCampaignNotice.objects.filter(
        campaign=campaign, user=user, kind=kind
    ).exists()


def _record_notice(campaign: ServiceCampaign, user: BotUser, kind: str) -> None:
    ServiceCampaignNotice.objects.get_or_create(
        campaign=campaign,
        user=user,
        kind=kind,
    )


def broadcast_campaign_message(
    campaign: ServiceCampaign,
    text: str,
    send_fn,
    *,
    kind: str | None = None,
    users=None,
) -> int:
    """Send text to campaign invitees (or given users). Optionally de-dupe by kind."""
    if not send_fn:
        return 0
    if users is None:
        users = [inv.user for inv in campaign.invites.select_related("user").all()]
    sent = 0
    for user in users:
        if kind and _notice_already_sent(campaign, user, kind):
            continue
        try:
            send_fn(user, text)
            if kind:
                _record_notice(campaign, user, kind)
            sent += 1
            ActivityLog.objects.create(
                user=user,
                kind=ActivityKind.SERVICE_NOTICE,
                title="Уведомление по сбору",
                detail=text[:200],
                meta={"campaign_id": campaign.id, "kind": kind or ""},
            )
        except Exception:
            logger.exception(
                "Failed campaign broadcast to %s (campaign %s)",
                user.max_user_id,
                campaign.id,
            )
    return sent


def close_campaign_goal_reached(campaign: ServiceCampaign, send_fn=None) -> bool:
    """Close campaign when goal is met and notify everyone: «Сбор закрыт.»"""
    campaign.refresh_from_db()
    if campaign.status == CampaignStatus.CLOSED:
        return False
    paid = campaign_collected(campaign)
    if paid < Decimal(campaign.total_amount or 0):
        return False
    campaign.status = CampaignStatus.CLOSED
    campaign.closed_at = timezone.now()
    campaign.save(update_fields=["status", "closed_at"])
    broadcast_campaign_message(
        campaign,
        "Сбор закрыт.",
        send_fn,
        kind=CampaignNoticeKind.CLOSED,
    )
    return True


def maybe_notify_surplus(campaign: ServiceCampaign, send_fn=None) -> bool:
    """
    If approved amount exceeds the goal and there are no pending receipts left,
    notify that the leftover went to the general budget.
    """
    paid = campaign_collected(campaign)
    total = Decimal(campaign.total_amount or 0)
    if paid <= total:
        return False
    pending = campaign.receipts.filter(status=ReceiptStatus.PENDING).exists()
    if pending:
        return False
    surplus = paid - total
    text = (
        f"По сбору «{campaign.title}» подтверждена сумма сверх цели.\n"
        f"Собрано: {paid:.0f} ₽ при цели {total:.0f} ₽.\n"
        f"Оставшаяся часть ({surplus:.0f} ₽) ушла в общий бюджет."
    )
    sent = broadcast_campaign_message(
        campaign,
        text,
        send_fn,
        kind=CampaignNoticeKind.SURPLUS,
    )
    return sent > 0


def unpaid_reminder_message(campaign: ServiceCampaign) -> str:
    event = ""
    if campaign.event_at:
        event = (
            f"\nДата мероприятия: "
            f"{timezone.localtime(campaign.event_at).strftime('%d.%m.%Y %H:%M')}"
        )
    return (
        f"Сбор «{campaign.title}»{event}\n\n"
        f"{UNPAID_REMINDER_TEXT}\n\n"
        f"Ваш взнос: {campaign.amount_per_user:.0f} ₽\n"
        f"{service_payment_requisites()}\n"
        "Пришлите фото чека в этот чат."
    )


def process_unpaid_reminders(send_fn=None, *, now=None) -> int:
    """
    For active campaigns with event_at: remind unpaid users
    3 days, 1 day and 2 hours before the event.
    """
    from datetime import timedelta

    if not send_fn:
        return 0
    now = now or timezone.now()
    windows = (
        (CampaignNoticeKind.REMIND_3D, timedelta(days=3)),
        (CampaignNoticeKind.REMIND_1D, timedelta(days=1)),
        (CampaignNoticeKind.REMIND_2H, timedelta(hours=2)),
    )
    campaigns = ServiceCampaign.objects.filter(
        status=CampaignStatus.ACTIVE,
        event_at__isnull=False,
        event_at__gt=now,
    )
    sent_total = 0
    text_cache: dict[int, str] = {}
    for campaign in campaigns:
        for kind, delta in windows:
            trigger_at = campaign.event_at - delta
            if now < trigger_at:
                continue
            unpaid = (
                campaign.invites.filter(status=InviteStatus.OFFERED)
                .select_related("user")
            )
            for inv in unpaid:
                if _notice_already_sent(campaign, inv.user, kind):
                    continue
                if campaign.id not in text_cache:
                    text_cache[campaign.id] = unpaid_reminder_message(campaign)
                try:
                    send_fn(inv.user, text_cache[campaign.id])
                    _record_notice(campaign, inv.user, kind)
                    sent_total += 1
                    ActivityLog.objects.create(
                        user=inv.user,
                        kind=ActivityKind.SERVICE_NOTICE,
                        title="Напоминание об оплате сбора",
                        detail=campaign.title,
                        meta={"campaign_id": campaign.id, "kind": kind},
                    )
                except Exception:
                    logger.exception(
                        "Failed unpaid reminder %s to %s",
                        kind,
                        inv.user.max_user_id,
                    )
    return sent_total


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
    event_at=None,
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
    if event_at is None:
        raise ValueError("Укажите дату мероприятия")
    campaign = create_campaign(
        category=category,
        title=title,
        description=description,
        group=group,
        total_amount=total_amount,
        amount_per_user=amount_per_user,
        event_at=event_at,
    )
    sent = offer_to_users(campaign, members, amount_per_user, send_fn=send_fn)
    return campaign, sent


def user_groups_list_message(user: BotUser, *, added_group: ServiceGroup | None = None) -> str:
    """Inform user about a new group membership and list all their groups."""
    groups = list(user.service_groups.order_by("name"))
    lines = [f"• {g.name}" for g in groups] or ["• (групп пока нет)"]
    if added_group:
        header = f"Вас добавили в группу: «{added_group.name}».\n\nВаши группы:\n"
    else:
        header = "Ваши группы:\n"
    return header + "\n".join(lines)


def notify_members_added_to_group(
    group: ServiceGroup,
    user_ids: list[int],
    send_fn=None,
) -> int:
    """Send MAX notice to newly added members: which group + full group list."""
    if not user_ids or not send_fn:
        return 0
    sent = 0
    for user in BotUser.objects.filter(id__in=user_ids):
        text = user_groups_list_message(user, added_group=group)
        try:
            send_fn(user, text)
            sent += 1
            ActivityLog.objects.create(
                user=user,
                kind=ActivityKind.SERVICE_OFFER,
                title="Добавлен в группу",
                detail=group.name,
                meta={"group_id": group.id},
            )
        except Exception:
            logger.exception("Failed to notify user %s about group %s", user.max_user_id, group.id)
    return sent


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


def approve_service_receipt(
    receipt: ServiceReceipt,
    comment: str = "",
    send_fn=None,
) -> ServiceReceipt:
    if receipt.status == ReceiptStatus.APPROVED:
        return receipt
    amount = Decimal(receipt.amount or 0)
    if amount <= 0:
        raise ValueError("Нельзя принять чек без суммы.")

    cfg = AppSettings.load()

    invite = receipt.invite
    campaign = invite.campaign
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
        detail=f"+{amount} ₽ → {campaign.title}",
        meta={"receipt_id": receipt.id},
    )

    # Goal reached → close + «Сбор закрыт.» to everyone
    close_campaign_goal_reached(campaign, send_fn=send_fn)
    # Surplus after all receipts verified → general budget notice
    maybe_notify_surplus(campaign, send_fn=send_fn)
    return receipt


def reject_service_receipt(receipt: ServiceReceipt, comment: str = "") -> ServiceReceipt:
    receipt.status = ReceiptStatus.REJECTED
    receipt.admin_comment = comment
    receipt.reviewed_at = timezone.now()
    receipt.save()
    return receipt


def approved_service_message(receipt: ServiceReceipt) -> str:
    inv = receipt.invite
    campaign = inv.campaign
    paid = campaign_collected(campaign)
    total = Decimal(campaign.total_amount or 0)
    lines = [
        f"Чек по мероприятию «{campaign.title}» принят.",
        f"Зачтено: {receipt.amount} ₽.",
        campaign_progress_line(campaign),
        (
            "Ваш статус взноса: "
            f"{'оплачено' if inv.status == InviteStatus.PAID else 'частично, можно дослать чек'}."
        ),
    ]
    if campaign.status == CampaignStatus.CLOSED:
        lines.append("Сбор закрыт.")
    if paid > total and not campaign.receipts.filter(status=ReceiptStatus.PENDING).exists():
        surplus = paid - total
        lines.append(
            f"Оставшаяся часть ({surplus:.0f} ₽) ушла в общий бюджет."
        )
    return "\n".join(lines)


def rejected_service_message(receipt: ServiceReceipt) -> str:
    extra = f"\nКомментарий: {receipt.admin_comment}" if receipt.admin_comment else ""
    return (
        f"Чек по мероприятию «{receipt.campaign.title}» отклонён.{extra}\n\n"
        f"{service_payment_requisites()}\n"
        "Пришлите корректный чек ещё раз."
    )
