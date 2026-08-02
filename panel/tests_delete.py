from __future__ import annotations

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.utils import timezone

from database.models import (
    BotUser,
    ContractorProfile,
    ContractorStatus,
    EquipmentType,
    ServiceGroup,
)


class PanelDeleteTests(TestCase):
    def setUp(self) -> None:
        self.admin = User.objects.create_user("deladm", password="pass")
        self.client = Client()
        self.client.login(username="deladm", password="pass")

    def test_delete_user(self):
        u = BotUser.objects.create(max_user_id="del-u", real_name="Тест")
        resp = self.client.post("/panel/", {"action": "delete_user", "user_id": u.id})
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(BotUser.objects.filter(pk=u.id).exists())

    def test_delete_users_bulk(self):
        a = BotUser.objects.create(max_user_id="del-a", real_name="А")
        b = BotUser.objects.create(max_user_id="del-b", real_name="Б")
        resp = self.client.post(
            "/panel/",
            {"action": "delete_users_bulk", "user_ids": [a.id, b.id]},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(BotUser.objects.filter(id__in=[a.id, b.id]).count(), 0)

    def test_delete_contractor(self):
        user = BotUser.objects.create(max_user_id="del-c", real_name="Водитель")
        profile = ContractorProfile.objects.create(
            user=user,
            equipment_type=EquipmentType.TRACTOR,
            status=ContractorStatus.VERIFIED,
            verified_at=timezone.now(),
        )
        resp = self.client.post(
            "/panel/contractors/",
            {"action": "delete", "contractor_id": profile.id},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(ContractorProfile.objects.filter(pk=profile.id).exists())
        self.assertTrue(BotUser.objects.filter(pk=user.id).exists())

    def test_delete_group_from_list(self):
        g = ServiceGroup.objects.create(name="Тест-группа")
        resp = self.client.post(
            "/panel/services/groups/",
            {"action": "delete_group", "group_id": g.id},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(ServiceGroup.objects.filter(pk=g.id).exists())
