"""Создание уведомлений в inbox + отправка FCM (api.push)."""

from __future__ import annotations

from typing import Any

from database.models import BotUser

from api.models import AppNotification

# type → шаблон deep_link ({id} подставляется)
DEEP_LINKS: dict[str, str] = {
    "collection.offered": "voitos://app/collections/{id}",
    "collection.remind_3d": "voitos://app/collections/{id}",
    "collection.remind_1d": "voitos://app/collections/{id}",
    "collection.remind_2h": "voitos://app/collections/{id}",
    "collection.progress": "voitos://app/collections/{id}",
    "collection.closed": "voitos://app/collections/{id}",
    "work_request.assigned": "voitos://app/work-requests/{id}",
    "work_request.no_executor": "voitos://app/work-requests/{id}",
    "work_request.slots_ready": "voitos://app/work-requests/{id}/slots",
    "work_request.confirm_amount": "voitos://app/work-requests/{id}/confirm",
    "work_request.rate": "voitos://app/work-requests/{id}/rate",
    "subscription.receipt_approved": "voitos://app/subscription",
    "subscription.receipt_rejected": "voitos://app/subscription",
    "subscription.renewal_4d": "voitos://app/subscription",
    "profile.verified": "voitos://app/home",
    "wish_ballot.started": "voitos://app/ballots/{id}",
    "manager_survey.started": "voitos://app/manager-survey/{id}",
    "reminder.due": "voitos://app/reminders/{id}",
    "work_request.offer": "voitos://app/executor/offers/{id}",
    "work_request.commission_ask": "voitos://app/executor/work-requests/{id}/commission",
}


def resolve_deep_link(
    ntype: str, *, entity_id: int | None = None, deep_link: str = ""
) -> str:
    if deep_link:
        return deep_link
    tmpl = DEEP_LINKS.get(ntype) or "voitos://app/home"
    if entity_id is not None and "{id}" in tmpl:
        return tmpl.format(id=entity_id)
    return tmpl.replace("/{id}", "").replace("{id}", "")


def notify_user(
    bot_user: BotUser,
    *,
    ntype: str,
    title: str,
    body: str = "",
    entity_type: str = "",
    entity_id: int | None = None,
    deep_link: str = "",
    payload: dict[str, Any] | None = None,
) -> AppNotification:
    """Записать событие в inbox и попытаться отправить FCM."""
    link = resolve_deep_link(ntype, entity_id=entity_id, deep_link=deep_link)
    note = AppNotification.objects.create(
        bot_user=bot_user,
        type=ntype,
        title=title[:255],
        body=body or "",
        deep_link=link,
        entity_type=entity_type or "",
        entity_id=entity_id,
        payload=payload or {},
    )
    try:
        from api.push import dispatch_push

        dispatch_push(note)
    except Exception:
        import logging

        logging.getLogger(__name__).exception(
            "dispatch_push failed notification=%s", note.id
        )
    return note
