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
    ServiceCampaignOfferPhoto,
    ServiceCampaignResultPhoto,
    ServiceCategory,
    ServiceGroup,
    ServiceInvite,
    ServiceReceipt,
    WorkStage,
)

WORK_STAGE_ORDER = [
    WorkStage.COLLECTING,
    WorkStage.WORK_STARTED,
    WorkStage.WORK_DONE,
    WorkStage.WORK_CLOSED,
]

WORK_STAGE_NEXT = {
    WorkStage.COLLECTING: WorkStage.WORK_STARTED,
    WorkStage.WORK_STARTED: WorkStage.WORK_DONE,
    WorkStage.WORK_DONE: WorkStage.WORK_CLOSED,
}
from subscriptions.receipts import normalize_phone

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


def campaign_surplus(campaign: ServiceCampaign) -> Decimal:
    """Amount above the goal that goes to the group budget after close."""
    paid = campaign_collected(campaign)
    total = Decimal(campaign.total_amount or 0)
    if paid <= total:
        return Decimal("0")
    return paid - total


def delete_service_campaign(campaign: ServiceCampaign, *, reason: str = "") -> str:
    """
    Permanently delete a service campaign and related invites/receipts/photos.

    Returns a short human label of what was deleted.
    """
    reason = (reason or "").strip()
    title = str(campaign)
    campaign_id = campaign.id
    # Remove media files best-effort before CASCADE row delete
    for photo in list(campaign.offer_photos.all()):
        try:
            if photo.image:
                photo.image.delete(save=False)
        except Exception:
            logger.exception("Failed to delete offer photo %s", photo.id)
    for photo in list(campaign.result_photos.all()):
        try:
            if photo.image:
                photo.image.delete(save=False)
        except Exception:
            logger.exception("Failed to delete result photo %s", photo.id)
    for receipt in list(campaign.receipts.all()):
        try:
            if receipt.image:
                receipt.image.delete(save=False)
        except Exception:
            logger.exception("Failed to delete service receipt file %s", receipt.id)

    campaign.delete()
    try:
        from services.tax import sync_self_employed_tax_collected

        sync_self_employed_tax_collected()
    except Exception:
        logger.exception("Failed to sync tax after campaign delete #%s", campaign_id)
    logger.info(
        "Deleted service campaign #%s (%s). Reason: %s",
        campaign_id,
        title,
        reason or "—",
    )
    return title


def group_accumulated_budget(group: ServiceGroup) -> Decimal:
    """
    Budget accumulated for a resident group: sum of surpluses from closed
    campaigns (collected over the goal → «общий бюджет»).
    """
    total = Decimal("0")
    closed = (
        ServiceCampaign.objects.filter(group=group, status=CampaignStatus.CLOSED)
        .prefetch_related("invites")
        .all()
    )
    for campaign in closed:
        total += campaign_surplus(campaign)
    return total


def budgets_by_group_ids(group_ids: list[int]) -> dict[int, Decimal]:
    """Map group_id -> accumulated surplus budget for closed campaigns."""
    if not group_ids:
        return {}
    result = {gid: Decimal("0") for gid in group_ids}
    closed = (
        ServiceCampaign.objects.filter(
            group_id__in=group_ids, status=CampaignStatus.CLOSED
        )
        .prefetch_related("invites")
        .all()
    )
    for campaign in closed:
        if campaign.group_id is None:
            continue
        result[campaign.group_id] = result.get(campaign.group_id, Decimal("0")) + campaign_surplus(
            campaign
        )
    return result


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
    needs_snow_haul: bool = False,
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
    haul = bool(needs_snow_haul) and category == ServiceCategory.SNOW
    return ServiceCampaign.objects.create(
        category=category,
        title=title,
        description=description.strip(),
        locality=(locality or "").strip(),
        group=group,
        total_amount=total_amount,
        amount_per_user=amount_per_user or Decimal("0"),
        event_at=event_at,
        needs_snow_haul=haul,
        status=CampaignStatus.DRAFT,
    )


def _campaign_date_lines(campaign: ServiceCampaign) -> tuple[str, str, str]:
    if campaign.event_at:
        date_s = timezone.localtime(campaign.event_at).strftime("%d.%m.%Y %H:%M")
        date_label = "Дата мероприятия"
    else:
        date_s = timezone.localtime(campaign.created_at).strftime("%d.%m.%Y")
        date_label = "Дата"
    group_line = f"Группа: {campaign.group.name}\n" if campaign.group_id else ""
    desc = f"{campaign.description}\n" if campaign.description else ""
    return date_s, date_label, group_line + desc


def offer_message(campaign: ServiceCampaign, amount_per_user: Decimal) -> str:
    date_s, date_label, extra = _campaign_date_lines(campaign)
    photo_n = campaign.offer_photos.count() if campaign.pk else 0
    photo_line = (
        f"\nК сообщению приложены фото того, что нужно сделать ({photo_n}).\n"
        if photo_n
        else ""
    )
    return (
        f"Начат сбор: {campaign.get_category_display()}\n"
        f"{campaign.title}\n"
        f"{extra}"
        f"{date_label}: {date_s}\n"
        f"Общая сумма: {campaign.total_amount:.0f} ₽\n"
        f"Вам нужно перевести: {amount_per_user:.0f} ₽\n"
        f"{photo_line}\n"
        f"Реквизиты:\n{service_payment_requisites()}\n\n"
        f"{campaign_progress_line(campaign)}\n\n"
        "Пришлите фото чека о переводе в этот чат.\n"
        "Список сборов — команда «сборы»."
    )


def save_offer_photos(
    campaign: ServiceCampaign,
    uploads: list[tuple[bytes, str]],
    *,
    max_photos: int = 2,
) -> list[ServiceCampaignOfferPhoto]:
    """Save up to max_photos task photos attached at campaign launch."""
    existing = campaign.offer_photos.count()
    remaining = max(0, max_photos - existing)
    saved: list[ServiceCampaignOfferPhoto] = []
    for raw, filename in uploads[:remaining]:
        if not raw:
            continue
        photo = ServiceCampaignOfferPhoto(campaign=campaign)
        safe_name = Path(filename or "task.jpg").name
        media_name = (
            f"{campaign.id}_{timezone.now().strftime('%Y%m%d_%H%M%S')}_{safe_name}"
        )
        photo.image.save(media_name, ContentFile(raw), save=False)
        photo.save()
        saved.append(photo)
    return saved


def campaign_offer_image_payloads(
    campaign: ServiceCampaign,
) -> list[tuple[bytes, str]]:
    payloads: list[tuple[bytes, str]] = []
    for photo in campaign.offer_photos.all()[:2]:
        try:
            with photo.image.open("rb") as fh:
                payloads.append((fh.read(), Path(photo.image.name).name))
        except Exception:
            logger.exception("Could not read offer photo %s", photo.id)
    return payloads


def resend_offer_message(campaign: ServiceCampaign, amount_per_user: Decimal) -> str:
    """Reminder text for admin «Повторить рассылку» — only for unpaid users."""
    date_s, date_label, extra = _campaign_date_lines(campaign)
    return (
        f"Напоминаю вам, что идёт сбор: {campaign.get_category_display()}\n"
        f"{campaign.title}\n"
        f"{extra}"
        f"{date_label}: {date_s}\n"
        f"Общая сумма: {campaign.total_amount:.0f} ₽\n"
        f"Вам нужно перевести: {amount_per_user:.0f} ₽\n\n"
        f"Реквизиты:\n{service_payment_requisites()}\n\n"
        f"{campaign_progress_line(campaign)}\n\n"
        "Если вы ещё не оплатили — пришлите фото чека в этот чат.\n"
        "Список сборов — команда «сборы»."
    )


def resend_to_unpaid(campaign: ServiceCampaign, send_fn=None, send_media_fn=None) -> int:
    """
    Admin resend: remind only users who have not paid yet.
    Does not message invitees with status PAID.
    """
    if campaign.status == CampaignStatus.CLOSED:
        return 0
    amount = Decimal(campaign.amount_per_user or 0)
    if amount <= 0:
        return 0
    unpaid = (
        campaign.invites.filter(status=InviteStatus.OFFERED)
        .select_related("user")
    )
    text = resend_offer_message(campaign, amount)
    image_payloads = campaign_offer_image_payloads(campaign)
    sent = 0
    for inv in unpaid:
        ActivityLog.objects.create(
            user=inv.user,
            kind=ActivityKind.SERVICE_NOTICE,
            title="Повторная рассылка сбора",
            detail=campaign.title,
            meta={"campaign_id": campaign.id, "invite_id": inv.id},
        )
        try:
            if image_payloads and send_media_fn:
                send_media_fn(inv.user, text, image_payloads)
                sent += 1
            elif send_fn:
                send_fn(inv.user, text)
                sent += 1
            else:
                sent += 1
        except Exception:
            logger.exception(
                "Failed resend to user %s about campaign %s",
                inv.user.max_user_id,
                campaign.id,
            )
    return sent


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
    """Close money collection when goal is met and notify everyone: «Сбор закрыт.»"""
    campaign.refresh_from_db()
    if campaign.status == CampaignStatus.CLOSED:
        return False
    paid = campaign_collected(campaign)
    if paid < Decimal(campaign.total_amount or 0):
        return False
    campaign.status = CampaignStatus.CLOSED
    campaign.save(update_fields=["status"])
    broadcast_campaign_message(
        campaign,
        "Сбор закрыт.",
        send_fn,
        kind=CampaignNoticeKind.CLOSED,
    )
    return True


def work_done_message(campaign: ServiceCampaign, photo_count: int = 0) -> str:
    extra = ""
    if photo_count:
        extra = f"\nПриложены фото результата: {photo_count}."
    return (
        f"Работа выполнена по мероприятию «{campaign.title}».{extra}\n"
        "Спасибо за участие!"
    )


def save_result_photos(
    campaign: ServiceCampaign,
    uploads: list[tuple[bytes, str]],
) -> list[ServiceCampaignResultPhoto]:
    """Save up to 2 result photos. Existing photos are kept; total capped at 2."""
    existing = campaign.result_photos.count()
    remaining = max(0, 2 - existing)
    saved: list[ServiceCampaignResultPhoto] = []
    for raw, filename in uploads[:remaining]:
        if not raw:
            continue
        photo = ServiceCampaignResultPhoto(campaign=campaign)
        safe_name = Path(filename or "result.jpg").name
        media_name = (
            f"{campaign.id}_{timezone.now().strftime('%Y%m%d_%H%M%S')}_{safe_name}"
        )
        photo.image.save(media_name, ContentFile(raw), save=False)
        photo.save()
        saved.append(photo)
    return saved


def advance_work_stage(
    campaign: ServiceCampaign,
    *,
    photo_uploads: list[tuple[bytes, str]] | None = None,
    send_fn=None,
    send_media_fn=None,
    allow_close_without_payout: bool = False,
) -> WorkStage:
    """
    Move campaign to the next work stage.
    On WORK_DONE: optional 1–2 photos and broadcast to all invitees.
    On WORK_CLOSED: finalize campaign (requires contractor payout receipts
    when there are accepted assignees, unless allow_close_without_payout).
    """
    current = campaign.work_stage or WorkStage.COLLECTING
    nxt = WORK_STAGE_NEXT.get(current)
    if not nxt:
        raise ValueError("Работы уже закрыты")

    photo_uploads = photo_uploads or []
    if len(photo_uploads) > 2:
        raise ValueError("Можно приложить не больше 2 фотографий")

    if nxt == WorkStage.WORK_CLOSED:
        from services.contractors import accepted_assignments_needing_payout

        needing = accepted_assignments_needing_payout(campaign)
        if needing and not allow_close_without_payout:
            names = ", ".join(str(a.contractor) for a in needing)
            raise ValueError(
                "Перед закрытием приложите чек перевода денег исполнителям: "
                + names
            )

    saved_photos: list[ServiceCampaignResultPhoto] = []
    if nxt == WorkStage.WORK_DONE and photo_uploads:
        saved_photos = save_result_photos(campaign, photo_uploads)

    campaign.work_stage = nxt
    campaign.work_stage_changed_at = timezone.now()
    update_fields = ["work_stage", "work_stage_changed_at"]
    if nxt == WorkStage.WORK_CLOSED:
        campaign.status = CampaignStatus.CLOSED
        campaign.closed_at = timezone.now()
        update_fields.extend(["status", "closed_at"])
    campaign.save(update_fields=update_fields)

    if nxt == WorkStage.WORK_DONE:
        text = work_done_message(campaign, photo_count=len(saved_photos))
        image_payloads = []
        for photo in saved_photos:
            try:
                with photo.image.open("rb") as fh:
                    image_payloads.append(
                        (fh.read(), Path(photo.image.name).name)
                    )
            except Exception:
                logger.exception("Could not read result photo %s", photo.id)
        users = [inv.user for inv in campaign.invites.select_related("user").all()]
        for user in users:
            try:
                if image_payloads and send_media_fn:
                    send_media_fn(user, text, image_payloads)
                elif send_fn:
                    send_fn(user, text)
                ActivityLog.objects.create(
                    user=user,
                    kind=ActivityKind.SERVICE_NOTICE,
                    title="Работа выполнена",
                    detail=campaign.title,
                    meta={
                        "campaign_id": campaign.id,
                        "photos": len(image_payloads),
                    },
                )
            except Exception:
                logger.exception(
                    "Failed work-done notice to %s (campaign %s)",
                    user.max_user_id,
                    campaign.id,
                )

    return WorkStage(nxt)


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

    Windows that already passed before the invite was created are skipped
    (иначе при старте сбора «через 30 минут» уходят сразу 3 одинаковых
    напоминания: за 3 дня, за 1 день и за 2 часа).
    """
    from datetime import timedelta

    if not send_fn:
        return 0
    now = now or timezone.now()
    # От более срочного к более раннему — за один проход не больше одного
    # напоминания на (сбор, пользователь).
    windows = (
        (CampaignNoticeKind.REMIND_2H, timedelta(hours=2)),
        (CampaignNoticeKind.REMIND_1D, timedelta(days=1)),
        (CampaignNoticeKind.REMIND_3D, timedelta(days=3)),
    )
    campaigns = ServiceCampaign.objects.filter(
        status=CampaignStatus.ACTIVE,
        event_at__isnull=False,
        event_at__gt=now,
    )
    sent_total = 0
    text_cache: dict[int, str] = {}
    for campaign in campaigns:
        unpaid = (
            campaign.invites.filter(status=InviteStatus.OFFERED)
            .select_related("user")
        )
        for inv in unpaid:
            offered_at = inv.offered_at or campaign.created_at or now
            sent_for_user = False
            for kind, delta in windows:
                trigger_at = campaign.event_at - delta
                if now < trigger_at:
                    continue
                # Окно уже наступило до приглашения — не догоняем задним числом.
                if offered_at and trigger_at < offered_at:
                    continue
                if _notice_already_sent(campaign, inv.user, kind):
                    continue
                if campaign.id not in text_cache:
                    text_cache[campaign.id] = unpaid_reminder_message(campaign)
                try:
                    send_fn(inv.user, text_cache[campaign.id])
                    _record_notice(campaign, inv.user, kind)
                    sent_total += 1
                    sent_for_user = True
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
                if sent_for_user:
                    break
    return sent_total


def offer_to_users(
    campaign: ServiceCampaign,
    user_ids: list[int],
    amount_per_user: Decimal,
    send_fn=None,
    send_media_fn=None,
) -> int:
    """Create invites and notify users via send_fn / send_media_fn(user, text, images)."""
    cfg = AppSettings.load()
    users = BotUser.objects.filter(id__in=user_ids)
    campaign.amount_per_user = amount_per_user
    campaign.save(update_fields=["amount_per_user"])
    image_payloads = campaign_offer_image_payloads(campaign)
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
            meta={
                "campaign_id": campaign.id,
                "invite_id": invite.id,
                "photos": len(image_payloads),
            },
        )
        try:
            if image_payloads and send_media_fn:
                send_media_fn(user, text, image_payloads)
                sent += 1
            elif send_fn:
                send_fn(user, text)
                sent += 1
            else:
                sent += 1
        except Exception:
            logger.exception("Failed to notify user %s about campaign", user.max_user_id)

        from services.volunteer import ask_volunteer_help

        ask_volunteer_help(campaign, user, send_fn=send_fn)

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
    needs_snow_haul: bool = False,
    photo_uploads: list[tuple[bytes, str]] | None = None,
    send_fn=None,
    send_media_fn=None,
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
    photo_uploads = photo_uploads or []
    if len(photo_uploads) > 2:
        raise ValueError("Можно приложить не больше 2 фотографий")
    campaign = create_campaign(
        category=category,
        title=title,
        description=description,
        group=group,
        total_amount=total_amount,
        amount_per_user=amount_per_user,
        event_at=event_at,
        needs_snow_haul=needs_snow_haul,
    )
    if photo_uploads:
        save_offer_photos(campaign, photo_uploads)
    sent = offer_to_users(
        campaign,
        members,
        amount_per_user,
        send_fn=send_fn,
        send_media_fn=send_media_fn,
    )
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
    send_media_fn=None,
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
            image_payloads = campaign_offer_image_payloads(campaign)
            ActivityLog.objects.create(
                user=user,
                kind=ActivityKind.SERVICE_OFFER,
                title="Сбор отправлен новому участнику группы",
                detail=campaign.title,
                meta={
                    "campaign_id": campaign.id,
                    "invite_id": invite.id,
                    "group_id": group.id,
                    "photos": len(image_payloads),
                },
            )
            try:
                if image_payloads and send_media_fn:
                    send_media_fn(user, text, image_payloads)
                    sent += 1
                elif send_fn:
                    send_fn(user, text)
                    sent += 1
                else:
                    sent += 1
            except Exception:
                logger.exception(
                    "Failed to notify new member %s about campaign %s",
                    user.max_user_id,
                    campaign.id,
                )
            from services.volunteer import ask_volunteer_help

            ask_volunteer_help(campaign, user, send_fn=send_fn)
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


def format_receipt_pick_menu(invites: list[ServiceInvite]) -> str:
    """Numbered menu: 1 = subscription, then open service invites."""
    lines = [
        "К чему относится этот чек? Ответьте номером:",
        "1. Подписка",
    ]
    for i, inv in enumerate(invites, 2):
        lines.append(
            f"{i}. {inv.campaign.get_category_display()} — {inv.campaign.title} "
            f"({inv.amount_due:.0f} ₽)"
        )
    return "\n".join(lines)


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

    from subscriptions.service import _parse_receipt_or_empty, _safe_receipt_filename

    filename = _safe_receipt_filename(filename, default="receipt.pdf")
    parsed = _parse_receipt_or_empty(image_bytes, filename)
    ocr_text = parsed.ocr_text or ""
    cfg = AppSettings.load()
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
    receipt.image.save(media_name, ContentFile(image_bytes), save=False)
    receipt.save()
    ActivityLog.objects.create(
        user=user,
        kind=ActivityKind.SERVICE_RECEIPT,
        title="Чек сервисного сбора",
        detail=f"{invite.campaign.title}: {parsed.amount}",
        meta={"receipt_id": receipt.id, "invite_id": invite.id},
    )
    try:
        from panel.admin_tasks import task_service_receipt

        task_service_receipt(receipt)
    except Exception:
        logger.exception("Failed to create admin task for service receipt")
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

    invite = receipt.invite
    campaign = invite.campaign
    invite.amount_paid = Decimal(invite.amount_paid or 0) + amount
    if invite.amount_paid >= invite.amount_due:
        invite.status = InviteStatus.PAID
        invite.paid_at = timezone.now()
    invite.save()

    receipt.status = ReceiptStatus.APPROVED
    receipt.admin_comment = comment
    receipt.reviewed_at = timezone.now()
    receipt.save()

    from services.tax import sync_self_employed_tax_collected

    sync_self_employed_tax_collected()

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
    try:
        from panel.admin_tasks import task_service_receipt

        task_service_receipt(receipt)
    except Exception:
        logger.exception("Failed to close admin task for service receipt")
    return receipt


def reject_service_receipt(receipt: ServiceReceipt, comment: str = "") -> ServiceReceipt:
    receipt.status = ReceiptStatus.REJECTED
    receipt.admin_comment = comment
    receipt.reviewed_at = timezone.now()
    receipt.save()
    try:
        from services.tax import sync_self_employed_tax_collected

        sync_self_employed_tax_collected()
    except Exception:
        logger.exception("Failed to sync self-employed tax after service reject")
    try:
        from panel.admin_tasks import task_service_receipt

        task_service_receipt(receipt)
    except Exception:
        logger.exception("Failed to close admin task for rejected service receipt")
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
