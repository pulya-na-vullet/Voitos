from __future__ import annotations

from decimal import Decimal

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.utils import timezone

from database.models import (
    BotUser,
    CampaignStatus,
    ProfileStatus,
    ServiceCampaign,
    ServiceCategory,
    ServiceGroup,
)
from services.service import delete_service_campaign, launch_campaign_to_group


class CampaignDeleteTests(TestCase):
    def setUp(self) -> None:
        self.user = BotUser.objects.create(
            max_user_id="cd1",
            real_name="Анна",
            phone="89000000001",
            address="ул. А",
            locality="Казань",
            profile_status=ProfileStatus.VERIFIED,
        )
        self.group = ServiceGroup.objects.create(name="Группа А")
        self.group.members.add(self.user)
        self.admin = User.objects.create_user("adm", password="pass")
        self.client = Client()
        self.client.login(username="adm", password="pass")

    def test_delete_service_campaign(self):
        campaign, _ = launch_campaign_to_group(
            category=ServiceCategory.SNOW,
            title="Снег",
            description="",
            group=self.group,
            total_amount=Decimal("1000"),
            amount_per_user=Decimal("500"),
            event_at=timezone.now(),
        )
        pk = campaign.id
        label = delete_service_campaign(campaign, reason="Тест")
        self.assertIn("Снег", label)
        self.assertFalse(ServiceCampaign.objects.filter(pk=pk).exists())

    def test_panel_delete_from_services_home(self):
        campaign, _ = launch_campaign_to_group(
            category=ServiceCategory.ROAD,
            title="Дорога",
            description="",
            group=self.group,
            total_amount=Decimal("2000"),
            amount_per_user=Decimal("500"),
            event_at=timezone.now(),
        )
        resp = self.client.post(
            "/panel/services/",
            {
                "action": "delete_campaign",
                "campaign_id": str(campaign.id),
                "reason": "Ошибочно запущен",
            },
        )
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(ServiceCampaign.objects.filter(pk=campaign.id).exists())
