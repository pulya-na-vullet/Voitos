"""Обратная связь из приложения: баг / ОС / отзыв о менеджере."""

from __future__ import annotations

from django.utils import timezone

from database.models import (
    ActivityKind,
    ActivityLog,
    AdminTaskKind,
    BotUser,
    FeedbackKind,
    FeedbackStatus,
    FeedbackTicket,
    ServiceGroup,
)
from panel.admin_tasks import close_task_for_source, upsert_task


def groups_for_user(user: BotUser) -> list[ServiceGroup]:
    return list(
        ServiceGroup.objects.filter(members=user)
        .select_related("manager")
        .order_by("name")
    )


def primary_manager_context(user: BotUser) -> tuple[ServiceGroup | None, object | None]:
    groups = groups_for_user(user)
    for g in groups:
        if g.manager_id:
            return g, g.manager
    return (groups[0] if groups else None), None


def manager_context_dict(user: BotUser) -> dict:
    group, manager = primary_manager_context(user)
    if manager is None:
        return {
            "available": False,
            "manager_name": "",
            "group_name": group.name if group else "",
            "group_id": group.id if group else None,
        }
    name = (
        (getattr(manager, "get_full_name", lambda: "")() or "").strip()
        or getattr(manager, "username", "")
        or f"id{manager.id}"
    )
    return {
        "available": True,
        "manager_name": name,
        "group_name": group.name if group else "",
        "group_id": group.id if group else None,
    }


def list_user_feedback(user: BotUser) -> list[FeedbackTicket]:
    return list(
        FeedbackTicket.objects.filter(user=user)
        .select_related("manager", "group", "admin_replied_by")
        .order_by("-created_at")[:100]
    )


def ticket_to_dict(ticket: FeedbackTicket) -> dict:
    manager_name = ""
    if ticket.manager_id:
        mgr = ticket.manager
        manager_name = (
            (getattr(mgr, "get_full_name", lambda: "")() or "").strip()
            or getattr(mgr, "username", "")
            or f"id{ticket.manager_id}"
        )
    return {
        "id": ticket.id,
        "kind": ticket.kind,
        "kind_label": ticket.get_kind_display(),
        "status": ticket.status,
        "status_label": ticket.get_status_display(),
        "subject": ticket.subject or "",
        "body": ticket.body or "",
        "score": ticket.score,
        "manager_name": manager_name,
        "group_name": ticket.group.name if ticket.group_id else "",
        "admin_reply": ticket.admin_reply or "",
        "admin_replied_at": ticket.admin_replied_at.isoformat() if ticket.admin_replied_at else None,
        "created_at": ticket.created_at.isoformat() if ticket.created_at else "",
        "updated_at": ticket.updated_at.isoformat() if ticket.updated_at else "",
    }


def create_feedback_ticket(
    user: BotUser,
    *,
    kind: str,
    body: str,
    subject: str = "",
    score: int | None = None,
) -> FeedbackTicket:
    body = (body or "").strip()
    subject = (subject or "").strip()[:200]
    if len(body) < 10:
        raise ValueError("Опишите обращение подробнее (минимум 10 символов).")
    if kind not in FeedbackKind.values:
        raise ValueError("Некорректный тип обращения.")

    score_int: int | None = None
    if score is not None and str(score).strip() != "":
        try:
            score_int = int(score)
        except (TypeError, ValueError):
            raise ValueError("Оценка менеджера должна быть от 1 до 5.") from None

    group = None
    manager = None
    if kind == FeedbackKind.MANAGER:
        group, manager = primary_manager_context(user)
        if manager is None:
            raise ValueError(
                "За вами пока не закреплён менеджер района — "
                "отправьте обычную обратную связь или баг."
            )
        if score_int is None or score_int not in range(1, 6):
            raise ValueError("Оценка менеджера должна быть от 1 до 5.")
        if not subject:
            subject = f"ОС по менеджеру · {group.name if group else 'район'}"

    if kind == FeedbackKind.BUG and not subject:
        subject = "Баг в приложении"
    if kind == FeedbackKind.FEEDBACK and not subject:
        subject = "Обратная связь"

    ticket = FeedbackTicket.objects.create(
        user=user,
        kind=kind,
        subject=subject,
        body=body,
        score=score_int if kind == FeedbackKind.MANAGER else None,
        manager=manager,
        group=group,
        status=FeedbackStatus.OPEN,
    )
    ActivityLog.objects.create(
        user=user,
        kind=ActivityKind.FEEDBACK,
        title=f"Обращение: {ticket.get_kind_display()}",
        detail=body[:400],
        meta={"ticket_id": ticket.id, "kind": kind},
    )
    upsert_task(
        kind=AdminTaskKind.FEEDBACK,
        title=f"ОС #{ticket.id}: {ticket.get_kind_display()}",
        description=(
            f"{user.real_name or user}\n"
            f"{subject}\n"
            f"{body[:280]}"
            + (f"\nОценка менеджеру: {score_int}/5" if score_int else "")
        ),
        user=user,
        action_url=f"/panel/feedback/{ticket.id}/",
        source_model="FeedbackTicket",
        source_id=ticket.id,
        priority=18 if kind == FeedbackKind.BUG else 25,
        meta={"ticket_id": ticket.id, "kind": kind},
    )
    return ticket


def answer_feedback_ticket(
    ticket: FeedbackTicket,
    *,
    reply: str,
    admin_user,
) -> FeedbackTicket:
    reply = (reply or "").strip()
    if len(reply) < 2:
        raise ValueError("Напишите текст ответа пользователю.")
    ticket.admin_reply = reply
    ticket.admin_replied_at = timezone.now()
    ticket.admin_replied_by = admin_user
    ticket.status = FeedbackStatus.ANSWERED
    ticket.save(
        update_fields=[
            "admin_reply",
            "admin_replied_at",
            "admin_replied_by",
            "status",
            "updated_at",
        ]
    )
    close_task_for_source(AdminTaskKind.FEEDBACK, "FeedbackTicket", ticket.id)
    ActivityLog.objects.create(
        user=ticket.user,
        kind=ActivityKind.FEEDBACK,
        title=f"Ответ на обращение #{ticket.id}",
        detail=reply[:400],
        meta={"ticket_id": ticket.id},
    )
    try:
        from api.emit import emit_app_event

        emit_app_event(
            ticket.user,
            ntype="feedback.answered",
            title="Ответ по вашему обращению",
            body=reply[:180],
            entity_type="FeedbackTicket",
            entity_id=ticket.id,
            deep_link=f"voitos://app/feedback/{ticket.id}",
        )
    except Exception:
        pass
    return ticket
