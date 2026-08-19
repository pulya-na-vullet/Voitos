"""Обратная связь из приложения: баг / ОС / отзыв о менеджере."""

from __future__ import annotations

import logging

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

logger = logging.getLogger(__name__)


def _manager_display_name(manager) -> str:
    if manager is None:
        return ""
    return (
        (getattr(manager, "get_full_name", lambda: "")() or "").strip()
        or getattr(manager, "username", "")
        or f"id{getattr(manager, 'id', '')}"
    )


def groups_for_user(user: BotUser) -> list[ServiceGroup]:
    return list(
        ServiceGroup.objects.filter(members=user)
        .select_related("manager")
        .order_by("name")
    )


def managers_for_user(user: BotUser) -> list[dict]:
    """Группы пользователя, у которых есть менеджер (для выбора в приложении)."""
    items: list[dict] = []
    seen_pairs: set[tuple[int, int]] = set()
    for g in groups_for_user(user):
        if not g.manager_id:
            continue
        key = (g.id, g.manager_id)
        if key in seen_pairs:
            continue
        seen_pairs.add(key)
        items.append(
            {
                "available": True,
                "group_id": g.id,
                "group_name": g.name or "",
                "manager_id": g.manager_id,
                "manager_name": _manager_display_name(g.manager),
            }
        )
    return items


def primary_manager_context(user: BotUser) -> tuple[ServiceGroup | None, object | None]:
    groups = groups_for_user(user)
    for g in groups:
        if g.manager_id:
            return g, g.manager
    return (groups[0] if groups else None), None


def manager_context_dict(user: BotUser) -> dict:
    """Обратная совместимость: первый менеджер + полный список."""
    managers = managers_for_user(user)
    if not managers:
        group, _ = primary_manager_context(user)
        return {
            "available": False,
            "manager_name": "",
            "group_name": group.name if group else "",
            "group_id": group.id if group else None,
            "manager_id": None,
            "managers": [],
        }
    primary = managers[0]
    return {
        "available": True,
        "manager_name": primary["manager_name"],
        "group_name": primary["group_name"],
        "group_id": primary["group_id"],
        "manager_id": primary["manager_id"],
        "managers": managers,
    }


def resolve_manager_group(
    user: BotUser, *, group_id: int | None = None
) -> tuple[ServiceGroup, object]:
    """Найти группу пользователя с менеджером; при group_id — конкретную."""
    groups = [g for g in groups_for_user(user) if g.manager_id]
    if not groups:
        raise ValueError(
            "За вами пока не закреплён менеджер района — "
            "отправьте обычную обратную связь или баг."
        )
    if group_id is not None:
        for g in groups:
            if g.id == int(group_id):
                return g, g.manager
        raise ValueError("Выберите группу, в которой вы состоите.")
    return groups[0], groups[0].manager


def list_user_feedback(user: BotUser) -> list[FeedbackTicket]:
    return list(
        FeedbackTicket.objects.filter(user=user)
        .select_related("manager", "group", "admin_replied_by")
        .order_by("-created_at")[:100]
    )


def ticket_to_dict(ticket: FeedbackTicket) -> dict:
    return {
        "id": ticket.id,
        "kind": ticket.kind,
        "kind_label": ticket.get_kind_display(),
        "status": ticket.status,
        "status_label": ticket.get_status_display(),
        "subject": ticket.subject or "",
        "body": ticket.body or "",
        "score": ticket.score,
        "manager_name": _manager_display_name(ticket.manager) if ticket.manager_id else "",
        "group_name": ticket.group.name if ticket.group_id else "",
        "group_id": ticket.group_id,
        "manager_id": ticket.manager_id,
        "admin_reply": ticket.admin_reply or "",
        "admin_replied_at": ticket.admin_replied_at.isoformat() if ticket.admin_replied_at else None,
        "created_at": ticket.created_at.isoformat() if ticket.created_at else "",
        "updated_at": ticket.updated_at.isoformat() if ticket.updated_at else "",
        "answered_by_ai": bool(
            ticket.admin_reply
            and ticket.admin_replied_by_id is None
            and ticket.status == FeedbackStatus.ANSWERED
        ),
    }


def create_feedback_ticket(
    user: BotUser,
    *,
    kind: str,
    body: str,
    subject: str = "",
    score: int | None = None,
    group_id: int | None = None,
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
        gid = None
        if group_id is not None and str(group_id).strip() != "":
            try:
                gid = int(group_id)
            except (TypeError, ValueError):
                raise ValueError("Некорректная группа.") from None
        group, manager = resolve_manager_group(user, group_id=gid)
        if score_int is None or score_int not in range(1, 6):
            raise ValueError("Оценка менеджера должна быть от 1 до 5.")
        if not subject:
            subject = (
                f"ОС по менеджеру · {_manager_display_name(manager)} · {group.name}"
            )

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
        meta={
            "ticket_id": ticket.id,
            "kind": kind,
            "manager_id": manager.id if manager else None,
            "group_id": group.id if group else None,
            "score": score_int,
        },
    )

    if kind == FeedbackKind.MANAGER:
        # ОС по менеджеру отвечает Яндекс ИИ; админ-таск не создаём.
        try:
            ai_answer_manager_feedback(ticket)
        except Exception:
            logger.exception("AI answer failed for manager feedback #%s", ticket.id)
            upsert_task(
                kind=AdminTaskKind.FEEDBACK,
                title=f"ОС #{ticket.id}: менеджер (ИИ не ответил)",
                description=(
                    f"{user.real_name or user}\n"
                    f"{subject}\n"
                    f"{body[:280]}\n"
                    f"Оценка: {score_int}/5 · {_manager_display_name(manager)}"
                ),
                user=user,
                action_url=f"/panel/feedback/{ticket.id}/",
                source_model="FeedbackTicket",
                source_id=ticket.id,
                priority=22,
                meta={"ticket_id": ticket.id, "kind": kind},
            )
    else:
        upsert_task(
            kind=AdminTaskKind.FEEDBACK,
            title=f"ОС #{ticket.id}: {ticket.get_kind_display()}",
            description=(
                f"{user.real_name or user}\n"
                f"{subject}\n"
                f"{body[:280]}"
            ),
            user=user,
            action_url=f"/panel/feedback/{ticket.id}/",
            source_model="FeedbackTicket",
            source_id=ticket.id,
            priority=18 if kind == FeedbackKind.BUG else 25,
            meta={"ticket_id": ticket.id, "kind": kind},
        )
    ticket.refresh_from_db()
    return ticket


def ai_answer_manager_feedback(ticket: FeedbackTicket) -> FeedbackTicket:
    """Сформировать ответ жителю через YandexGPT по ОС о менеджере."""
    if ticket.kind != FeedbackKind.MANAGER:
        raise ValueError("ИИ отвечает только на ОС по менеджеру.")
    if ticket.status == FeedbackStatus.ANSWERED and ticket.admin_reply:
        return ticket

    manager_name = _manager_display_name(ticket.manager) or "менеджер"
    group_name = ticket.group.name if ticket.group_id else "район"
    score = ticket.score or "—"
    system = (
        "Ты — служба заботы Voitos. Житель оставил обратную связь о менеджере "
        "соседской группы. Ответь коротко и по-человечески на русском (4–7 предложений).\n"
        "Поблагодари за оценку, покажи что мнение важно, при низкой оценке (1–3) "
        "признай проблему и скажи что передадим менеджеру/руководству для улучшения, "
        "при высокой (4–5) — поблагодари и отметь сильные стороны из комментария.\n"
        "Не выдумывай факты, которых нет в отзыве. Без markdown и без подписи «ИИ»."
    )
    user_prompt = (
        f"Менеджер: {manager_name}\n"
        f"Группа / район: {group_name}\n"
        f"Оценка жителя: {score}/5\n"
        f"Комментарий:\n{(ticket.body or '').strip()[:1200]}"
    )

    from ai.factory import get_llm_provider

    llm = get_llm_provider()
    reply = llm.complete_text(system, user_prompt, temperature=0.35, max_tokens=500)
    reply = (reply or "").strip()
    if len(reply) < 2:
        raise ValueError("ИИ вернул пустой ответ.")

    # Лог в ManagerSurveyAILog если возможно (для экрана менеджера)
    try:
        from database.models import ManagerSurveyAILog

        if ticket.manager_id:
            ManagerSurveyAILog.objects.create(
                manager_id=ticket.manager_id,
                period=None,
                role="assistant",
                content=(
                    f"[ОС из приложения #{ticket.id}] оценка {score}/5\n"
                    f"Житель: {ticket.user}\n"
                    f"Комментарий: {(ticket.body or '')[:500]}\n\n"
                    f"Ответ ИИ:\n{reply[:1500]}"
                ),
            )
    except Exception:
        logger.exception("Failed to log manager AI reply for ticket #%s", ticket.id)

    return answer_feedback_ticket(ticket, reply=reply, admin_user=None)


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
        meta={
            "ticket_id": ticket.id,
            "by_ai": admin_user is None,
            "admin_id": getattr(admin_user, "id", None),
        },
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
