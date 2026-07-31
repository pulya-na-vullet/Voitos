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
        self.alley = ServiceGroup.objects.create(name="9 аллея")
        self.rockers = ServiceGroup.objects.create(name="казанские рокеры")
        self.alley.members.add(self.elena, self.dmitry, self.ivan)
        self.rockers.members.add(
            BotUser.objects.create(max_user_id="map-rk", real_name="Рокер")
        )
        link_family_members([self.elena, self.dmitry])
        self.admin = User.objects.create_user("mapadm", password="pass")
        self.client = Client()
        self.client.login(username="mapadm", password="pass")

    def test_household_merges_relatives(self):
        self.elena.refresh_from_db()
        self.dmitry.refresh_from_db()
        self.assertEqual(household_root_id(self.elena), self.dmitry.id)
        households = build_households([self.elena, self.dmitry, self.ivan])
        self.assertEqual(len(households), 2)
        family = households[self.dmitry.id]
        self.assertTrue(family["has_family"])
        self.assertIn("Елена", family["label"])
        self.assertIn("Дмитрий", family["label"])

    def test_build_clients_map_one_graph_per_group(self):
        graphs = build_clients_map()
        names = [g["group_name"] for g in graphs]
        self.assertIn("9 аллея", names)
        self.assertIn("казанские рокеры", names)
        alley = next(g for g in graphs if g["group_name"] == "9 аллея")
        # Елена+Дмитрий = 1 вершина, Иван = 1 → 2 домохозяйства (+ hub не считается)
        self.assertEqual(alley["household_count"], 2)
        household_nodes = [n for n in alley["nodes"] if n["kind"] == "household"]
        family_node = next(n for n in household_nodes if n["has_family"])
        self.assertEqual(family_node["member_count"], 2)
        self.assertTrue(any("Елена" in lbl for lbl in family_node["labels"]))
        self.assertTrue(any("Дмитрий" in lbl for lbl in family_node["labels"]))
        # Cytoscape elements: hub + 2 households + edges
        node_els = [e for e in alley["elements"] if "source" not in e["data"]]
        self.assertEqual(len(node_els), 3)
        family_el = next(e for e in node_els if "family" in e.get("classes", ""))
        self.assertIn("Елена", family_el["data"]["label"])
        self.assertIn("Дмитрий", family_el["data"]["label"])

    def test_panel_page_renders_groups(self):
        import json
        import re

        resp = self.client.get("/panel/clients-map/")
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        self.assertIn("Карта клиентов", body)
        self.assertIn("cytoscape", body.lower())
        self.assertIn("9 аллея", body)
        self.assertIn("казанские рокеры", body)
        self.assertIn("clients-map-data", body)
        match = re.search(
            r'<script id="clients-map-data" type="application/json">(.*?)</script>',
            body,
            re.S,
        )
        self.assertIsNotNone(match)
        payload = json.loads(match.group(1))
        labels = " ".join(
            e["data"].get("label", "")
            for g in payload
            for e in g["elements"]
            if "source" not in e["data"]
        )
        self.assertIn("Елена", labels)
        self.assertIn("Дмитрий", labels)
