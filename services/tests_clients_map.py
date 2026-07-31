from __future__ import annotations

from datetime import timedelta

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.utils import timezone

from database.models import BotUser, ServiceGroup
from services.clients_map import build_clients_map, build_households, household_root_id
from subscriptions.family import link_family_members


class ClientsMapTests(TestCase):
    def setUp(self) -> None:
        self.dmitry = BotUser.objects.create(
            max_user_id="map-dm",
            real_name="Дмитрий",
            subscription_until=timezone.now() + timedelta(days=60),
        )
        self.elena = BotUser.objects.create(
            max_user_id="map-el",
            real_name="Елена",
        )
        self.ivan = BotUser.objects.create(
            max_user_id="map-iv",
            real_name="Иван",
        )
        self.rocker = BotUser.objects.create(
            max_user_id="map-rk",
            real_name="Рокер",
            subscription_until=timezone.now() + timedelta(days=20),
        )
        self.alley = ServiceGroup.objects.create(name="9 аллея")
        self.rockers = ServiceGroup.objects.create(name="казанские рокеры")
        self.alley.members.add(self.elena, self.dmitry, self.ivan)
        self.rockers.members.add(self.rocker)
        link_family_members([self.elena, self.dmitry])
        self.admin = User.objects.create_user("mapadm", password="pass")
        self.client = Client()
        self.client.login(username="mapadm", password="pass")

    def test_household_merges_relatives(self):
        self.elena.refresh_from_db()
        self.dmitry.refresh_from_db()
        self.assertEqual(household_root_id(self.elena), self.dmitry.id)
        households = build_households([self.elena, self.dmitry, self.ivan])
        # Семья Дмитрий+Елена + Иван = 2 вершины
        self.assertEqual(len(households), 2)
        family = households[self.dmitry.id]
        self.assertTrue(family["has_family"])
        self.assertIn("Елена", family["label"])
        self.assertIn("Дмитрий", family["label"])

    def test_build_clients_map_family_vertices_no_hub(self):
        graphs = build_clients_map()
        alley = next(g for g in graphs if g["group_name"] == "9 аллея")
        # Елена+Дмитрий и Иван — две семейные вершины, без хаба группы
        self.assertEqual(alley["vertex_count"], 2)
        self.assertEqual(alley["payer_count"], 1)
        node_els = [e for e in alley["elements"] if "source" not in e["data"]]
        self.assertEqual(len(node_els), 2)
        self.assertTrue(all(e["data"]["kind"] == "household" for e in node_els))
        labels = " ".join(e["data"]["label"] for e in node_els)
        self.assertIn("Елена", labels)
        self.assertIn("Дмитрий", labels)
        self.assertIn("Иван", labels)

    def test_panel_page_renders_groups(self):
        import json
        import re

        resp = self.client.get("/panel/clients-map/")
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        self.assertIn("Карта клиентов", body)
        self.assertIn("семей", body)
        match = re.search(
            r'<script id="clients-map-data" type="application/json">(.*?)</script>',
            body,
            re.S,
        )
        self.assertIsNotNone(match)
        payload = json.loads(match.group(1))
        alley = next(g for g in payload if g["group_name"] == "9 аллея")
        nodes = [e for e in alley["elements"] if "source" not in e["data"]]
        self.assertEqual(len(nodes), 2)
        self.assertTrue(all(n["data"]["kind"] == "household" for n in nodes))
