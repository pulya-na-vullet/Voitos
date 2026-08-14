"""Tests for panel admin/manager role model."""

from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from database.models import BotUser, PanelProfile, PanelRole, ServiceGroup
from panel.roles import (
    assign_group_manager,
    can_access_bot_user,
    can_access_group,
    is_panel_admin,
    is_panel_manager,
    manager_credentials_max_message,
    manager_group_ids,
)

User = get_user_model()


class PanelRolesTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser("admin", "a@t.com", "adminpass")
        PanelProfile.objects.create(user=self.admin, role=PanelRole.ADMIN)
        self.u1 = BotUser.objects.create(
            max_user_id="1001", real_name="Иван", phone="89001112233", chat_id="c1"
        )
        self.u2 = BotUser.objects.create(
            max_user_id="1002", real_name="Пётр", phone="89004445566", chat_id="c2"
        )
        self.u3 = BotUser.objects.create(max_user_id="1003", real_name="Чужой")
        self.g1 = ServiceGroup.objects.create(name="Двор 1")
        self.g2 = ServiceGroup.objects.create(name="Двор 2")
        self.g1.members.add(self.u1, self.u2)
        self.g2.members.add(self.u1, self.u2)
        self.client = Client()

    def test_assign_manager_one_per_group_many_groups(self):
        mgr_user, plain = assign_group_manager(self.g1, self.u1, password="Secret123!")
        self.assertTrue(plain)
        self.g1.refresh_from_db()
        self.assertEqual(self.g1.manager_id, mgr_user.id)
        self.assertTrue(is_panel_manager(mgr_user))
        self.assertFalse(is_panel_admin(mgr_user))

        # Тот же менеджер на вторую группу
        assign_group_manager(self.g2, self.u1, password=None)
        self.g2.refresh_from_db()
        self.assertEqual(self.g2.manager_id, mgr_user.id)
        self.assertEqual(set(manager_group_ids(mgr_user)), {self.g1.id, self.g2.id})

        # Нельзя назначить не из группы
        with self.assertRaises(ValueError):
            assign_group_manager(self.g1, self.u3)

    def test_manager_scope(self):
        mgr_user, _ = assign_group_manager(self.g1, self.u1, password="Secret123!")
        self.assertTrue(can_access_group(mgr_user, self.g1))
        self.assertFalse(can_access_group(mgr_user, self.g2))
        self.assertTrue(can_access_bot_user(mgr_user, self.u1))
        self.assertTrue(can_access_bot_user(mgr_user, self.u2))
        self.assertFalse(can_access_bot_user(mgr_user, self.u3))
        self.assertTrue(can_access_bot_user(self.admin, self.u3))

    def test_manager_login_menu_and_forbidden(self):
        mgr_user, _ = assign_group_manager(self.g1, self.u1, password="Secret123!")
        ok = self.client.login(username=mgr_user.username, password="Secret123!")
        self.assertTrue(ok)

        # Разрешённые разделы
        for name in (
            "panel:admin_tasks_today",
            "panel:users",
            "panel:services",
            "panel:services_groups",
            "panel:services_archive",
            "panel:services_wishes",
            "panel:services_ranking",
            "panel:clients_map",
        ):
            resp = self.client.get(reverse(name))
            self.assertEqual(resp.status_code, 200, msg=name)

        # Заявки жителей доступны менеджеру
        resp = self.client.get(reverse("panel:work_requests"))
        self.assertEqual(resp.status_code, 200)

        # Админ-only → редирект (в т.ч. исполнители)
        for name in (
            "panel:receipts",
            "panel:settings",
            "panel:earnings_forecast",
            "panel:contractors",
            "panel:executor_roles",
            "panel:manager_logs",
        ):
            resp = self.client.get(reverse(name))
            self.assertEqual(resp.status_code, 302, msg=name)

        # Чужой пользователь недоступен
        resp = self.client.get(reverse("panel:user_dashboard", args=[self.u3.id]))
        self.assertEqual(resp.status_code, 302)

        # Своя группа доступна, чужая — нет
        resp = self.client.get(reverse("panel:service_group_edit", args=[self.g1.id]))
        self.assertEqual(resp.status_code, 200)
        resp = self.client.get(reverse("panel:service_group_edit", args=[self.g2.id]))
        self.assertEqual(resp.status_code, 302)

    @patch("panel.views._notify_user")
    def test_admin_assigns_sends_creds_to_max(self, notify_mock):
        self.client.login(username="admin", password="adminpass")
        url = reverse("panel:service_group_edit", args=[self.g1.id])
        resp = self.client.post(
            url,
            {
                "action": "assign_manager",
                "manager_bot_user_id": str(self.u2.id),
                "manager_password": "MgrPass99",
            },
        )
        self.assertEqual(resp.status_code, 302)
        self.g1.refresh_from_db()
        self.assertIsNotNone(self.g1.manager_id)
        profile = PanelProfile.objects.get(user=self.g1.manager)
        self.assertEqual(profile.role, PanelRole.MANAGER)
        self.assertEqual(profile.bot_user_id, self.u2.id)
        self.assertTrue(
            self.client.login(username=self.g1.manager.username, password="MgrPass99")
        )

        notify_mock.assert_called_once()
        called_user, called_text = notify_mock.call_args[0]
        self.assertEqual(called_user.id, self.u2.id)
        expected = manager_credentials_max_message(
            username=self.g1.manager.username,
            password="MgrPass99",
            group_name=self.g1.name,
        )
        self.assertEqual(called_text, expected)
        self.assertIn("Логин:", called_text)
        self.assertIn("Пароль: MgrPass99", called_text)
        self.assertIn("URL:", called_text)
        self.assertIn("/panel/login/", called_text)
