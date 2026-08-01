"""Исполнители (трактор / камаз): назначения на сервисные задачи."""
from __future__ import annotations

import logging
from datetime import timedelta

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
    ContractorProfile,
    ContractorStatus,
    EquipmentType,
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
    return [EquipmentType.TRACTOR, EquipmentType.TRUCK]


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
