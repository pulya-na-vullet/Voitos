from __future__ import annotations

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.utils import timezone

from database.models import (
    BotUser,
    ContractorProfile,
    ContractorStatus,
    EquipmentType,
    PanelProfile,
    PanelRole,
)


class ContractorsLocalityBrowseTests(TestCase):
    def setUp(self) -> None:
        self.admin = User.objects.create_superuser("locadm", "l@t.com", "pass")
        PanelProfile.objects.create(user=self.admin, role=PanelRole.ADMIN)
        self.client = Client()
        self.client.login(username="locadm", password="pass")

        u1 = BotUser.objects.create(
            max_user_id="loc-1", real_name="Мастер Куюки", locality="Куюки"
        )
        u2 = BotUser.objects.create(
            max_user_id="loc-2", real_name="Мастер Казань", locality="Казань"
        )
        ContractorProfile.objects.create(
            user=u1,
            equipment_type=EquipmentType.TRACTOR,
            locality="Куюки",
            status=ContractorStatus.VERIFIED,
            verified_at=timezone.now(),
        )
        ContractorProfile.objects.create(
            user=u2,
            equipment_type=EquipmentType.TRUCK,
            locality="Казань",
            status=ContractorStatus.VERIFIED,
            verified_at=timezone.now(),
        )

    def test_index_shows_localities_not_masters(self):
        resp = self.client.get("/panel/contractors/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Населённые пункты")
        self.assertContains(resp, "Куюки")
        self.assertContains(resp, "Казань")
        self.assertNotContains(resp, "Мастер Куюки")
        self.assertNotContains(resp, "Мастер Казань")

    def test_locality_shows_only_its_masters(self):
        resp = self.client.get("/panel/contractors/", {"locality": "Куюки"})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Мастер Куюки")
        self.assertNotContains(resp, "Мастер Казань")
        self.assertContains(resp, "Все населённые пункты")
