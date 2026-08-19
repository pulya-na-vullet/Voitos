from __future__ import annotations

from datetime import datetime, timedelta

from django.utils import timezone

from database.models import ActivityKind, ActivityLog, BotUser, Reminder, ReminderRepeat


class ReminderService:
    def create(
        self,
        user: BotUser,
        text: str,
        due_at: datetime,
        *,
        repeat: str = ReminderRepeat.NONE,
    ) -> Reminder:
        if timezone.is_naive(due_at):
            due_at = timezone.make_aware(due_at, timezone.get_current_timezone())
        if repeat not in {ReminderRepeat.NONE, ReminderRepeat.DAILY, "none", "daily"}:
            repeat = ReminderRepeat.NONE
        reminder = Reminder.objects.create(
            user=user,
            text=text.strip(),
            due_at=due_at,
            repeat=repeat,
        )
        ActivityLog.objects.create(
            user=user,
            kind=ActivityKind.REMINDER_CREATE,
            title="Создано напоминание",
            detail=f"{reminder.text} → {reminder.due_at} [{reminder.repeat}]",
            meta={
                "id": reminder.id,
                "due_at": reminder.due_at.isoformat(),
                "repeat": reminder.repeat,
            },
        )
        return reminder

    def list_active(self, user: BotUser) -> list[Reminder]:
        return list(
            Reminder.objects.filter(user=user, is_done=False).order_by("due_at")
        )

    def list_due(self, now: datetime | None = None) -> list[Reminder]:
        now = now or timezone.now()
        return list(
            Reminder.objects.filter(is_done=False, due_at__lte=now).select_related("user")
        )

    def mark_sent(self, reminder: Reminder) -> None:
        now = timezone.now()
        reminder.sent_at = now
        if reminder.repeat == ReminderRepeat.DAILY:
            next_due = reminder.due_at + timedelta(days=1)
            # Keep wall-clock time; skip missed days if the process was down
            while next_due <= now:
                next_due += timedelta(days=1)
            reminder.due_at = next_due
            reminder.is_done = False
            reminder.save(update_fields=["is_done", "sent_at", "due_at"])
            detail = f"{reminder.text} (след. {timezone.localtime(next_due):%d.%m.%Y %H:%M})"
        else:
            reminder.is_done = True
            reminder.save(update_fields=["is_done", "sent_at"])
            detail = reminder.text
        ActivityLog.objects.create(
            user=reminder.user,
            kind=ActivityKind.REMINDER_SENT,
            title="Напоминание отправлено",
            detail=detail,
            meta={"id": reminder.id, "repeat": reminder.repeat},
        )
        try:
            from subscriptions.renewal_reminders import emit_renewal_app_event

            emit_renewal_app_event(reminder.user, reminder.text)
        except Exception:
            pass

    def delete_by_query(self, user: BotUser, query: str) -> Reminder | None:
        items = self.list_active(user)
        q = query.lower()
        for item in items:
            if item.text.lower() in q or any(
                token in item.text.lower() for token in q.split() if len(token) > 3
            ):
                ActivityLog.objects.create(
                    user=user,
                    kind=ActivityKind.REMINDER_DELETE,
                    title="Удалено напоминание",
                    detail=item.text,
                    meta={"id": item.id},
                )
                text = item.text
                item_id = item.id
                item.delete()
                return Reminder(id=item_id, text=text)
        return None

    def delete(self, item_id: int) -> bool:
        item = Reminder.objects.filter(pk=item_id).first()
        if not item:
            return False
        ActivityLog.objects.create(
            user=item.user,
            kind=ActivityKind.REMINDER_DELETE,
            title="Удалено напоминание",
            detail=item.text,
            meta={"id": item.id},
        )
        item.delete()
        return True

    def format_list(self, items: list[Reminder]) -> str:
        if not items:
            return "Напоминаний нет."
        lines = []
        for item in items:
            local = timezone.localtime(item.due_at).strftime("%d.%m.%Y %H:%M")
            suffix = " · каждый день" if item.repeat == ReminderRepeat.DAILY else ""
            lines.append(f"• {item.text} — {local}{suffix}")
        return "\n".join(lines)
