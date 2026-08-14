from __future__ import annotations

from django.contrib.auth.models import User
from django.test import Client, TestCase

from database.models import (
    AdminTask,
    AdminTaskKind,
    AdminTaskStatus,
    BotUser,
    PanelProfile,
    PanelRole,
)
from panel.admin_tasks import build_task_sections, tasks_fingerprint


class AdminTasksLiveFeedTests(TestCase):
    def setUp(self) -> None:
        self.admin = User.objects.create_superuser("adm", "a@t.com", "pass")
        PanelProfile.objects.create(user=self.admin, role=PanelRole.ADMIN)
        self.client = Client()
        self.client.login(username="adm", password="pass")
        self.bot_user = BotUser.objects.create(max_user_id="live1", display_name="Live")

    def test_feed_unchanged_then_changed(self):
        resp = self.client.get("/panel/tasks/today/feed/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["changed"])
        self.assertIn("html", data)
        fp = data["fingerprint"]

        resp2 = self.client.get(f"/panel/tasks/today/feed/?fp={fp}")
        self.assertEqual(resp2.status_code, 200)
        data2 = resp2.json()
        self.assertFalse(data2["changed"])

        AdminTask.objects.create(
            kind=AdminTaskKind.PAYMENT_RECEIPT,
            title="Чек подписки #1",
            user=self.bot_user,
            status=AdminTaskStatus.OPEN,
            priority=10,
        )
        resp3 = self.client.get(f"/panel/tasks/today/feed/?fp={fp}")
        data3 = resp3.json()
        self.assertTrue(data3["changed"])
        self.assertEqual(data3["total"], 1)
        self.assertIn("Чек подписки #1", data3["html"])

    def test_today_page_has_live_root(self):
        resp = self.client.get("/panel/tasks/today/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "tasks-live-root")
        self.assertContains(resp, "tasks/today/feed")

    def test_fingerprint_changes_with_task(self):
        before = tasks_fingerprint()
        AdminTask.objects.create(
            kind=AdminTaskKind.PROFILE_REVIEW,
            title="Анкета",
            user=self.bot_user,
            status=AdminTaskStatus.OPEN,
        )
        after = tasks_fingerprint()
        self.assertNotEqual(before, after)
        sections, total, _ = build_task_sections()
        self.assertEqual(total, 1)
        self.assertEqual(len(sections), 1)
