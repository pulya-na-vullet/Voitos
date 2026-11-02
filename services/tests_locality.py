"""Canonical settlement grouping for masters / call-a-master."""

from __future__ import annotations

from django.test import Client, TestCase
from django.utils import timezone

from api.models import MobileAuthToken
from database.models import (
    BotUser,
    ContractorProfile,
    ContractorStatus,
    ExecutorRole,
    ProfileStatus,
    ServiceGroup,
)
from services.locality import (
    canonicalize_locality,
    heuristic_settlement,
    localities_match,
    locality_bucket_label,
    settlement_for_client,
    settlement_for_group,
)
from services.master_booking import roles_for_client_call


class LocalityCanonicalizeTests(TestCase):
    def test_kuyuki_variants_collapse(self):
        self.assertEqual(heuristic_settlement("Куюки"), "Куюки")
        self.assertEqual(heuristic_settlement("куюки"), "Куюки")
        self.assertEqual(
            heuristic_settlement("Куюки, 24 квартал дом 1 строение 1"),
            "Куюки",
        )
        self.assertTrue(localities_match("Куюки", "куюки"))
        self.assertTrue(
            localities_match("Куюки", "Куюки, 24 квартал дом 1 строение 1")
        )
        self.assertEqual(
            locality_bucket_label("куюки"),
            locality_bucket_label("Куюки, 24 квартал дом 1 строение 1"),
        )

    def test_known_list_maps_address_without_name(self):
        name = heuristic_settlement(
            "24 квартал дом 1 строение 1, Куюки",
            known=["Куюки"],
        )
        self.assertEqual(name, "Куюки")

    def test_canonicalize_without_ai(self):
        self.assertEqual(
            canonicalize_locality(
                "Куюки, 24 квартал дом 1",
                use_ai=False,
                known=["Куюки"],
            ),
            "Куюки",
        )


class GroupSettlementAndCallRolesTests(TestCase):
    def setUp(self):
        self.role_tractor = ExecutorRole.objects.create(
            code="tractor", name="Тракторист", is_active=True
        )
        self.role_elec = ExecutorRole.objects.create(
            code="r_elec_g", name="Электрик", is_active=True, client_books_master=True
        )
        self.client = BotUser.objects.create(
            max_user_id="loc-cl",
            real_name="Житель",
            locality="24 квартал дом 1 строение 1",
            address="Куюки, 24 квартал дом 1 строение 1",
            profile_status=ProfileStatus.VERIFIED,
        )
        self.t_user = BotUser.objects.create(
            max_user_id="loc-tr",
            real_name="Тракторист Иван",
            locality="Куюки",
            profile_status=ProfileStatus.VERIFIED,
        )
        self.e_user = BotUser.objects.create(
            max_user_id="loc-el",
            real_name="Электрик Пётр",
            locality="куюки",
            profile_status=ProfileStatus.VERIFIED,
        )
        ContractorProfile.objects.create(
            user=self.t_user,
            role=self.role_tractor,
            equipment_type=self.role_tractor.code,
            locality="Куюки",
            status=ContractorStatus.VERIFIED,
            verified_at=timezone.now(),
        )
        ContractorProfile.objects.create(
            user=self.e_user,
            role=self.role_elec,
            equipment_type=self.role_elec.code,
            locality="Куюки, 24 квартал дом 1 строение 1",
            status=ContractorStatus.VERIFIED,
            verified_at=timezone.now(),
        )
        self.group = ServiceGroup.objects.create(name="Куюки двор")
        self.group.members.add(self.client, self.t_user, self.e_user)
        self.tok = MobileAuthToken.objects.create(bot_user=self.client)
        self.auth = {"HTTP_AUTHORIZATION": f"Bearer {self.tok.token}"}
        self.http = Client()

    def test_group_settlement_is_kuyuki(self):
        self.assertEqual(settlement_for_group(self.group), "Куюки")

    def test_call_roles_use_group_id(self):
        loc = settlement_for_client(self.client, group_id=self.group.id)
        self.assertEqual(loc, "Куюки")
        codes = {r.code for r in roles_for_client_call(self.client, locality=loc)}
        self.assertIn("tractor", codes)
        self.assertIn("r_elec_g", codes)

        api = self.http.get(
            f"/api/v1/executor-roles?for=call&group_id={self.group.id}",
            **self.auth,
        )
        self.assertEqual(api.status_code, 200)
        body = api.json()
        self.assertEqual(body.get("locality"), "Куюки")
        got = {r["code"] for r in body["items"]}
        self.assertIn("tractor", got)
        self.assertIn("r_elec_g", got)

    def test_group_name_beats_member_profiles(self):
        """Чат «Чебоксары» — мастера Чебоксар, даже если жители прописаны в Куюках."""
        other_role = ExecutorRole.objects.create(
            code="r_nails_ch", name="Маникюр", is_active=True, client_books_master=True
        )
        ch_user = BotUser.objects.create(
            max_user_id="loc-ch",
            real_name="Мастер Чебоксары",
            locality="Чебоксары",
            profile_status=ProfileStatus.VERIFIED,
        )
        ContractorProfile.objects.create(
            user=ch_user,
            role=other_role,
            equipment_type=other_role.code,
            locality="Чебоксары",
            status=ContractorStatus.VERIFIED,
            verified_at=timezone.now(),
        )
        g = ServiceGroup.objects.create(name="Чебоксары", locality="Чебоксары")
        g.members.add(self.client, self.t_user, ch_user)
        self.assertEqual(settlement_for_group(g), "Чебоксары")
        loc = settlement_for_client(self.client, group_id=g.id)
        self.assertEqual(loc, "Чебоксары")
        codes = {r.code for r in roles_for_client_call(self.client, locality=loc)}
        self.assertIn("r_nails_ch", codes)
        self.assertNotIn("tractor", codes)

        api = self.http.get(
            f"/api/v1/executor-roles?for=call&group_id={g.id}",
            **self.auth,
        )
        self.assertEqual(api.status_code, 200)
        body = api.json()
        self.assertEqual(body.get("locality"), "Чебоксары")
        got = {r["code"] for r in body["items"]}
        self.assertIn("r_nails_ch", got)
        self.assertNotIn("tractor", got)
        loc = settlement_for_client(self.client)
        self.assertEqual(loc, "Куюки")

    def test_courtyard_group_uses_residents_settlement(self):
        """«ул. А» / «Двор» — не город; мастера Куюков должны быть видны."""
        g = ServiceGroup.objects.create(name="ул. А")
        g.members.add(self.client, self.t_user, self.e_user)
        self.assertEqual(settlement_for_group(g), "Куюки")
        loc = settlement_for_client(self.client, group_id=g.id)
        self.assertEqual(loc, "Куюки")
        codes = {r.code for r in roles_for_client_call(self.client, locality=loc)}
        self.assertIn("tractor", codes)
        api = self.http.get(
            f"/api/v1/executor-roles?for=call&group_id={g.id}",
            **self.auth,
        )
        self.assertEqual(api.status_code, 200)
        body = api.json()
        self.assertEqual(body.get("locality"), "Куюки")
        self.assertIn("tractor", {r["code"] for r in body["items"]})

    def test_explicit_group_locality_overrides_members(self):
        g = ServiceGroup.objects.create(name="двор 9", locality="Чебоксары")
        g.members.add(self.client, self.t_user)
        self.assertEqual(settlement_for_group(g), "Чебоксары")
        loc = settlement_for_client(self.client, group_id=g.id)
        self.assertEqual(loc, "Чебоксары")
        codes = {r.code for r in roles_for_client_call(self.client, locality=loc)}
        self.assertNotIn("tractor", codes)

    def test_city_group_without_masters_is_empty(self):
        g = ServiceGroup.objects.create(name="Чебоксары", locality="Чебоксары")
        g.members.add(self.client, self.t_user)
        self.assertEqual(settlement_for_group(g), "Чебоксары")
        loc = settlement_for_client(self.client, group_id=g.id)
        self.assertEqual(loc, "Чебоксары")
        codes = {r.code for r in roles_for_client_call(self.client, locality=loc)}
        self.assertEqual(codes, set())
        api = self.http.get(
            f"/api/v1/executor-roles?for=call&group_id={g.id}",
            **self.auth,
        )
        self.assertEqual(api.status_code, 200)
        body = api.json()
        self.assertEqual(body.get("locality"), "Чебоксары")
        self.assertEqual(body.get("items"), [])


class PanelMastersMergeTests(TestCase):
    def setUp(self):
        from django.contrib.auth.models import User

        from database.models import PanelProfile, PanelRole

        self.admin = User.objects.create_superuser("locadm2", "l2@t.com", "pass")
        PanelProfile.objects.create(user=self.admin, role=PanelRole.ADMIN)
        self.client = Client()
        self.client.login(username="locadm2", password="pass")
        role = ExecutorRole.objects.create(code="tractor", name="Тракторист", is_active=True)
        for i, loc in enumerate(
            ("Куюки", "куюки", "Куюки, 24 квартал дом 1 строение 1")
        ):
            u = BotUser.objects.create(
                max_user_id=f"mrg-{i}", real_name=f"Мастер {i}", locality=loc
            )
            ContractorProfile.objects.create(
                user=u,
                equipment_type=role.code,
                role=role,
                locality=loc,
                status=ContractorStatus.VERIFIED,
                verified_at=timezone.now(),
            )

    def test_index_merges_kuyuki_cards(self):
        resp = self.client.get("/panel/contractors/")
        self.assertEqual(resp.status_code, 200)
        locs = resp.context["localities"]
        self.assertEqual(len(locs), 1)
        self.assertEqual(locs[0]["name"], "Куюки")
        self.assertEqual(locs[0]["count"], 3)
        self.assertNotContains(resp, "24 квартал")

    def test_filter_canonical_shows_all_three(self):
        resp = self.client.get("/panel/contractors/", {"locality": "Куюки"})
        self.assertContains(resp, "Мастер 0")
        self.assertContains(resp, "Мастер 1")
        self.assertContains(resp, "Мастер 2")
        self.assertContains(resp, "Нас. пункт")
        self.assertContains(resp, "Куюки")


class PanelGroupLocalityTests(TestCase):
    def setUp(self):
        from django.contrib.auth.models import User

        from database.models import PanelProfile, PanelRole

        self.admin = User.objects.create_superuser("grpadm", "g@t.com", "pass")
        PanelProfile.objects.create(user=self.admin, role=PanelRole.ADMIN)
        self.client = Client()
        self.client.login(username="grpadm", password="pass")

    def test_create_group_with_locality(self):
        resp = self.client.post(
            "/panel/services/groups/",
            {
                "action": "create_group",
                "name": "ул. Баумана",
                "locality": "куюки",
                "description": "",
            },
        )
        self.assertEqual(resp.status_code, 302)
        g = ServiceGroup.objects.get(name="ул. Баумана")
        self.assertEqual(g.locality, "Куюки")

    def test_list_shows_settlement_column(self):
        ServiceGroup.objects.create(name="Двор", locality="Куюки")
        resp = self.client.get("/panel/services/groups/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Нас. пункт")
        self.assertContains(resp, "Куюки")
        self.assertContains(resp, "Двор")

    def test_edit_saves_locality(self):
        g = ServiceGroup.objects.create(name="Двор")
        resp = self.client.post(
            f"/panel/services/groups/{g.id}/",
            {"name": "Двор", "description": "", "locality": "Чебоксары"},
        )
        self.assertEqual(resp.status_code, 302)
        g.refresh_from_db()
        self.assertEqual(g.locality, "Чебоксары")
