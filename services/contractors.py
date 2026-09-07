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




def annotate_contractor_total_earned(qs):
    """
    Счётчик «Заработано»: выплаты по сервисным кампаниям
    + чистый заработок по заявкам (сумма клиента − комиссия 10%).
    """
    from django.db.models import (
        DecimalField,
        ExpressionWrapper,
        F,
        OuterRef,
        Subquery,
        Sum,
        Value,
    )
    from django.db.models.functions import Coalesce

    from database.models import WorkRequest

    money = DecimalField(max_digits=14, decimal_places=2)
    zero = Value(Decimal("0.00"), output_field=money)

    payout_sq = (
        ContractorPayout.objects.filter(contractor_id=OuterRef("pk"))
        .values("contractor_id")
        .annotate(total=Sum("amount"))
        .values("total")[:1]
    )
    work_sq = (
        WorkRequest.objects.filter(
            assigned_contractor_id=OuterRef("pk"),
            executor_earned_amount__isnull=False,
        )
        .exclude(status="cancelled")
        .values("assigned_contractor_id")
        .annotate(total=Sum("executor_earned_amount"))
        .values("total")[:1]
    )
    return qs.annotate(
        payout_earned=Coalesce(Subquery(payout_sq, output_field=money), zero),
        work_earned=Coalesce(Subquery(work_sq, output_field=money), zero),
    ).annotate(
        total_earned=ExpressionWrapper(
            F("payout_earned") + F("work_earned"),
            output_field=money,
        )
    )


def contractor_total_earned(contractor: ContractorProfile) -> Decimal:
    row = annotate_contractor_total_earned(
        ContractorProfile.objects.filter(pk=contractor.pk)
    ).first()
    if not row:
        return Decimal("0.00")
    return Decimal(row.total_earned or 0).quantize(Decimal("0.01"))


def verified_contractors(*, equipment_type: str | None = None):
    qs = ContractorProfile.objects.filter(
        status=ContractorStatus.VERIFIED
    ).select_related("user")
    if equipment_type:
        qs = qs.filter(equipment_type=equipment_type)
    return qs.order_by(
        "-is_voitos_team", "equipment_type", "user__real_name", "id"
    )


def suggested_equipment_for_campaign(campaign: ServiceCampaign) -> list[str]:
    """Типы техники / коды ролей, которые нужны для кампании."""
    from services.executor_roles import suggested_role_codes_for_campaign

    return suggested_role_codes_for_campaign(campaign)


def _fmt_dt(dt) -> str:
    if not dt:
        return "—"
    return timezone.localtime(dt).strftime("%d.%m.%Y %H:%M")


def offer_message(assignment: CampaignAssignment) -> str:
    from services.dispatch_priority import campaign_work_address, maps_route_urls

    c = assignment.campaign
    eq = assignment.get_equipment_type_display()
    when = _fmt_dt(assignment.scheduled_at or c.event_at)
    address = campaign_work_address(c)
    maps = maps_route_urls(address)
    lines = [
        f"Вам предложен заказ как исполнителю ({eq}).",
        f"Задача: {c.title}",
        f"Категория: {c.get_category_display()}",
        f"Время: {when}",
    ]
    if address:
        lines.append(f"Адрес / объект: {address}")
    if maps.get("yandex_maps_url"):
        lines.append(f"Маршрут Яндекс.Карты: {maps['yandex_maps_url']}")
    if maps.get("dgis_maps_url"):
        lines.append(f"Маршрут 2ГИС: {maps['dgis_maps_url']}")
    desc = (c.description or "").strip()
    if desc:
        lines.append(desc)
    lines.extend(
        [
            "",
            "Ответьте:",
            "1 / да — согласен на это время",
            "2 / нет — отказаться",
            "3 / другое время — предложить своё время "
            f"(у вас будет {COUNTER_OFFER_MINUTES} минут; "
            "администратор подтвердит или отклонит)",
        ]
    )
    return "\n".join(lines)


def auto_offer_priority_contractors_for_campaign(
    campaign: ServiceCampaign,
    *,
    send_fn=None,
) -> int:
    """
    После сбора денег: предложить работу приоритетным (Voitos) исполнителям
    по рекомендуемым ролям. Если у приоритетных нет свободного слота — низкий приоритет.
    """
    from services.dispatch_priority import contractor_has_capacity_today
    from services.resident_helpers import uses_resident_helpers
    from services.work_request_dispatch import localities_match

    if uses_resident_helpers(campaign):
        return 0
    codes = suggested_equipment_for_campaign(campaign)
    if not codes:
        return 0

    when = campaign.event_at or timezone.now()
    camp_loc = (campaign.locality or "").strip()
    if campaign.group_id and not camp_loc:
        # НП часто на жителях группы — возьмём у первого с НП
        member = (
            campaign.group.members.exclude(locality="")
            .order_by("id")
            .only("locality")
            .first()
        )
        if member:
            camp_loc = (member.locality or "").strip()

    offered = 0
    active_statuses = {
        AssignmentStatus.OFFERED,
        AssignmentStatus.COUNTER_OFFER,
        AssignmentStatus.ACCEPTED,
    }
    for code in codes:
        if campaign.assignments.filter(
            equipment_type=code, status__in=active_statuses
        ).exists():
            continue
        candidates = list(verified_contractors(equipment_type=code))
        if not candidates:
            continue

        def _score(c: ContractorProfile) -> tuple:
            cloc = (c.locality or getattr(c.user, "locality", "") or "").strip()
            loc_ok = 1.0 if (camp_loc and cloc and localities_match(camp_loc, cloc)) else (
                0.4 if not camp_loc else 0.1
            )
            team = 1 if c.is_voitos_team else 0
            free = 1 if contractor_has_capacity_today(c) else 0
            return (team * free, free, loc_ok, team, -c.id)

        # Сначала команда Voitos со свободным слотом; иначе — остальные со слотом.
        team_free = [c for c in candidates if c.is_voitos_team and contractor_has_capacity_today(c)]
        pool = team_free or [
            c for c in candidates if not c.is_voitos_team and contractor_has_capacity_today(c)
        ]
        if not pool:
            # Ни у кого нет «слота» — всё равно предложим лучшему по НП/приоритету.
            pool = candidates
        pool.sort(key=_score, reverse=True)
        pick = pool[0]
        try:
            assign_contractor(campaign, pick, scheduled_at=when, send_fn=send_fn)
            offered += 1
        except ValueError:
            continue
        except Exception:
            logger.exception(
                "auto_offer failed campaign=%s code=%s contractor=%s",
                campaign.id,
                code,
                pick.id,
            )
    return offered


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


def work_request_executor_contact_lines(contractor: ContractorProfile) -> list[str]:
    """Контакты исполнителя для клиента по заявке (телефон + MAX)."""
    user = contractor.user
    phone = (contractor.phone or user.phone or "").strip()
    uname = (user.username or "").strip().lstrip("@")
    link = max_profile_link(user)
    lines: list[str] = []
    if phone:
        lines.append(f"Телефон: {phone}")
    if uname:
        lines.append(f"MAX: @{uname}")
    if link:
        lines.append(f"Профиль MAX: {link}")
    if not lines:
        lines.append("Контакты в анкете не указаны — уточните у администратора.")
    return lines


def format_executor_contacts_block(contractor: ContractorProfile) -> str:
    return "\n".join(work_request_executor_contact_lines(contractor))


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
    """Если на задаче ≥2 исполнителей — поставить в очередь контакты друг друга."""
    from services.outbox import deliver

    active = active_peer_assignments(campaign)
    if len(active) < 2:
        return 0
    sent = 0
    for assignment in active:
        peers = [a for a in active if a.id != assignment.id]
        if not peers:
            continue
        text = peers_contacts_message(campaign, peers)
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
        if deliver(
            assignment.contractor.user,
            text,
            kind="campaign.peer_contacts",
            meta={"campaign_id": campaign.id, "assignment_id": assignment.id},
            send_fn=send_fn,
        ):
            sent += 1
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
    from services.outbox import KIND_CONTRACTOR_OFFER, deliver

    deliver(
        contractor.user,
        text,
        kind=KIND_CONTRACTOR_OFFER,
        meta={"assignment_id": assignment.id, "campaign_id": campaign.id},
        send_fn=send_fn,
    )

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
    from services.outbox import KIND_RESIDENTS_ASSIGN, deliver

    members = list(campaign.group.members.all())
    if not members:
        return 0
    sent = 0
    now = timezone.now()
    for user in members:
        if deliver(
            user,
            text,
            kind=KIND_RESIDENTS_ASSIGN,
            meta={"campaign_id": campaign.id},
            send_fn=send_fn,
        ):
            sent += 1
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
    """Notify paid residents + each contractor via outbox. Returns (residents, contractors)."""
    from services.outbox import KIND_CAMPAIGN_PAYOUT, deliver

    if not payouts:
        return 0, 0

    resident_text = _payout_resident_message(campaign, payouts)
    payout_ids = [p.id for p in payouts]
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
        if deliver(
            user,
            resident_text,
            kind=KIND_CAMPAIGN_PAYOUT,
            meta={"campaign_id": campaign.id, "payout_ids": payout_ids},
            send_fn=send_fn,
            send_media_fn=send_media_fn,
        ):
            r_sent += 1

    c_sent = 0
    for p in payouts:
        msg = _payout_contractor_message(p)
        if deliver(
            p.contractor.user,
            msg,
            kind=KIND_CAMPAIGN_PAYOUT,
            meta={"campaign_id": campaign.id, "payout_id": p.id},
            send_fn=send_fn,
            send_media_fn=send_media_fn,
        ):
            c_sent += 1
            p.contractor_notified_at = now
            p.save(update_fields=["contractor_notified_at"])
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
