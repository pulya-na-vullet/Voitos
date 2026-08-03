from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.utils import timezone

from database.models import (
    AssignmentStatus,
    BotUser,
    CampaignAssignment,
    CampaignResidentHelper,
    CampaignStatus,
    ContractorPayout,
    ContractorProfile,
    ContractorStatus,
    EquipmentType,
    ResidentHelperStatus,
    ServiceCategory,
    ServiceGroup,
    WorkStage,
)
from services.contractors import suggested_equipment_for_campaign
from services.resident_helpers import assign_resident_helper, group_members_for_helper_pick
from services.service import create_campaign


class ResidentHelperTests(TestCase):
    def setUp(self) -> None:
        self.admin = User.objects.create_user("helpadm", password="pass")
        self.client = Client()
        self.client.login(username="helpadm", password="pass")
        self.a = BotUser.objects.create(max_user_id="rh-a", real_name="Анна", phone="89001110001")
        self.b = BotUser.objects.create(max_user_id="rh-b", real_name="Борис", phone="89001110002")
        self.group = ServiceGroup.objects.create(name="Двор 1")
        self.group.members.add(self.a, self.b)
        self.campaign = create_campaign(
            category=ServiceCategory.PLAYGROUND,
            title="Домик",
            group=self.group,
            total_amount=Decimal("1000"),
            amount_per_user=Decimal("500"),
            event_at=timezone.now() + timedelta(days=2),
        )
        self.campaign.status = CampaignStatus.ACTIVE
        self.campaign.save(update_fields=["status"])
        self.sent: list[tuple[int, str]] = []

        def capture(user, text):
            self.sent.append((user.id, text))

        self.capture = capture

    def test_playground_has_no_equipment_suggestion(self):
        self.assertEqual(suggested_equipment_for_campaign(self.campaign), [])

    def test_assign_from_group_and_peer_contacts(self):
        pick = group_members_for_helper_pick(self.campaign)
        self.assertEqual({u.id for u in pick}, {self.a.id, self.b.id})
        assign_resident_helper(self.campaign, self.a, send_fn=self.capture)
        assign_resident_helper(self.campaign, self.b, send_fn=self.capture)
        self.assertEqual(
            CampaignResidentHelper.objects.filter(
                campaign=self.campaign, status=ResidentHelperStatus.ASSIGNED
            ).count(),
            2,
        )
        self.assertTrue(any("назначили исполнителем" in t.lower() for _, t in self.sent))
        self.assertTrue(any("Контакты коллег" in t for _, t in self.sent))
        self.assertTrue(any("89001110002" in t for uid, t in self.sent if uid == self.a.id))

    def test_panel_dropdown_and_assign(self):
        resp = self.client.get(f"/panel/services/campaigns/{self.campaign.id}/")
        body = resp.content.decode()
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Назначить жителя группы", body)
        self.assertIn(str(self.a.id), body)
        self.assertNotIn("Отправить заказ", body)
        resp = self.client.post(
            f"/panel/services/campaigns/{self.campaign.id}/",
            {"action": "assign_resident_helper", "user_id": self.a.id},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(
            CampaignResidentHelper.objects.filter(
                campaign=self.campaign, user=self.a, status=ResidentHelperStatus.ASSIGNED
            ).exists()
        )

    def test_outsider_rejected(self):
        outsider = BotUser.objects.create(max_user_id="rh-x", real_name="Чужой")
        with self.assertRaises(ValueError):
            assign_resident_helper(self.campaign, outsider, send_fn=self.capture)


class ContractorEarningsTests(TestCase):
    def setUp(self) -> None:
        self.admin = User.objects.create_user("earnadm", password="pass")
        self.client = Client()
        self.client.login(username="earnadm", password="pass")
        self.driver = BotUser.objects.create(max_user_id="earn-1", real_name="Иван")
        self.profile = ContractorProfile.objects.create(
            user=self.driver,
            equipment_type=EquipmentType.TRACTOR,
            status=ContractorStatus.VERIFIED,
            verified_at=timezone.now(),
            bank_name="Сбер",
            payout_phone="89001112233",
        )
        group = ServiceGroup.objects.create(name="Улица")
        campaign = create_campaign(
            category=ServiceCategory.SNOW,
            title="Снег",
            group=group,
            total_amount=Decimal("3000"),
            amount_per_user=Decimal("300"),
            event_at=timezone.now() + timedelta(days=1),
        )
        campaign.status = CampaignStatus.CLOSED
        campaign.work_stage = WorkStage.WORK_CLOSED
        campaign.save()
        assignment = CampaignAssignment.objects.create(
            campaign=campaign,
            contractor=self.profile,
            equipment_type=EquipmentType.TRACTOR,
            status=AssignmentStatus.ACCEPTED,
            scheduled_at=campaign.event_at,
            accepted_at=timezone.now(),
        )
        img = SimpleUploadedFile("p.png", b"fake", content_type="image/png")
        ContractorPayout.objects.create(
            campaign=campaign,
            assignment=assignment,
            contractor=self.profile,
            amount=Decimal("1500"),
            receipt_image=img,
            bank_name="Сбер",
            payout_phone="89001112233",
        )

    def test_contractors_page_shows_total_earned(self):
        resp = self.client.get("/panel/contractors/")
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        self.assertIn("Заработано", body)
        self.assertIn("1500", body)
