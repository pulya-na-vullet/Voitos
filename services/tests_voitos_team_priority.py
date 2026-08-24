"""Приоритет команды Voitos при автоподборе заявок и сборов."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from database.models import (
    AssignmentStatus,
    BotUser,
    CampaignAssignment,
    CampaignStatus,
    ContractorProfile,
    ContractorStatus,
    ExecutorRole,
    ServiceCampaign,
    ServiceCategory,
    ServiceGroup,
    WorkRequest,
    WorkRequestStatus,
)
from services.dispatch_priority import (
    campaign_work_address,
    maps_route_urls,
    prioritize_dispatch_candidates,
)
from services.work_request_dispatch import try_dispatch_request


class VoitosTeamPriorityDispatchTests(TestCase):
    def setUp(self):
        self.role = ExecutorRole.objects.create(
            code="computer_master",
            name="Компьютерный мастер",
            is_active=True,
            client_books_master=False,
        )
        self.client_user = BotUser.objects.create(
            max_user_id="c-prio",
            real_name="Житель",
            locality="Куюки",
            phone="89001110001",
        )
        self.team_user = BotUser.objects.create(
            max_user_id="e-team",
            real_name="Команда",
            locality="Куюки",
            phone="89001110002",
        )
        self.other_user = BotUser.objects.create(
            max_user_id="e-other",
            real_name="Внешний",
            locality="Куюки",
            phone="89001110003",
        )
        self.team = ContractorProfile.objects.create(
            user=self.team_user,
            role=self.role,
            equipment_type="computer_master",
            locality="Куюки",
            status=ContractorStatus.VERIFIED,
            is_voitos_team=True,
        )
        self.other = ContractorProfile.objects.create(
            user=self.other_user,
            role=self.role,
            equipment_type="computer_master",
            locality="Куюки",
            status=ContractorStatus.VERIFIED,
            is_voitos_team=False,
        )

    def test_prioritize_prefers_voitos_team_with_capacity(self):
        cands = [
            (self.other, 1.0, "np"),
            (self.team, 1.0, "np"),
        ]
        with patch(
            "services.dispatch_priority.contractor_has_capacity_today",
            return_value=True,
        ):
            ranked = prioritize_dispatch_candidates(cands)
        self.assertEqual(ranked[0][0].id, self.team.id)

    def test_prioritize_falls_to_low_when_team_busy(self):
        cands = [
            (self.other, 1.0, "np"),
            (self.team, 1.0, "np"),
        ]

        def _cap(c):
            return c.id != self.team.id

        with patch(
            "services.dispatch_priority.contractor_has_capacity_today",
            side_effect=_cap,
        ):
            ranked = prioritize_dispatch_candidates(cands)
        self.assertEqual([c.id for c, _, _ in ranked], [self.other.id])

    def test_dispatch_offers_team_first(self):
        req = WorkRequest.objects.create(
            user=self.client_user,
            role=self.role,
            description="Починить ПК",
            client_locality="Куюки",
            status=WorkRequestStatus.PENDING,
        )
        msgs = []

        def capture(user, text):
            msgs.append((user.max_user_id, text))

        with patch(
            "services.dispatch_priority.contractor_has_capacity_today",
            return_value=True,
        ):
            offer = try_dispatch_request(req, send_fn=capture, use_ai=False)
        self.assertIsNotNone(offer)
        self.assertEqual(offer.contractor_id, self.team.id)


class CampaignAutoOfferTests(TestCase):
    def setUp(self):
        self.group = ServiceGroup.objects.create(name="9 аллея")
        self.campaign = ServiceCampaign.objects.create(
            category=ServiceCategory.SNOW,
            title="Чистка снега",
            locality="Куюки",
            group=self.group,
            total_amount=Decimal("1000"),
            amount_per_user=Decimal("100"),
            status=CampaignStatus.ACTIVE,
            event_at=timezone.now() + timedelta(days=1),
            needs_snow_haul=False,
        )
        self.role = ExecutorRole.objects.create(
            code="tractor",
            name="Тракторист",
            is_active=True,
            for_snow=True,
            client_books_master=False,
        )
        self.team_user = BotUser.objects.create(
            max_user_id="tr-team", real_name="Трактор Voitos", locality="Куюки"
        )
        self.low_user = BotUser.objects.create(
            max_user_id="tr-low", real_name="Трактор внешний", locality="Куюки"
        )
        self.team = ContractorProfile.objects.create(
            user=self.team_user,
            role=self.role,
            equipment_type="tractor",
            locality="Куюки",
            status=ContractorStatus.VERIFIED,
            is_voitos_team=True,
            plate_number="A111AA",
        )
        self.low = ContractorProfile.objects.create(
            user=self.low_user,
            role=self.role,
            equipment_type="tractor",
            locality="Куюки",
            status=ContractorStatus.VERIFIED,
            is_voitos_team=False,
        )

    def test_work_address_uses_group_name(self):
        addr = campaign_work_address(self.campaign)
        self.assertIn("9 аллея", addr)
        maps = maps_route_urls(addr)
        self.assertIn("yandex.ru/maps", maps["yandex_maps_url"])
        self.assertIn("2gis.ru", maps["dgis_maps_url"])

    def test_auto_offer_prefers_voitos_team(self):
        from services.contractors import auto_offer_priority_contractors_for_campaign

        with patch(
            "services.dispatch_priority.contractor_has_capacity_today",
            return_value=True,
        ):
            n = auto_offer_priority_contractors_for_campaign(self.campaign, send_fn=None)
        self.assertEqual(n, 1)
        a = CampaignAssignment.objects.get(campaign=self.campaign)
        self.assertEqual(a.contractor_id, self.team.id)
        self.assertEqual(a.status, AssignmentStatus.OFFERED)

    def test_auto_offer_falls_to_low_when_team_full(self):
        from services.contractors import auto_offer_priority_contractors_for_campaign

        def _cap(c):
            return c.id != self.team.id

        with patch(
            "services.dispatch_priority.contractor_has_capacity_today",
            side_effect=_cap,
        ):
            n = auto_offer_priority_contractors_for_campaign(self.campaign, send_fn=None)
        self.assertEqual(n, 1)
        a = CampaignAssignment.objects.get(campaign=self.campaign)
        self.assertEqual(a.contractor_id, self.low.id)
