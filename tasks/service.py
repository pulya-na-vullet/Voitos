from __future__ import annotations

from database.models import ActivityKind, ActivityLog, BotUser, TaskItem, TaskStatus


class TaskService:
    def create(self, user: BotUser, text: str) -> TaskItem:
        task = TaskItem.objects.create(user=user, text=text.strip(), status=TaskStatus.OPEN)
        ActivityLog.objects.create(
            user=user,
            kind=ActivityKind.TASK_CREATE,
            title="Создана задача",
            detail=task.text,
            meta={"id": task.id},
        )
        return task

    def list_open(self, user: BotUser) -> list[TaskItem]:
        return list(TaskItem.objects.filter(user=user, status=TaskStatus.OPEN).order_by("created_at"))

    def list_all(self, user: BotUser, limit: int = 50) -> list[TaskItem]:
        return list(TaskItem.objects.filter(user=user).order_by("-created_at")[:limit])

    def complete_by_query(self, user: BotUser, query: str) -> TaskItem | None:
        open_tasks = self.list_open(user)
        if not open_tasks:
            return None
        q = query.lower()
        for task in open_tasks:
            if task.text.lower() in q or any(
                token in task.text.lower() for token in q.split() if len(token) > 3
            ):
                task.mark_done()
                ActivityLog.objects.create(
                    user=user,
                    kind=ActivityKind.TASK_DONE,
                    title="Задача выполнена",
                    detail=task.text,
                    meta={"id": task.id},
                )
                return task
        # fuzzy: pick best substring match
        best = None
        best_score = 0
        for task in open_tasks:
            score = sum(1 for token in q.split() if len(token) > 2 and token in task.text.lower())
            if score > best_score:
                best_score = score
                best = task
        if best and best_score > 0:
            best.mark_done()
            ActivityLog.objects.create(
                user=user,
                kind=ActivityKind.TASK_DONE,
                title="Задача выполнена",
                detail=best.text,
                meta={"id": best.id},
            )
            return best
        return None

    def delete_by_query(self, user: BotUser, query: str) -> TaskItem | None:
        tasks = list(TaskItem.objects.filter(user=user).order_by("-created_at"))
        q = query.lower()
        for task in tasks:
            if task.text.lower() in q or any(
                token in task.text.lower() for token in q.split() if len(token) > 3
            ):
                ActivityLog.objects.create(
                    user=user,
                    kind=ActivityKind.TASK_DELETE,
                    title="Удалена задача",
                    detail=task.text,
                    meta={"id": task.id},
                )
                text = task.text
                task_id = task.id
                task.delete()
                deleted = TaskItem(id=task_id, text=text)
                return deleted
        return None

    def delete(self, item_id: int) -> bool:
        item = TaskItem.objects.filter(pk=item_id).first()
        if not item:
            return False
        ActivityLog.objects.create(
            user=item.user,
            kind=ActivityKind.TASK_DELETE,
            title="Удалена задача",
            detail=item.text,
            meta={"id": item.id},
        )
        item.delete()
        return True

    def format_list(self, items: list[TaskItem]) -> str:
        if not items:
            return "Список задач пуст."
        return "\n".join(f"• {t.text}" for t in items)
