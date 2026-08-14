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

        from bot.registration import (
            REG_KIND,
            handle_registration_step,
            needs_registration,
            start_registration,
        )

        if pending.pending_kind == REG_KIND:
            reply = handle_registration_step(user, text, pending)
            self._store_out(user, reply, "registration")
            return reply

        from bot.contractor_registration import (
            CONTRACTOR_REG_KIND,
            handle_contractor_registration_step,
            start_contractor_registration,
        )

        if pending.pending_kind == CONTRACTOR_REG_KIND:
            reply = handle_contractor_registration_step(user, text, pending)
            self._store_out(user, reply, "contractor_registration")
            return reply

        from bot.work_request import WORK_REQUEST_KIND, handle_work_request_step

        if pending.pending_kind == WORK_REQUEST_KIND:
            reply = handle_work_request_step(user, text, pending)
            self._store_out(user, reply, "work_request")
            return reply

        if pending.pending_kind == "contractor_offer_reply":
            from services.contractors import handle_offer_reply

            reply = handle_offer_reply(user, text, pending)
            if reply is not None:
                self._store_out(user, reply, "contractor_offer")
                return reply

        if pending.pending_kind == "volunteer_help_reply":
            from services.volunteer import handle_volunteer_help_reply

            reply = handle_volunteer_help_reply(user, text, pending)
            if reply is not None:
                self._store_out(user, reply, "volunteer_help")
                return reply

        if pending.pending_kind == "reminder_time":
            reply = self._finish_pending_reminder(user, text, pending)
            if reply is not None:
                self._store_out(user, reply, "create_reminder")
                return reply

        if pending.pending_kind == "service_invite_pick":
            reply = self._pick_service_invite(user, text, pending)
            if reply is not None:
                self._store_out(user, reply, "service_collections")
                return reply

        if pending.pending_kind == "wish_group_pick":
            reply = self._pick_wish_group(user, text, pending)
            if reply is not None:
                self._store_out(user, reply, "neighborhood_wish")
                return reply

        if needs_registration(user):
            # Allow help/subscription/collections meta commands before forcing form
            from ai.intent import (
                CONTRACTOR_REG_RE,
                HELP_RE,
                SERVICE_COLLECTIONS_RE,
                SUBSCRIPTION_RE,
                WISHES_LIST_RE,
            )

            if CONTRACTOR_REG_RE.match(text):
                reply = start_contractor_registration(user, pending)
                self._store_out(user, reply, "contractor_registration")
                return reply

            if not (
                HELP_RE.match(text)
                or SUBSCRIPTION_RE.match(text)
                or SERVICE_COLLECTIONS_RE.match(text)
                or WISHES_LIST_RE.match(text)
            ):
                reply = start_registration(user, pending)
                self._store_out(user, reply, "registration")
                return reply

        intent = self.analyzer.analyze(text)
        reply = self._dispatch(user, text, intent, pending)

        self._store_out(user, reply, intent.intent)

        # Keep last user text for "Запомни это"
        if intent.intent not in {"force_remember", "force_forget", "registration"}:
            pending.last_user_text = text
            pending.save(update_fields=["last_user_text", "updated_at"])

        return reply

    def _store_out(self, user: BotUser, reply: str, intent: str) -> None:
        ChatMessage.objects.create(
            user=user,
            role=MessageRole.ASSISTANT,
            text=reply,
            intent=intent,
        )
        ActivityLog.objects.create(
            user=user,
            kind=ActivityKind.MESSAGE_OUT,
            title="Исходящее сообщение",
            detail=reply[:500],
            meta={"intent": intent},
        )

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

        if intent.intent == "service_collections":
            from services.service import format_collections_for_user

            return format_collections_for_user(user)

        if intent.intent == "list_wishes":
            from services.wishes import format_user_wishes_reply

            return format_user_wishes_reply(user)

        if intent.intent == "neighborhood_wish":
            return self._capture_neighborhood_wish(user, text, intent, pending)

        if intent.intent == "registration":
            from bot.registration import start_registration

            return start_registration(user, pending)

        if intent.intent == "contractor_registration":
            from bot.contractor_registration import start_contractor_registration

            # Команда «стать исполнителем» — всегда показываем полный список ролей.
            # Роль по номеру/названию человек выбирает следующим сообщением.
            return start_contractor_registration(user, pending)

        if intent.intent == "work_request":
            from bot.work_request import start_work_request

            return start_work_request(user, pending, text=text)

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

    def _pick_service_invite(self, user: BotUser, text: str, pending: PendingAction) -> str | None:
        """User chooses subscription (#1) or a campaign for a pending receipt."""
        import base64

        from services.service import (
            format_receipt_pick_menu,
            open_invites_for_user,
            submit_service_receipt,
        )
        from subscriptions.service import payment_help_text, submit_receipt

        invites = open_invites_for_user(user)
        payload = pending.pending_payload or {}
        raw_b64 = payload.get("image_b64")
        filename = payload.get("filename") or "receipt.jpg"
        if not raw_b64:
            pending.clear_pending()
            return "Не нашёл сохранённый чек. Пришлите фото ещё раз."

        lower = text.lower().strip()
        if lower in {"отмена", "стоп"}:
            pending.clear_pending()
            return "Ок, чек не прикрепляю. Пришлите снова, когда будете готовы."

        choose_subscription = lower in {"1", "подписка", "подписку"} or lower.startswith(
            "подписк"
        )
        chosen = None
        if not choose_subscription and lower.isdigit():
            # Menu: 1=subscription, 2..=invites
            idx = int(lower) - 2
            if 0 <= idx < len(invites):
                chosen = invites[idx]
        if not choose_subscription and chosen is None:
            for inv in invites:
                title = inv.campaign.title.lower()
                cat = inv.campaign.get_category_display().lower()
                if lower in title or lower in cat or any(
                    t in title for t in lower.split() if len(t) > 3
                ):
                    chosen = inv
                    break
        if not choose_subscription and chosen is None:
            return "Не понял выбор.\n\n" + format_receipt_pick_menu(invites)

        try:
            file_bytes = base64.b64decode(raw_b64)
        except Exception:
            logger.exception("Invalid pending receipt payload")
            pending.clear_pending()
            return "Не удалось прочитать сохранённый чек. Пришлите PDF или скрин ещё раз."

        if choose_subscription:
            try:
                receipt = submit_receipt(user, file_bytes, filename=filename)
            except Exception:
                logger.exception("Subscription receipt submit after pick failed")
                pending.clear_pending()
                return (
                    "Не удалось сохранить чек подписки. Пришлите PDF или скрин ещё раз.\n\n"
                    + payment_help_text()
                )
            pending.clear_pending()
            amount_line = f"Сумма: {receipt.amount or 'будет проверена администратором'} ₽"
            if receipt.transfer_date:
                amount_line += f", дата: {receipt.transfer_date.strftime('%d.%m.%Y')}"
            return (
                "Ваш чек отправлен на проверку администратору.\n"
                f"{amount_line}."
            )

        try:
            receipt = submit_service_receipt(
                user,
                file_bytes,
                invite=chosen,
                filename=filename,
            )
        except Exception:
            logger.exception("Service receipt submit after pick failed")
            pending.clear_pending()
            return "Не удалось сохранить чек. Пришлите PDF или скрин ещё раз."
        pending.clear_pending()
        return (
            f"Ваш чек по «{chosen.campaign.title}» отправлен на проверку администратору.\n"
            f"Сумма: {receipt.amount or 'будет проверена администратором'} ₽."
        )

    def _capture_neighborhood_wish(
        self,
        user: BotUser,
        text: str,
        intent: IntentResult,
        pending: PendingAction,
    ) -> str:
        from services.wishes import (
            capture_wish,
            detect_topic,
            extract_wish_text,
            user_groups,
        )

        body = (intent.wish_text or extract_wish_text(text)).strip()
        topic = intent.topic or detect_topic(body)
        groups = user_groups(user)
        if not groups:
            return (
                "Записал бы пожелание, но вас пока нет в группе жителей. "
                "Попросите администратора добавить вас в состав группы — "
                "тогда голоса соседей будут считаться вместе."
            )
        if len(groups) == 1:
            wish = capture_wish(
                user,
                body,
                group=groups[0],
                topic=topic,
                confidence=intent.confidence or 0.7,
                source_message=text,
            )
            return (
                f"Принято для группы «{groups[0].name}».\n"
                f"Тема: {wish.get_topic_display()}.\n"
                "Соседи могут писать идеи так же — смотреть итог: «пожелания»."
            )

        pending.pending_kind = "wish_group_pick"
        pending.pending_payload = {
            "text": body,
            "topic": topic,
            "confidence": intent.confidence or 0.7,
            "group_ids": [g.id for g in groups],
        }
        pending.save(update_fields=["pending_kind", "pending_payload", "updated_at"])
        lines = ["Вы в нескольких группах. Куда записать пожелание? Напишите номер:"]
        for i, g in enumerate(groups, start=1):
            lines.append(f"{i}. {g.name}")
        return "\n".join(lines)

    def _pick_wish_group(
        self, user: BotUser, text: str, pending: PendingAction
    ) -> str | None:
        from database.models import ServiceGroup
        from services.wishes import capture_wish

        lower = text.lower().strip()
        if lower in {"отмена", "отменить", "стоп", "не надо"}:
            pending.clear_pending()
            return "Ок, пожелание не сохраняю."

        payload = pending.pending_payload or {}
        group_ids = list(payload.get("group_ids") or [])
        groups = list(ServiceGroup.objects.filter(id__in=group_ids).order_by("id"))
        if not groups:
            pending.clear_pending()
            return "Группы не найдены. Напишите пожелание ещё раз."

        chosen = None
        if text.strip().isdigit():
            idx = int(text.strip())
            if 1 <= idx <= len(groups):
                chosen = groups[idx - 1]
        if chosen is None:
            for g in groups:
                if g.name.lower() in lower or lower in g.name.lower():
                    chosen = g
                    break
        if chosen is None:
            lines = ["Не понял номер. Выберите группу:"]
            for i, g in enumerate(groups, start=1):
                lines.append(f"{i}. {g.name}")
            return "\n".join(lines)

        wish = capture_wish(
            user,
            payload.get("text") or text,
            group=chosen,
            topic=payload.get("topic"),
            confidence=float(payload.get("confidence") or 0.7),
            source_message=payload.get("text") or text,
        )
        pending.clear_pending()
        return (
            f"Принято для группы «{chosen.name}».\n"
            f"Тема: {wish.get_topic_display()}.\n"
            "Смотреть итог голосования: «пожелания»."
        )

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
