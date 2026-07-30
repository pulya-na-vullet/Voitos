from __future__ import annotations

from datetime import datetime

from django.utils import timezone

from database.models import ActivityKind, ActivityLog, BotUser, Reminder


class ReminderService:
    def create(self, user: BotUser, text: str, due_at: datetime) -> Reminder:
        if timezone.is_naive(due_at):
            due_at = timezone.make_aware(due_at, timezone.get_current_timezone())
        reminder = Reminder.objects.create(user=user, text=text.strip(), due_at=due_at)
        ActivityLog.objects.create(
            user=user,
            kind=ActivityKind.REMINDER_CREATE,
            title="Создано напоминание",
            detail=f"{reminder.text} → {reminder.due_at}",
            meta={"id": reminder.id, "due_at": reminder.due_at.isoformat()},
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
        reminder.is_done = True
        reminder.sent_at = timezone.now()
        reminder.save(update_fields=["is_done", "sent_at"])
        ActivityLog.objects.create(
            user=reminder.user,
            kind=ActivityKind.REMINDER_SENT,
            title="Напоминание отправлено",
            detail=reminder.text,
            meta={"id": reminder.id},
        )

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
            lines.append(f"• {item.text} — {local}")
        return "\n".join(lines)
