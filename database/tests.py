from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from ai.intent import IntentAnalyzer, _parse_due
from bot.pipeline import MessagePipeline
from database.models import BotUser, MemoryItem, Reminder, TaskItem, TaskStatus
from memory.service import MemoryService
from reminders.service import ReminderService
from tasks.service import TaskService


class IntentRulesTests(TestCase):
    def setUp(self) -> None:
        self.analyzer = IntentAnalyzer()

    def test_help_command(self):
        r = self.analyzer.analyze("помощь")
        self.assertEqual(r.intent, "help")

    def test_subscription_command(self):
        r = self.analyzer.analyze("подписка")
        self.assertEqual(r.intent, "subscription_info")

    def test_force_remember(self):
        r = self.analyzer.analyze("Запомни это.")
        self.assertEqual(r.intent, "force_remember")

    def test_force_forget(self):
        r = self.analyzer.analyze("Не запоминай")
        self.assertEqual(r.intent, "force_forget")

    def test_list_tasks(self):
        r = self.analyzer.analyze("Что мне нужно сделать?")
        self.assertEqual(r.intent, "list_tasks")

    def test_search_memory(self):
        r = self.analyzer.analyze("Что ты помнишь про холодильник?")
        self.assertEqual(r.intent, "search_memory")

    def test_parse_day_of_month(self):
        due = _parse_due("напомни 20 числа оплатить")
        self.assertIsNotNone(due)
        self.assertEqual(due.day, 20)

    def test_parse_in_one_minute(self):
        from django.utils import timezone as tz

        before = tz.localtime()
        due = _parse_due("У меня сегодня через 1 минуту звонок напиши мне об этом")
        self.assertIsNotNone(due)
        delta = (due - before).total_seconds()
        self.assertGreater(delta, 30)
        self.assertLess(delta, 90)
        self.assertEqual(due.year, before.year)

    def test_resolve_rejects_past_llm_date(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        from ai.intent import resolve_reminder_due
        from django.utils import timezone as tz

        bad = datetime(2023, 5, 10, 12, 1, tzinfo=ZoneInfo("Europe/Moscow"))
        due = resolve_reminder_due(
            "сегодня через 1 минуту звонок",
            llm_due=bad,
        )
        self.assertGreaterEqual(due.year, tz.localtime().year)
        self.assertGreater(due, tz.localtime() - timedelta(seconds=5))

    def test_daily_morning_breakfast_reminder(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        from ai.intent import resolve_reminder_schedule

        text = (
            'Сделай мне каждодневное напоминание с текстом: '
            '"Доброе утро! Приятного завтрака и полюби свою семью!)"'
        )
        # LLM wrongly suggests 01:00 — must be ignored for morning wording
        bad = datetime(2026, 7, 31, 1, 0, tzinfo=ZoneInfo("Europe/Moscow"))
        schedule = resolve_reminder_schedule(text, llm_due=bad, reminder_text="Доброе утро! …")
        self.assertFalse(schedule.needs_clarification)
        self.assertEqual(schedule.repeat, "daily")
        self.assertIsNotNone(schedule.due_at)
        self.assertEqual(schedule.due_at.hour, 9)
        self.assertEqual(schedule.due_at.minute, 0)

    def test_unclear_reminder_asks_clarify(self):
        from ai.intent import resolve_reminder_schedule

        schedule = resolve_reminder_schedule("Напомни про важное")
        self.assertTrue(schedule.needs_clarification)
        self.assertIsNone(schedule.due_at)


class ServicesTests(TestCase):
    def setUp(self) -> None:
        self.user = BotUser.objects.create(max_user_id="1", display_name="Test")

    def test_memory_save_and_search(self):
        svc = MemoryService()
        svc.save(self.user, "Купила холодильник Bosch", category="purchases")
        found = svc.search(self.user, "холодильник")
        self.assertEqual(len(found), 1)
        self.assertIn("Bosch", found[0].text)

    def test_task_flow(self):
        svc = TaskService()
        svc.create(self.user, "Купить подарок маме")
        open_tasks = svc.list_open(self.user)
        self.assertEqual(len(open_tasks), 1)
        done = svc.complete_by_query(self.user, "Выполнил подарок")
        self.assertIsNotNone(done)
        self.assertEqual(done.status, TaskStatus.DONE)

    def test_reminder_create(self):
        svc = ReminderService()
        due = timezone.now() + timedelta(hours=2)
        rem = svc.create(self.user, "Оплатить коммуналку", due)
        self.assertFalse(rem.is_done)
        self.assertEqual(Reminder.objects.count(), 1)

    def test_daily_reminder_reschedules_after_send(self):
        svc = ReminderService()
        due = timezone.now() - timedelta(minutes=1)
        rem = svc.create(self.user, "Доброе утро!", due, repeat="daily")
        before = rem.due_at
        svc.mark_sent(rem)
        rem.refresh_from_db()
        self.assertFalse(rem.is_done)
        self.assertGreater(rem.due_at, before)
        self.assertGreater(rem.due_at, timezone.now())


class PipelineTests(TestCase):
    def setUp(self) -> None:
        from database.models import ProfileStatus

        self.user = BotUser.objects.create(
            max_user_id="42",
            display_name="Client",
            real_name="Client",
            phone="89001112233",
            address="Казань, ул. Тестовая, 1",
            locality="Казань",
            profile_status=ProfileStatus.VERIFIED,
        )
        self.pipeline = MessagePipeline()

    def test_explicit_remember(self):
        self.pipeline.handle(self.user, "У меня аллергия на пенициллин.")
        reply = self.pipeline.handle(self.user, "Запомни это.")
        self.assertIn("Запомнил", reply)
        self.assertTrue(MemoryItem.objects.filter(user=self.user).exists())

    def test_do_not_remember(self):
        self.pipeline.handle(self.user, "Сегодня идёт дождь.")
        reply = self.pipeline.handle(self.user, "Не запоминай.")
        self.assertIn("не запоминаю", reply.lower())

    @patch("bot.pipeline.IntentAnalyzer.analyze")
    def test_save_memory_intent(self, analyze):
        from ai.intent import IntentResult

        analyze.return_value = IntentResult(
            intent="save_memory",
            should_save=True,
            memory_text="Купила холодильник Bosch",
            category="purchases",
            confidence=0.9,
        )
        reply = self.pipeline.handle(self.user, "Купила новый холодильник Bosch.")
        self.assertEqual(reply, "Запомнил.")
        self.assertEqual(MemoryItem.objects.filter(user=self.user).count(), 1)

    @patch("bot.pipeline.IntentAnalyzer.analyze")
    def test_create_task_intent(self, analyze):
        from ai.intent import IntentResult

        analyze.return_value = IntentResult(
            intent="create_task",
            task_text="Купить подарок маме",
            confidence=0.9,
        )
        reply = self.pipeline.handle(self.user, "Нужно купить подарок маме.")
        self.assertIn("задач", reply.lower())
        self.assertEqual(TaskItem.objects.filter(user=self.user).count(), 1)

    def test_list_tasks_command(self):
        TaskService().create(self.user, "Позвонить стоматологу")
        reply = self.pipeline.handle(self.user, "Что мне нужно сделать?")
        self.assertIn("стоматологу", reply)

    def test_help_shows_features_and_days(self):
        self.user.subscription_until = timezone.now() + timedelta(days=12)
        self.user.grace_until = None
        self.user.save()
        reply = self.pipeline.handle(self.user, "помощь")
        self.assertIn("Что умеет бот", reply)
        self.assertIn("придомовой", reply.lower())
        self.assertIn("подписка", reply.lower())
        self.assertIn("дн", reply)

    def test_description_command_alias(self):
        reply = self.pipeline.handle(self.user, "описание")
        self.assertIn("Voitos", reply)
        self.assertIn("самозанят", reply.lower())
        self.assertIn("сборы", reply.lower())

    def test_subscription_command_details(self):
        from database.models import PaymentReceipt, ReceiptStatus

        self.user.subscription_until = timezone.now() + timedelta(days=5)
        self.user.save()
        PaymentReceipt.objects.create(
            user=self.user,
            amount=100,
            status=ReceiptStatus.APPROVED,
            admin_comment="Оплата подтверждена, спасибо",
            months_granted=1,
        )
        reply = self.pipeline.handle(self.user, "подписка")
        self.assertIn("Условия пользования", reply)
        self.assertIn("Осталось дней", reply)
        self.assertIn("Оплата подтверждена", reply)
        self.assertIn("89625507832", reply)

    @patch("bot.pipeline.IntentAnalyzer.analyze")
    def test_reminder_clarification_then_time(self, analyze):
        from ai.intent import IntentResult

        analyze.return_value = IntentResult(
            intent="create_reminder",
            reminder_text="Важная встреча",
            due_at=None,
            needs_time_clarify=True,
            confidence=0.9,
        )
        reply = self.pipeline.handle(self.user, "Напомни про важную встречу")
        self.assertIn("Когда", reply)
        self.assertEqual(Reminder.objects.count(), 0)

        # Second message answers the time — pending handler, no analyze needed
        reply2 = self.pipeline.handle(self.user, "завтра в 15:00")
        self.assertIn("Готово", reply2)
        rem = Reminder.objects.get()
        self.assertEqual(rem.text, "Важная встреча")
        self.assertEqual(timezone.localtime(rem.due_at).hour, 15)

    @patch("bot.pipeline.IntentAnalyzer.analyze")
    def test_daily_morning_creates_repeating(self, analyze):
        from ai.intent import IntentResult

        text = (
            'Сделай мне каждодневное напоминание с текстом: '
            '"Доброе утро! Приятного завтрака и полюби свою семью!)"'
        )
        analyze.return_value = IntentResult(
            intent="create_reminder",
            reminder_text="Доброе утро! Приятного завтрака и полюби свою семью!)",
            repeat="daily",
            confidence=0.9,
        )
        reply = self.pipeline.handle(self.user, text)
        self.assertIn("каждый день", reply.lower())
        rem = Reminder.objects.get()
        self.assertEqual(rem.repeat, "daily")
        self.assertEqual(timezone.localtime(rem.due_at).hour, 9)
        self.assertIn("Доброе утро", rem.text)
