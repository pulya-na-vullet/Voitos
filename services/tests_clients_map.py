from __future__ import annotations

from datetime import timedelta

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.utils import timezone

from database.models import BotUser, PanelProfile, PanelRole, ServiceGroup
from panel.roles import assign_group_manager
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
        self.admin = User.objects.create_superuser("mapadm", "m@t.com", "pass")
        PanelProfile.objects.create(user=self.admin, role=PanelRole.ADMIN)
        self.client = Client()
        self.client.login(username="mapadm", password="pass")

    def test_manager_node_marked_panel_role(self):
        assign_group_manager(self.alley, self.ivan, password="MgrMap99!")
        graphs = build_clients_map()
        alley = next(g for g in graphs if g["group_name"] == "9 аллея")
        nodes = [e for e in alley["elements"] if "source" not in e["data"]]
        ivan_node = next(n for n in nodes if n["data"]["label"] == "Иван")
        self.assertIn("panel-role", ivan_node["classes"])
        self.assertTrue(ivan_node["data"]["is_panel_role"])
        self.assertGreaterEqual(alley["panel_role_count"], 1)

    def test_household_merges_relatives(self):
        self.elena.refresh_from_db()
        self.dmitry.refresh_from_db()
        self.assertEqual(household_root_id(self.elena), self.dmitry.id)
        households = build_households([self.elena, self.dmitry, self.ivan])
        # Семья Дмитрий+Елена + Иван = 2 домохозяйства
        self.assertEqual(len(households), 2)
        family = households[self.dmitry.id]
        self.assertTrue(family["has_family"])
        self.assertIn("Елена", family["label"])
        self.assertIn("Дмитрий", family["label"])

    def test_family_spokes_and_root_ring(self):
        # Без активной подписки грани между корнями всё равно есть.
        maria = BotUser.objects.create(
            max_user_id="map-mr",
            real_name="Мария",
        )
        self.alley.members.add(maria)
        self.dmitry.subscription_until = None
        self.dmitry.save(update_fields=["subscription_until"])

        graphs = build_clients_map()
        alley = next(g for g in graphs if g["group_name"] == "9 аллея")
        # Дмитрий, Елена, Иван, Мария — по одной вершине на человека
        self.assertEqual(alley["vertex_count"], 4)
        self.assertEqual(alley["user_count"], 4)
        self.assertEqual(alley["payer_count"], 0)
        self.assertEqual(alley["household_count"], 3)

        node_els = [e for e in alley["elements"] if "source" not in e["data"]]
        self.assertEqual(len(node_els), 4)
        self.assertTrue(all(e["data"]["kind"] == "person" for e in node_els))
        by_label = {e["data"]["label"]: e for e in node_els}
        self.assertIn("Елена", by_label)
        self.assertIn("Дмитрий", by_label)
        self.assertIn("dependent", by_label["Елена"]["classes"])
        self.assertIn("family-parent", by_label["Дмитрий"]["classes"])

        edges = [e for e in alley["elements"] if "source" in e["data"]]
        family_edges = [e for e in edges if e["data"]["kind"] == "family"]
        ring_edges = [e for e in edges if e["data"]["kind"] == "ring"]
        self.assertEqual(len(family_edges), 1)
        self.assertEqual(family_edges[0]["data"]["source"], f"u{self.elena.id}")
        self.assertEqual(family_edges[0]["data"]["target"], f"u{self.dmitry.id}")
        # Кольцо между корневыми (Дмитрий, Иван, Мария); Елена — только спица
        self.assertEqual(len(ring_edges), 3)
        ring_nodes = set()
        for edge in ring_edges:
            ring_nodes.add(edge["data"]["source"])
            ring_nodes.add(edge["data"]["target"])
        self.assertEqual(
            ring_nodes,
            {f"u{self.dmitry.id}", f"u{self.ivan.id}", f"u{maria.id}"},
        )
        self.assertNotIn(f"u{self.elena.id}", ring_nodes)

    def test_four_dependents_attach_to_payer(self):
        kids = []
        for i in range(4):
            kid = BotUser.objects.create(
                max_user_id=f"map-kid-{i}",
                real_name=f"Ребёнок{i}",
            )
            kids.append(kid)
            self.alley.members.add(kid)
        link_family_members([self.dmitry, self.elena, *kids])
        graphs = build_clients_map()
        alley = next(g for g in graphs if g["group_name"] == "9 аллея")
        family_edges = [
            e for e in alley["elements"]
            if e.get("data", {}).get("kind") == "family"
        ]
        self.assertEqual(len(family_edges), 5)  # Елена + 4 ребёнка → Дмитрий
        for edge in family_edges:
            self.assertEqual(edge["data"]["target"], f"u{self.dmitry.id}")
        self.assertIn("family-parent", next(
            e["classes"] for e in alley["elements"]
            if e["data"].get("label") == "Дмитрий"
        ))

    def test_dependents_stay_in_own_group_only(self):
        """Иждивенцы не должны появляться в чужих группах плательщика."""
        other = ServiceGroup.objects.create(name="другая группа")
        other.members.add(self.dmitry)  # плательщик в двух группах
        # Елена только в «9 аллея»
        graphs = build_clients_map()
        alley = next(g for g in graphs if g["group_name"] == "9 аллея")
        other_g = next(g for g in graphs if g["group_name"] == "другая группа")

        alley_labels = {
            e["data"]["label"]
            for e in alley["elements"]
            if "source" not in e["data"]
        }
        other_labels = {
            e["data"]["label"]
            for e in other_g["elements"]
            if "source" not in e["data"]
        }
        self.assertIn("Елена", alley_labels)
        self.assertIn("Дмитрий", alley_labels)
        self.assertIn("Дмитрий", other_labels)
        self.assertNotIn("Елена", other_labels)
        other_family = [
            e
            for e in other_g["elements"]
            if e.get("data", {}).get("kind") == "family"
        ]
        self.assertEqual(other_family, [])

    def test_panel_page_renders_groups(self):
        import json
        import re

        resp = self.client.get("/panel/clients-map/")
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        self.assertIn("Карта клиентов", body)
        self.assertIn("связаны между собой", body)
        match = re.search(
            r'<script id="clients-map-data" type="application/json">(.*?)</script>',
            body,
            re.S,
        )
        self.assertIsNotNone(match)
        payload = json.loads(match.group(1))
        alley = next(g for g in payload if g["group_name"] == "9 аллея")
        nodes = [e for e in alley["elements"] if "source" not in e["data"]]
        self.assertEqual(len(nodes), 3)
        self.assertTrue(all(n["data"]["kind"] == "person" for n in nodes))
        edges = [e for e in alley["elements"] if "source" in e["data"]]
        self.assertTrue(any(e["data"]["kind"] == "family" for e in edges))
