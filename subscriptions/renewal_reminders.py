"""Напоминания о продлении подписки (за 4 дня / 2 дня / 2 часа до конца).

Создаются при принятии чека администратором. Плательщику — продлить оплату;
членам семьи — напомнить плательщику о скором окончании подписки.
Доставляются обычным reminder-scheduler'ом.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from django.utils import timezone

from database.models import ActivityKind, ActivityLog, BotUser, Reminder

logger = logging.getLogger(__name__)

# Маркер системных напоминаний (не показывается пользователю).
RENEWAL_MARKER = "⟦подписка⟧"

RENEWAL_OFFSETS: tuple[tuple[timedelta, str], ...] = (
    (timedelta(days=4), "за 4 дня"),
    (timedelta(days=2), "за 2 дня"),
    (timedelta(hours=2), "за 2 часа"),
)


def strip_renewal_marker(text: str) -> str | None:
    """Если это системное напоминание о подписке — вернуть текст без маркера."""
    if text.startswith(RENEWAL_MARKER):
        return text[len(RENEWAL_MARKER) :]
    return None


def renewal_ntype_from_text(text: str) -> str | None:
    """Определить тип mobile-события по тексту системного напоминания."""
    if not text.startswith(RENEWAL_MARKER) and "Подписка заканчивается" not in text:
        if RENEWAL_MARKER not in text and "заканчивается" not in text:
            return None
    body = text
    if "за 2 часа" in body:
        return "subscription.renewal_2h"
    if "за 2 дня" in body:
        return "subscription.renewal_2d"
    if "за 4 дня" in body:
        return "subscription.renewal_4d"
    if text.startswith(RENEWAL_MARKER):
        return "subscription.renewal_4d"
    return None


def emit_renewal_app_event(user: BotUser, reminder_text: str) -> None:
    """Inbox + FCM при наступлении системного напоминания о продлении."""
    ntype = renewal_ntype_from_text(reminder_text)
    if not ntype:
        return
    body = strip_renewal_marker(reminder_text) or reminder_text
    try:
        from api.emit import emit_app_event

        emit_app_event(
            user,
            ntype=ntype,
            title="Продление подписки",
            body=body[:500],
            entity_type="subscription",
        )
    except Exception:
        logger.exception("emit renewal app event failed user=%s", getattr(user, "id", None))


def _until_label(until) -> str:
    return timezone.localtime(until).strftime("%d.%m.%Y %H:%M")


def payer_renewal_text(until, offset_label: str) -> str:
    from subscriptions.service import payment_help_text

    return (
        f"{RENEWAL_MARKER}"
        f"Подписка заканчивается {_until_label(until)} ({offset_label}).\n"
        f"Продлите доступ, чтобы сохранить ассистента и функции сервиса.\n\n"
        f"{payment_help_text()}"
    )


def family_renewal_text(payer: BotUser, until, offset_label: str) -> str:
    payer_name = str(payer).strip() or "члену семьи"
    return (
        f"{RENEWAL_MARKER}"
        f"Напомните {payer_name}, что подписка Voitos "
        f"заканчивается {_until_label(until)} ({offset_label}).\n"
        f"Попросите продлить оплату, чтобы у семьи сохранился доступ."
    )


def cancel_subscription_renewal_reminders(*users: BotUser) -> int:
    """Удалить незавершённые системные напоминания о подписке у пользователей."""
    ids = [int(u.id) for u in users if u is not None]
    if not ids:
        return 0
    qs = Reminder.objects.filter(
        user_id__in=ids,
        is_done=False,
        text__startswith=RENEWAL_MARKER,
    )
    deleted, _ = qs.delete()
    return int(deleted)


def schedule_subscription_renewal_reminders(user: BotUser) -> int:
    """
    Пересоздать напоминания о продлении для плательщика и его семьи.

    `user` — тот, чья подписка оплачена (чек принят). Если `subscription_until`
    пуст — только снять старые напоминания.
    """
    user.refresh_from_db()
    dependents = list(user.family_dependents.all())
    cancel_subscription_renewal_reminders(user, *dependents)

    until = user.subscription_until
    if not until:
        return 0

    now = timezone.now()
    if until <= now:
        return 0

    created = 0
    reminders: list[Reminder] = []
    for delta, label in RENEWAL_OFFSETS:
        due_at = until - delta
        if due_at <= now:
            continue
        reminders.append(
            Reminder(
                user=user,
                text=payer_renewal_text(until, label),
                due_at=due_at,
            )
        )
        for dep in dependents:
            reminders.append(
                Reminder(
                    user=dep,
                    text=family_renewal_text(user, until, label),
                    due_at=due_at,
                )
            )

    if reminders:
        Reminder.objects.bulk_create(reminders)
        created = len(reminders)
        ActivityLog.objects.create(
            user=user,
            kind=ActivityKind.REMINDER_CREATE,
            title="Напоминания о продлении подписки",
            detail=(
                f"Создано {created} шт. до "
                f"{timezone.localtime(until).strftime('%d.%m.%Y %H:%M')} "
                f"(семья: {len(dependents)})"
            ),
            meta={
                "count": created,
                "dependents": [d.id for d in dependents],
                "subscription_until": until.isoformat(),
            },
        )
    return created
