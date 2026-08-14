"""Tests for work requests and executor roles."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import Client, TestCase
from django.urls import reverse

from database.models import (
    BotUser,
    ExecutorRole,
    PanelActionLog,
    PanelProfile,
    PanelRole,
    PendingAction,
    ServiceGroup,
    WorkRequest,
)
from bot.work_request import handle_work_request_photo, handle_work_request_step, start_work_request
from panel.manager_log import log_manager_action
from panel.roles import assign_group_manager
from services.executor_roles import ensure_default_equipment_roles

User = get_user_model()


class ExecutorRolesPanelTests(TestCase):
    def setUp(self):
        ensure_default_equipment_roles()
        self.admin = User.objects.create_superuser("adm", "a@t.com", "pass")
        PanelProfile.objects.create(user=self.admin, role=PanelRole.ADMIN)
        self.client = Client()
        self.client.login(username="adm", password="pass")

    def test_roles_page_and_create(self):
        resp = self.client.get(reverse("panel:executor_roles"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Трактор")
        resp = self.client.post(
            reverse("panel:executor_roles"),
            {
                "action": "create",
                "code": "plumber",
                "name": "Сантехник",
                "requires_qualification_docs": "on",
                "sort_order": "50",
            },
        )
        self.assertEqual(resp.status_code, 302)
        role = ExecutorRole.objects.get(code="plumber")
        self.assertTrue(role.requires_qualification_docs)


class WorkRequestBotTests(TestCase):
    def setUp(self):
        ensure_default_equipment_roles()
        ExecutorRole.objects.get_or_create(
            code="electrician",
            defaults={
                "name": "Электрик",
                "requires_qualification_docs": True,
                "sort_order": 60,
            },
        )
        self.user = BotUser.objects.create(max_user_id="wr1", real_name="Аня")
        self.pending, _ = PendingAction.objects.get_or_create(user=self.user)

    def test_work_request_flow(self):
        msg = start_work_request(self.user, self.pending, text="нужен электрик")
        self.assertIn("электрик", msg.lower())
        msg = handle_work_request_step(self.user, "Починить розетку на кухне", self.pending)
        self.assertIn("фото", msg.lower())
        msg = handle_work_request_photo(
            self.user,
            self.pending,
            image_bytes=b"fakepng",
            filename="a.jpg",
        )
        self.assertIn("добавлено", msg.lower())
        msg = handle_work_request_step(self.user, "готово", self.pending)
        self.assertIn("отправлена", msg.lower())
        self.assertEqual(WorkRequest.objects.filter(user=self.user).count(), 1)
        req = WorkRequest.objects.get(user=self.user)
        self.assertEqual(req.role.code, "electrician")
        self.assertEqual(req.photos.count(), 1)


class ManagerLogTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser("adm2", "b@t.com", "pass")
        PanelProfile.objects.create(user=self.admin, role=PanelRole.ADMIN)
        self.bot = BotUser.objects.create(max_user_id="ml1", real_name="Менеджер")
        self.g = ServiceGroup.objects.create(name="Г1")
        self.g.members.add(self.bot)
        self.mgr, _ = assign_group_manager(self.g, self.bot, password="MgrPass1!")
        self.client = Client()

    def test_log_only_for_manager_and_admin_sees(self):
        log_manager_action(self.mgr, action="test", title="Тест действие")
        self.assertEqual(PanelActionLog.objects.filter(actor=self.mgr).count(), 1)
        # admin action not logged as manager
        log_manager_action(self.admin, action="test", title="Админ")
        self.assertEqual(PanelActionLog.objects.filter(actor=self.admin).count(), 0)

        self.client.login(username="adm2", password="pass")
        resp = self.client.get(reverse("panel:manager_logs"))
        self.assertEqual(resp.status_code, 200)
        resp = self.client.get(reverse("panel:manager_log_detail", args=[self.mgr.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Тест действие")

        self.client.logout()
        self.client.login(username=self.mgr.username, password="MgrPass1!")
        resp = self.client.get(reverse("panel:manager_logs"))
        self.assertEqual(resp.status_code, 302)
