"""Tests for scalable bot user search (manager picker)."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from database.models import BotUser, PanelProfile, PanelRole, ServiceGroup
from panel.bot_user_search import search_bot_users
from panel.roles import assign_group_manager

User = get_user_model()


class BotUserSearchTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser("adm", "a@t.com", "pass")
        PanelProfile.objects.create(user=self.admin, role=PanelRole.ADMIN)
        self.u1 = BotUser.objects.create(
            max_user_id="s1",
            real_name="Анна Смирнова",
            phone="89001112233",
            locality="Куюки",
            chat_id="c1",
        )
        self.u2 = BotUser.objects.create(
            max_user_id="s2",
            real_name="Борис",
            phone="89004445566",
            locality="Казань",
            chat_id="c2",
        )
        self.client = Client()

    def test_search_by_name_and_phone(self):
        self.assertEqual(len(search_bot_users("Ан")), 1)
        self.assertEqual(search_bot_users("Ан")[0]["id"], self.u1.id)
        rows = search_bot_users("89004")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], self.u2.id)
        self.assertEqual(search_bot_users("а"), [])  # min 2 chars

    def test_search_endpoint_admin_only(self):
        url = reverse("panel:bot_users_search")
        resp = self.client.get(url, {"q": "Анна"})
        self.assertIn(resp.status_code, {302, 403})

        self.client.login(username="adm", password="pass")
        resp = self.client.get(url, {"q": "Анна"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["results"][0]["id"], self.u1.id)

    def test_assign_outsider_as_manager(self):
        g = ServiceGroup.objects.create(name="Двор X")
        g.members.add(self.u1)
        account, plain = assign_group_manager(g, self.u2, password="MgrOut99!")
        g.refresh_from_db()
        self.assertEqual(g.manager_id, account.id)
        self.assertEqual(account.panel_profile.bot_user_id, self.u2.id)
        self.assertTrue(plain)
