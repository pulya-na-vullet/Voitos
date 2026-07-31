from __future__ import annotations

import logging

from django.utils import timezone

from ai.factory import AINotConfiguredError, get_llm_provider
from ai.intent import IntentAnalyzer, IntentResult
from database.models import (
    ActivityKind,
    ActivityLog,
    BotUser,
    ChatMessage,
    MessageRole,
    PendingAction,
    ReminderRepeat,
)
from memory.service import MemoryService
from reminders.service import ReminderService
from tasks.service import TaskService

logger = logging.getLogger(__name__)

CHAT_SYSTEM = """Ты Voitos — спокойный персональный помощник.
Отвечай кратко, по-русски, без воды.
Не выдумывай факты о пользователе — опирайся на память ниже, если она есть.
Если данных недостаточно — скажи об этом коротко.
"""


class MessagePipeline:
    def __init__(self) -> None:
        self.analyzer = IntentAnalyzer()
        self.memory = MemoryService()
        self.tasks = TaskService()
        self.reminders = ReminderService()

    def handle(self, user: BotUser, text: str, *, is_voice: bool = False, voice_transcript: str = "") -> str:
        text = (text or "").strip()
        if not text:
            return "Не расслышал. Напиши ещё раз."

        ChatMessage.objects.create(
            user=user,
            role=MessageRole.USER,
            text=text,
            is_voice=is_voice,
            voice_transcript=voice_transcript,
        )
        ActivityLog.objects.create(
            user=user,
            kind=ActivityKind.MESSAGE_IN,
            title="Входящее сообщение",
            detail=text[:500],
            meta={"is_voice": is_voice},
        )

        pending, _ = PendingAction.objects.get_or_create(user=user)
        if pending.pending_kind == "reminder_time":
            reply = self._finish_pending_reminder(user, text, pending)
            if reply is not None:
                ChatMessage.objects.create(
                    user=user,
                    role=MessageRole.ASSISTANT,
                    text=reply,
                    intent="create_reminder",
                )
                ActivityLog.objects.create(
                    user=user,
                    kind=ActivityKind.MESSAGE_OUT,
                    title="Исходящее сообщение",
                    detail=reply[:500],
                    meta={"intent": "create_reminder", "pending": "reminder_time"},
                )
                return reply

        intent = self.analyzer.analyze(text)
        reply = self._dispatch(user, text, intent, pending)

        ChatMessage.objects.create(
            user=user,
            role=MessageRole.ASSISTANT,
            text=reply,
            intent=intent.intent,
        )
        ActivityLog.objects.create(
            user=user,
            kind=ActivityKind.MESSAGE_OUT,
            title="Исходящее сообщение",
            detail=reply[:500],
            meta={"intent": intent.intent},
        )

        # Keep last user text for "Запомни это"
        if intent.intent not in {"force_remember", "force_forget"}:
            pending.last_user_text = text
            pending.save(update_fields=["last_user_text", "updated_at"])

        return reply

    def _dispatch(
        self,
        user: BotUser,
        text: str,
        intent: IntentResult,
        pending: PendingAction,
    ) -> str:
        if intent.intent == "help":
            from bot.messages import help_message

            return help_message(user)

        if intent.intent == "subscription_info":
            from bot.messages import subscription_detail_message

            return subscription_detail_message(user)

        if intent.intent == "force_remember":
            source = pending.last_user_text.strip()
            if text.lower().startswith("запомни") and "это" not in text.lower()[:12]:
                # "Запомни: факт"
                import re

                body = re.sub(r"^запомни[:\s]+", "", text, flags=re.IGNORECASE).strip()
                if body and body.lower() != "это" and body.lower() != "это.":
                    source = body
            if not source:
                return "Нечего запоминать. Напиши информацию, затем «Запомни это»."
            self.memory.save(user, source, category=intent.category or "other", source_message=source)
            return "Запомнил."

        if intent.intent == "force_forget":
            pending.last_user_text = ""
            pending.save(update_fields=["last_user_text", "updated_at"])
            return "Хорошо, не запоминаю."

        if intent.intent == "save_memory" or (intent.should_save and intent.memory_text):
            fact = (intent.memory_text or text).strip()
            self.memory.save(user, fact, category=intent.category, source_message=text)
            return "Запомнил." if intent.confidence >= 0.6 else "Сохранил."

        if intent.intent == "create_reminder":
            return self._create_reminder_from_intent(user, text, intent, pending)

        if intent.intent == "create_task":
            task_text = (intent.task_text or text).strip()
            self.tasks.create(user, task_text)
            return "Добавил в список задач."

        if intent.intent == "complete_task":
            task = self.tasks.complete_by_query(user, intent.query or text)
            if not task:
                return "Не нашёл такую задачу."
            return f"Отметил выполненным: {task.text}"

        if intent.intent == "delete_task":
            task = self.tasks.delete_by_query(user, intent.query or text)
            if not task:
                return "Не нашёл задачу для удаления."
            return f"Удалил задачу: {task.text}"

        if intent.intent == "delete_reminder":
            rem = self.reminders.delete_by_query(user, intent.query or text)
            if not rem:
                return "Не нашёл напоминание для удаления."
            return f"Удалил напоминание: {rem.text}"

        if intent.intent == "list_tasks":
            return self.tasks.format_list(self.tasks.list_open(user))

        if intent.intent == "list_reminders":
            return self.reminders.format_list(self.reminders.list_active(user))

        if intent.intent == "list_memory":
            return self.memory.format_list(self.memory.list_all(user))

        if intent.intent == "search_memory":
            items = self.memory.search(user, intent.query or text)
            if not items:
                return "Ничего не нашёл в памяти."
            # Prefer a short natural answer for single hit
            if len(items) == 1:
                return items[0].text
            return self.memory.format_list(items)

        return self._chat(user, text)

    def _create_reminder_from_intent(
        self,
        user: BotUser,
        text: str,
        intent: IntentResult,
        pending: PendingAction,
    ) -> str:
        from ai.intent import extract_reminder_body, resolve_reminder_schedule

        body = extract_reminder_body(text, intent.reminder_text)
        schedule = resolve_reminder_schedule(
            text,
            llm_due=intent.due_at,
            hint=intent.due_hint,
            reminder_text=body,
            llm_repeat=intent.repeat,
        )
        if schedule.needs_clarification or not schedule.due_at:
            pending.pending_kind = "reminder_time"
            pending.pending_payload = {
                "text": body or text,
                "repeat": schedule.repeat or intent.repeat or ReminderRepeat.NONE,
            }
            pending.save(update_fields=["pending_kind", "pending_payload", "updated_at"])
            return schedule.clarify_question or (
                "Когда напомнить? Напиши время, например: «завтра в 10:00»."
            )

        rem = self.reminders.create(
            user,
            body or text,
            schedule.due_at,
            repeat=schedule.repeat or ReminderRepeat.NONE,
        )
        pending.clear_pending()
        when = timezone.localtime(rem.due_at).strftime("%d.%m.%Y %H:%M")
        if rem.repeat == ReminderRepeat.DAILY:
            clock = timezone.localtime(rem.due_at).strftime("%H:%M")
            return f"Готово. Буду напоминать каждый день в {clock}. Первый раз — {when}."
        return f"Готово. Напомню {when}."

    def _finish_pending_reminder(
        self, user: BotUser, text: str, pending: PendingAction
    ) -> str | None:
        from ai.intent import resolve_reminder_schedule

        lower = text.lower().strip()
        if lower in {"отмена", "отменить", "не надо", "не нужно", "стоп"}:
            pending.clear_pending()
            return "Ок, напоминание не создаю."

        payload = pending.pending_payload or {}
        body = (payload.get("text") or "").strip() or "Напоминание"
        repeat = payload.get("repeat") or ReminderRepeat.NONE
        # Combine original request context with the time answer
        combined = f"{body}. {text}"
        schedule = resolve_reminder_schedule(
            text,
            reminder_text=combined,
            llm_repeat=repeat,
            time_answer_only=True,
        )
        # If user started a completely different request, don't trap them
        if schedule.needs_clarification and any(
            k in lower for k in ("задач", "запомн", "что ты помн", "привет")
        ):
            pending.clear_pending()
            return None

        if schedule.needs_clarification or not schedule.due_at:
            return (
                schedule.clarify_question
                or "Не понял время. Напиши, например: «в 9 утра» или «завтра в 10:00»."
            )

        if repeat == ReminderRepeat.DAILY or schedule.repeat == ReminderRepeat.DAILY:
            schedule.repeat = ReminderRepeat.DAILY

        rem = self.reminders.create(
            user,
            body,
            schedule.due_at,
            repeat=schedule.repeat or ReminderRepeat.NONE,
        )
        pending.clear_pending()
        when = timezone.localtime(rem.due_at).strftime("%d.%m.%Y %H:%M")
        if rem.repeat == ReminderRepeat.DAILY:
            clock = timezone.localtime(rem.due_at).strftime("%H:%M")
            return f"Готово. Буду напоминать каждый день в {clock}. Первый раз — {when}."
        return f"Готово. Напомню {when}."

    def _chat(self, user: BotUser, text: str) -> str:
        memories = self.memory.list_all(user, limit=30)
        memory_block = "\n".join(f"- [{m.get_category_display()}] {m.text}" for m in memories) or "пусто"
        open_tasks = self.tasks.list_open(user)
        tasks_block = "\n".join(f"- {t.text}" for t in open_tasks) or "пусто"
        system = (
            f"{CHAT_SYSTEM}\n\nПамять пользователя:\n{memory_block}\n\n"
            f"Открытые задачи:\n{tasks_block}"
        )
        try:
            llm = get_llm_provider()
            answer = llm.complete_text(system, text, temperature=0.4, max_tokens=500)
            return answer or "Понял."
        except AINotConfiguredError as exc:
            return str(exc)
        except Exception:
            logger.exception("Chat completion failed")
            return "Сейчас не могу ответить. Проверь настройки ИИ в панели."
