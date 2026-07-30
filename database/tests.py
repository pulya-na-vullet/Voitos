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


class PipelineTests(TestCase):
    def setUp(self) -> None:
        self.user = BotUser.objects.create(max_user_id="42", display_name="Client")
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
