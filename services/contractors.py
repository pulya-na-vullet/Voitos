"""Исполнители (трактор / камаз): назначения на сервисные задачи."""
from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from django.core.files.base import ContentFile
from django.db.models import Max
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from database.models import (
    ActivityKind,
    ActivityLog,
    AdminTaskKind,
    AssignmentStatus,
    BotUser,
    CampaignAssignment,
    ContractorPayout,
    ContractorProfile,
    ContractorStatus,
    EquipmentType,
    InviteStatus,
    ServiceCampaign,
    ServiceCategory,
)

logger = logging.getLogger(__name__)

COUNTER_OFFER_MINUTES = 20

_YES = {"да", "yes", "y", "+", "ага", "угу", "принято", "согласен", "ок", "1"}
_NO = {"нет", "no", "n", "-", "отказ", "отказаться", "2"}
_OTHER_TIME = {
    "другое время",
    "другое",
    "время",
    "изменить время",
    "3",
}


def verified_contractors(*, equipment_type: str | None = None):
    qs = ContractorProfile.objects.filter(
        status=ContractorStatus.VERIFIED
    ).select_related("user")
    if equipment_type:
        qs = qs.filter(equipment_type=equipment_type)
    return qs.order_by("equipment_type", "user__real_name", "id")


def suggested_equipment_for_campaign(campaign: ServiceCampaign) -> list[str]:
    """Типы техники, которые нужны для кампании."""
    if campaign.category == ServiceCategory.SNOW:
        types = [EquipmentType.TRACTOR]
        if campaign.needs_snow_haul:
            types.append(EquipmentType.TRUCK)
        return types
    if campaign.category == ServiceCategory.ROAD:
        return [EquipmentType.TRACTOR, EquipmentType.TRUCK]
    # Площадка / освещение — исполнители из жителей группы, не техника.
    return []


def _fmt_dt(dt) -> str:
    if not dt:
        return "—"
    return timezone.localtime(dt).strftime("%d.%m.%Y %H:%M")


def offer_message(assignment: CampaignAssignment) -> str:
    c = assignment.campaign
    eq = assignment.get_equipment_type_display()
    when = _fmt_dt(assignment.scheduled_at or c.event_at)
    return (
        f"Вам предложен заказ как исполнителю ({eq}).\n"
        f"Задача: {c.title}\n"
        f"Категория: {c.get_category_display()}\n"
        f"Время: {when}\n"
        f"{(c.description or '').strip()}\n\n"
        "Ответьте:\n"
        "1 / да — согласен на это время\n"
        "2 / нет — отказаться\n"
        "3 / другое время — предложить своё время "
        f"(у вас будет {COUNTER_OFFER_MINUTES} минут; "
        "администратор подтвердит или отклонит)"
    )


_PEER_ACTIVE_STATUSES = {
    AssignmentStatus.OFFERED,
    AssignmentStatus.COUNTER_OFFER,
    AssignmentStatus.ACCEPTED,
}


def max_profile_link(user: BotUser) -> str:
    """Публичная ссылка на профиль в MAX по username, если он есть."""
    uname = (user.username or "").strip().lstrip("@")
    if not uname:
        return ""
    return f"https://max.ru/{uname}"


def contractor_contact_lines(assignment: CampaignAssignment) -> list[str]:
    contractor = assignment.contractor
    user = contractor.user
    phone = (contractor.phone or user.phone or "").strip()
    uname = (user.username or "").strip().lstrip("@")
    link = max_profile_link(user)
    lines = [f"{user} ({assignment.get_equipment_type_display()})"]
    if phone:
        lines.append(f"Телефон: {phone}")
    if uname:
        lines.append(f"MAX: @{uname}")
    if link:
        lines.append(f"Ссылка MAX: {link}")
    if not phone and not uname:
        lines.append("Контакты в анкете не указаны — уточните у администратора.")
    return lines


def peers_contacts_message(
    campaign: ServiceCampaign,
    peers: list[CampaignAssignment],
) -> str:
    lines = [
        f"На задаче «{campaign.title}» назначено несколько исполнителей.",
        "Контакты коллег для связи:",
        "",
    ]
    for i, peer in enumerate(peers, start=1):
        block = contractor_contact_lines(peer)
        lines.append(f"{i}. {block[0]}")
        lines.extend(block[1:])
        lines.append("")
    return "\n".join(lines).rstrip()


def active_peer_assignments(campaign: ServiceCampaign) -> list[CampaignAssignment]:
    return list(
        campaign.assignments.filter(status__in=_PEER_ACTIVE_STATUSES)
        .select_related("contractor", "contractor__user")
        .order_by("sort_order", "id")
    )


def notify_peer_contractors(
    campaign: ServiceCampaign,
    *,
    send_fn=None,
) -> int:
    """Если на задаче ≥2 исполнителей — разослать им контакты друг друга."""
    if send_fn is None:
        send_fn = _default_send_fn()
    if not send_fn:
        return 0
    active = active_peer_assignments(campaign)
    if len(active) < 2:
        return 0
    sent = 0
    for assignment in active:
        peers = [a for a in active if a.id != assignment.id]
        if not peers:
            continue
        text = peers_contacts_message(campaign, peers)
        try:
            send_fn(assignment.contractor.user, text)
            sent += 1
            ActivityLog.objects.create(
                user=assignment.contractor.user,
                kind=ActivityKind.CONTRACTOR_OFFER,
                title="Контакты коллег-исполнителей",
                detail=f"{campaign.title}: {len(peers)} чел.",
                meta={
                    "campaign_id": campaign.id,
                    "assignment_id": assignment.id,
                    "peer_ids": [p.id for p in peers],
                },
            )
        except Exception:
            logger.exception(
                "Failed to notify peer contacts for contractor %s",
                assignment.contractor_id,
            )
    return sent


def assign_contractor(
    campaign: ServiceCampaign,
    contractor: ContractorProfile,
    *,
    scheduled_at=None,
    send_fn=None,
) -> CampaignAssignment:
    if contractor.status != ContractorStatus.VERIFIED:
        raise ValueError("Исполнитель ещё не проверен администратором")

    when = scheduled_at or campaign.event_at
    if when is None:
        raise ValueError("Укажите время работ")

    order = (
        campaign.assignments.aggregate(m=Max("sort_order")).get("m") or 0
    ) + 1
    assignment, created = CampaignAssignment.objects.get_or_create(
        campaign=campaign,
        contractor=contractor,
        defaults={
            "equipment_type": contractor.equipment_type,
            "status": AssignmentStatus.OFFERED,
            "scheduled_at": when,
            "sort_order": order,
        },
    )
    if not created:
        if assignment.status in {
            AssignmentStatus.ACCEPTED,
            AssignmentStatus.OFFERED,
            AssignmentStatus.COUNTER_OFFER,
        }:
            raise ValueError("Этот исполнитель уже назначен на задачу")
        assignment.equipment_type = contractor.equipment_type
        assignment.status = AssignmentStatus.OFFERED
        assignment.scheduled_at = when
        assignment.proposed_at = None
        assignment.counter_deadline = None
        assignment.responded_at = None
        assignment.accepted_at = None
        assignment.residents_notified_at = None
        assignment.sort_order = order
        assignment.save()

    text = offer_message(assignment)
    ActivityLog.objects.create(
        user=contractor.user,
        kind=ActivityKind.CONTRACTOR_OFFER,
        title="Предложение исполнителю",
        detail=f"{campaign.title} @ {_fmt_dt(when)}",
        meta={"assignment_id": assignment.id, "campaign_id": campaign.id},
    )
    if send_fn:
        try:
            send_fn(contractor.user, text)
        except Exception:
            logger.exception("Failed to notify contractor %s", contractor.id)

    # Pending reply state for bot
    from database.models import PendingAction

    pending, _ = PendingAction.objects.get_or_create(user=contractor.user)
    pending.pending_kind = "contractor_offer_reply"
    pending.pending_payload = {"assignment_id": assignment.id}
    pending.save(update_fields=["pending_kind", "pending_payload", "updated_at"])

    # Несколько исполнителей на одной задаче — обмен контактами и ссылками MAX.
    try:
        notify_peer_contractors(campaign, send_fn=send_fn)
    except Exception:
        logger.exception("Failed to share peer contacts for campaign %s", campaign.id)

    return assignment


def parse_user_datetime(text: str):
    """Parse 'ДД.ММ.ГГГГ ЧЧ:ММ' or ISO-ish from contractor."""
    raw = (text or "").strip()
    if not raw:
        return None
    # DD.MM.YYYY HH:MM
    for fmt in ("%d.%m.%Y %H:%M", "%d.%m.%Y %H:%M:%S", "%d.%m.%y %H:%M"):
        try:
            from datetime import datetime as dt_cls

            dt = dt_cls.strptime(raw, fmt)
            if timezone.is_naive(dt):
                dt = timezone.make_aware(dt, timezone.get_current_timezone())
            return dt
        except ValueError:
            pass
    normalized = raw.replace(" ", "T")
    if len(normalized) == 16:
        normalized += ":00"
    dt = parse_datetime(normalized)
    if dt is not None and timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_current_timezone())
    return dt


def handle_offer_reply(
    user: BotUser,
    text: str,
    pending,
) -> str | None:
    payload = pending.pending_payload or {}
    assignment_id = payload.get("assignment_id")
    if not assignment_id:
        pending.clear_pending()
        return None
    assignment = (
        CampaignAssignment.objects.select_related("campaign", "contractor", "contractor__user")
        .filter(pk=assignment_id, contractor__user=user)
        .first()
    )
    if assignment is None:
        pending.clear_pending()
        return "Предложение уже неактуально."

    if assignment.status not in {AssignmentStatus.OFFERED, AssignmentStatus.COUNTER_OFFER}:
        pending.clear_pending()
        return f"По этому заказу статус: {assignment.get_status_display()}."

    low = (text or "").strip().lower()
    step = payload.get("step") or "choose"

    if step == "await_time":
        when = parse_user_datetime(text)
        if when is None:
            return (
                "Не разобрал время. Напишите в формате:\n"
                "31.12.2026 09:30"
            )
        return _submit_counter_offer(assignment, when, pending)

    if low in _YES or low.startswith("1"):
        return accept_assignment(assignment, pending=pending)
    if low in _NO or low.startswith("2"):
        return decline_assignment(assignment, pending=pending)
    if low in _OTHER_TIME or "другое" in low:
        pending.pending_payload = {
            "assignment_id": assignment.id,
            "step": "await_time",
        }
        pending.save(update_fields=["pending_payload", "updated_at"])
        return (
            f"Напишите удобное время (ДД.ММ.ГГГГ ЧЧ:ММ).\n"
            f"Администратор ответит в течение {COUNTER_OFFER_MINUTES} минут."
        )
    # Maybe they sent datetime directly
    when = parse_user_datetime(text)
    if when is not None:
        return _submit_counter_offer(assignment, when, pending)
    return (
        "Ответьте: да — согласиться, нет — отказаться, "
        "или «другое время» чтобы предложить своё."
    )


def _submit_counter_offer(assignment: CampaignAssignment, when, pending) -> str:
    now = timezone.now()
    assignment.status = AssignmentStatus.COUNTER_OFFER
    assignment.proposed_at = when
    assignment.counter_deadline = now + timedelta(minutes=COUNTER_OFFER_MINUTES)
    assignment.responded_at = now
    assignment.save(
        update_fields=[
            "status",
            "proposed_at",
            "counter_deadline",
            "responded_at",
        ]
    )
    pending.clear_pending()
    ActivityLog.objects.create(
        user=assignment.contractor.user,
        kind=ActivityKind.CONTRACTOR_REPLY,
        title="Исполнитель предложил другое время",
        detail=_fmt_dt(when),
        meta={"assignment_id": assignment.id},
    )
    try:
        from panel.admin_tasks import upsert_task

        upsert_task(
            kind=AdminTaskKind.CONTRACTOR_COUNTER,
            title=(
                f"Другое время: {assignment.contractor} → "
                f"{assignment.campaign.title}"
            ),
            description=(
                f"Было: {_fmt_dt(assignment.scheduled_at)}\n"
                f"Предложено: {_fmt_dt(when)}\n"
                f"Ответить до: {_fmt_dt(assignment.counter_deadline)}"
            ),
            user=assignment.contractor.user,
            action_url=f"/panel/services/campaigns/{assignment.campaign_id}/",
            source_model="CampaignAssignment",
            source_id=assignment.id,
            priority=20,
            meta={
                "assignment_id": assignment.id,
                "proposed_at": when.isoformat(),
            },
        )
    except Exception:
        logger.exception("Failed to create counter-offer admin task")
    return (
        f"Предложил время {_fmt_dt(when)}. "
        f"Ждём решение администратора (до {_fmt_dt(assignment.counter_deadline)})."
    )


def accept_assignment(
    assignment: CampaignAssignment,
    *,
    pending=None,
    scheduled_at=None,
    send_fn=None,
    notify_residents: bool = True,
) -> str:
    now = timezone.now()
    if scheduled_at is not None:
        assignment.scheduled_at = scheduled_at
    assignment.status = AssignmentStatus.ACCEPTED
    assignment.accepted_at = now
    assignment.responded_at = assignment.responded_at or now
    assignment.proposed_at = None
    assignment.counter_deadline = None
    assignment.save()
    if pending is not None:
        pending.clear_pending()
    ActivityLog.objects.create(
        user=assignment.contractor.user,
        kind=ActivityKind.CONTRACTOR_REPLY,
        title="Исполнитель согласился",
        detail=_fmt_dt(assignment.scheduled_at),
        meta={"assignment_id": assignment.id},
    )
    try:
        from panel.admin_tasks import close_task_for_source

        close_task_for_source(
            AdminTaskKind.CONTRACTOR_COUNTER,
            "CampaignAssignment",
            assignment.id,
        )
    except Exception:
        logger.exception("Failed to close counter task")

    if notify_residents:
        notify_residents_about_assignments(
            assignment.campaign, send_fn=send_fn
        )
    return (
        f"Принято. Время работ: {_fmt_dt(assignment.scheduled_at)}. "
        "Жителям группы уйдёт уведомление."
    )


def decline_assignment(assignment: CampaignAssignment, *, pending=None) -> str:
    assignment.status = AssignmentStatus.DECLINED
    assignment.responded_at = timezone.now()
    assignment.save(update_fields=["status", "responded_at"])
    if pending is not None:
        pending.clear_pending()
    ActivityLog.objects.create(
        user=assignment.contractor.user,
        kind=ActivityKind.CONTRACTOR_REPLY,
        title="Исполнитель отказался",
        detail=str(assignment.campaign),
        meta={"assignment_id": assignment.id},
    )
    return "Отказ зафиксирован. Администратор назначит другого исполнителя."


def reject_counter_offer(
    assignment: CampaignAssignment,
    *,
    send_fn=None,
) -> str:
    """Admin rejects proposed time — assignment freed for another contractor."""
    assignment.status = AssignmentStatus.REJECTED_TIME
    assignment.responded_at = timezone.now()
    assignment.counter_deadline = None
    assignment.save(update_fields=["status", "responded_at", "counter_deadline"])
    try:
        from panel.admin_tasks import close_task_for_source

        close_task_for_source(
            AdminTaskKind.CONTRACTOR_COUNTER,
            "CampaignAssignment",
            assignment.id,
        )
    except Exception:
        logger.exception("Failed to close counter task on reject")
    msg = (
        "Администратор не согласился с предложенным временем. "
        "Задачу передадут другому исполнителю с такой же техникой."
    )
    if send_fn:
        try:
            send_fn(assignment.contractor.user, msg)
        except Exception:
            logger.exception("Failed to notify contractor about rejected time")
    return msg


def approve_counter_offer(
    assignment: CampaignAssignment,
    *,
    send_fn=None,
) -> str:
    if not assignment.proposed_at:
        raise ValueError("Нет предложенного времени")
    when = assignment.proposed_at
    reply = accept_assignment(
        assignment,
        scheduled_at=when,
        send_fn=send_fn,
        notify_residents=True,
    )
    if send_fn:
        try:
            send_fn(
                assignment.contractor.user,
                f"Администратор подтвердил ваше время: {_fmt_dt(when)}.",
            )
        except Exception:
            logger.exception("Failed to notify contractor about approved time")
    return reply


def residents_status_message(campaign: ServiceCampaign) -> str | None:
    accepted = list(
        campaign.assignments.filter(status=AssignmentStatus.ACCEPTED)
        .select_related("contractor", "contractor__user")
        .order_by("sort_order", "accepted_at", "id")
    )
    if not accepted:
        return None
    lines = [
        f"Статус по задаче «{campaign.title}»:",
        "Исполнители приедут в назначенное время:",
    ]
    for i, a in enumerate(accepted, start=1):
        name = str(a.contractor.user)
        eq = a.get_equipment_type_display()
        lines.append(f"{i}. {name} ({eq}) — {_fmt_dt(a.scheduled_at)}")
    return "\n".join(lines)


def _default_send_fn():
    """Build Max send_fn from runtime settings when panel/bot didn't pass one."""
    try:
        from ai.factory import get_runtime_settings
        from bot.client import MaxClient

        token = (get_runtime_settings().max_bot_token or "").strip()
        if not token:
            return None
        client = MaxClient(token)

        def send(user, text, _client=client):
            if user.chat_id:
                try:
                    _client.send_message(text, chat_id=user.chat_id)
                    return
                except Exception:
                    logger.exception("chat_id send failed for %s", user.max_user_id)
            _client.send_message(text, user_id=user.max_user_id)

        return send
    except Exception:
        logger.exception("Could not build default MAX send_fn")
        return None


def notify_residents_about_assignments(
    campaign: ServiceCampaign,
    *,
    send_fn=None,
) -> int:
    """Notify group members about accepted contractors (in order)."""
    text = residents_status_message(campaign)
    if not text or not campaign.group_id:
        return 0
    members = list(campaign.group.members.all())
    if not members:
        return 0
    send = send_fn or _default_send_fn()
    sent = 0
    now = timezone.now()
    for user in members:
        if send:
            try:
                send(user, text)
                sent += 1
            except Exception:
                logger.exception("Failed to notify resident %s", user.id)
    campaign.assignments.filter(status=AssignmentStatus.ACCEPTED).update(
        residents_notified_at=now
    )
    return sent


def expire_stale_counter_offers(*, send_fn=None) -> int:
    """Mark counter-offers past deadline as expired."""
    now = timezone.now()
    qs = CampaignAssignment.objects.filter(
        status=AssignmentStatus.COUNTER_OFFER,
        counter_deadline__lt=now,
    ).select_related("contractor", "contractor__user")
    n = 0
    for assignment in qs:
        assignment.status = AssignmentStatus.EXPIRED
        assignment.save(update_fields=["status"])
        n += 1
        try:
            from panel.admin_tasks import close_task_for_source

            close_task_for_source(
                AdminTaskKind.CONTRACTOR_COUNTER,
                "CampaignAssignment",
                assignment.id,
            )
        except Exception:
            logger.exception("Failed to close expired counter task")
        if send_fn:
            try:
                send_fn(
                    assignment.contractor.user,
                    "Время на согласование другого слота истекло (20 минут). "
                    "Задачу могут передать другому исполнителю.",
                )
            except Exception:
                logger.exception("Failed to notify expired counter")
    return n


def cancel_assignment(assignment: CampaignAssignment, *, send_fn=None) -> None:
    assignment.status = AssignmentStatus.CANCELLED
    assignment.save(update_fields=["status"])
    if send_fn:
        try:
            send_fn(
                assignment.contractor.user,
                f"Назначение на «{assignment.campaign.title}» отменено администратором.",
            )
        except Exception:
            logger.exception("Failed to notify cancel")


def accepted_assignments_needing_payout(campaign: ServiceCampaign):
    """Accepted assignments that still have no admin payout receipt."""
    paid_ids = set(
        campaign.contractor_payouts.values_list("assignment_id", flat=True)
    )
    return list(
        campaign.assignments.filter(status=AssignmentStatus.ACCEPTED)
        .exclude(id__in=paid_ids)
        .select_related("contractor", "contractor__user")
        .order_by("sort_order", "id")
    )


def campaign_requires_contractor_payouts(campaign: ServiceCampaign) -> bool:
    return bool(accepted_assignments_needing_payout(campaign))


def _payout_resident_message(campaign: ServiceCampaign, payouts: list[ContractorPayout]) -> str:
    lines = [
        f"По задаче «{campaign.title}» администратор перевёл оплату исполнителям:",
    ]
    for p in payouts:
        name = str(p.contractor.user)
        eq = p.contractor.get_equipment_type_display()
        bank = p.bank_name or "—"
        phone = p.payout_phone or "—"
        lines.append(
            f"• {name} ({eq}): {p.amount:.0f} ₽ → {phone}, банк {bank}"
        )
    lines.append("Чек(и) перевода во вложении / у администратора в карточке сбора.")
    return "\n".join(lines)


def _payout_contractor_message(payout: ContractorPayout) -> str:
    return (
        f"Деньги за работу по «{payout.campaign.title}» переведены.\n"
        f"Сумма: {payout.amount:.0f} ₽\n"
        f"Банк: {payout.bank_name or '—'}\n"
        f"На номер: {payout.payout_phone or payout.contractor.phone or '—'}\n\n"
        "Проверьте счёт. Если сумма пришла — всё в порядке. "
        "Если денег нет или сумма неверная — напишите в этот чат администратору."
    )


def record_contractor_payouts(
    campaign: ServiceCampaign,
    items: list[dict],
    *,
    send_fn=None,
    send_media_fn=None,
) -> list[ContractorPayout]:
    """
    Save admin payout receipts for accepted contractors and notify.

    items: [{assignment_id, amount: Decimal, file_bytes, filename, comment?}, ...]
    """
    if not items:
        raise ValueError("Нужно приложить оплату хотя бы одному исполнителю")

    created: list[ContractorPayout] = []
    for item in items:
        assignment = (
            CampaignAssignment.objects.select_related("contractor", "contractor__user")
            .filter(pk=item["assignment_id"], campaign=campaign)
            .first()
        )
        if assignment is None:
            raise ValueError(f"Назначение #{item['assignment_id']} не найдено")
        if assignment.status != AssignmentStatus.ACCEPTED:
            raise ValueError(
                f"Исполнитель «{assignment.contractor}» ещё не подтвердил заказ"
            )
        if campaign.contractor_payouts.filter(assignment=assignment).exists():
            raise ValueError(
                f"Оплата для «{assignment.contractor}» уже приложена"
            )
        amount = Decimal(item["amount"])
        if amount <= 0:
            raise ValueError("Сумма перевода должна быть больше нуля")
        raw = item.get("file_bytes") or b""
        filename = (item.get("filename") or "payout.jpg").strip() or "payout.jpg"
        if not raw:
            raise ValueError(
                f"Приложите чек перевода для «{assignment.contractor}»"
            )

        contractor = assignment.contractor
        payout = ContractorPayout(
            campaign=campaign,
            assignment=assignment,
            contractor=contractor,
            amount=amount,
            bank_name=(contractor.bank_name or "")[:255],
            payout_phone=(
                contractor.payout_phone or contractor.phone or ""
            )[:32],
            comment=(item.get("comment") or "")[:2000],
        )
        ext = Path(filename).suffix or ".jpg"
        safe_name = f"payout_{campaign.id}_{assignment.id}{ext}"
        payout.receipt_image.save(safe_name, ContentFile(raw), save=False)
        payout.save()
        created.append(payout)
        ActivityLog.objects.create(
            user=contractor.user,
            kind=ActivityKind.CONTRACTOR_PAYOUT,
            title="Оплата исполнителю",
            detail=f"{amount:.0f} ₽ · {campaign.title}",
            meta={
                "campaign_id": campaign.id,
                "assignment_id": assignment.id,
                "payout_id": payout.id,
            },
        )

    notify_contractor_payouts(
        campaign, created, send_fn=send_fn, send_media_fn=send_media_fn
    )
    return created


def notify_contractor_payouts(
    campaign: ServiceCampaign,
    payouts: list[ContractorPayout],
    *,
    send_fn=None,
    send_media_fn=None,
) -> tuple[int, int]:
    """Notify paid residents + each contractor. Returns (residents, contractors)."""
    if not payouts:
        return 0, 0
    send = send_fn or _default_send_fn()
    media = send_media_fn

    image_payloads: list[tuple[bytes, str]] = []
    for p in payouts:
        try:
            with p.receipt_image.open("rb") as fh:
                image_payloads.append(
                    (fh.read(), Path(p.receipt_image.name).name)
                )
        except Exception:
            logger.exception("Could not read payout receipt %s", p.id)

    resident_text = _payout_resident_message(campaign, payouts)
    # Who collected money: paid invites, else all group members
    residents = list(
        BotUser.objects.filter(
            id__in=campaign.invites.filter(status=InviteStatus.PAID).values_list(
                "user_id", flat=True
            )
        )
    )
    if not residents and campaign.group_id:
        residents = list(campaign.group.members.all())

    r_sent = 0
    now = timezone.now()
    for user in residents:
        if not send and not media:
            break
        try:
            if image_payloads and media:
                media(user, resident_text, image_payloads)
            elif send:
                send(user, resident_text)
            r_sent += 1
        except Exception:
            logger.exception("Failed payout notice to resident %s", user.id)

    c_sent = 0
    for p in payouts:
        msg = _payout_contractor_message(p)
        payloads = []
        try:
            with p.receipt_image.open("rb") as fh:
                payloads = [(fh.read(), Path(p.receipt_image.name).name)]
        except Exception:
            logger.exception("Could not re-read payout %s for contractor", p.id)
        try:
            if payloads and media:
                media(p.contractor.user, msg, payloads)
            elif send:
                send(p.contractor.user, msg)
            c_sent += 1
            p.contractor_notified_at = now
            p.save(update_fields=["contractor_notified_at"])
        except Exception:
            logger.exception(
                "Failed payout notice to contractor %s", p.contractor_id
            )

    ContractorPayout.objects.filter(id__in=[p.id for p in payouts]).update(
        residents_notified_at=now
    )
    return r_sent, c_sent


def close_campaign_requiring_payouts(
    campaign: ServiceCampaign,
    items: list[dict],
    *,
    send_fn=None,
    send_media_fn=None,
):
    """
    Close work (WORK_CLOSED) only after payout receipts for all accepted contractors.
    If there are no accepted contractors — close without payouts.
    """
    from services.service import advance_work_stage
    from database.models import WorkStage

    current = campaign.work_stage or WorkStage.COLLECTING
    if current != WorkStage.WORK_DONE:
        raise ValueError("Закрытие доступно только после этапа «Работа выполнена»")

    needing = accepted_assignments_needing_payout(campaign)
    if needing:
        needed_ids = {a.id for a in needing}
        provided = {int(i["assignment_id"]) for i in items}
        missing = needed_ids - provided
        if missing:
            names = [
                str(a.contractor)
                for a in needing
                if a.id in missing
            ]
            raise ValueError(
                "Перед закрытием приложите чек перевода каждому исполнителю: "
                + ", ".join(names)
            )
        record_contractor_payouts(
            campaign,
            items,
            send_fn=send_fn,
            send_media_fn=send_media_fn,
        )
    return advance_work_stage(
        campaign,
        send_fn=send_fn,
        send_media_fn=send_media_fn,
        allow_close_without_payout=True,
    )
