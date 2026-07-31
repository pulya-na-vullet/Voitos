from __future__ import annotations

from decimal import Decimal

from django.test import TestCase

from database.models import (
    BotUser,
    CampaignStatus,
    InviteStatus,
    ProfileStatus,
    ServiceCategory,
)
from bot.pipeline import MessagePipeline
from bot.registration import handle_registration_step, start_registration
from database.models import PendingAction
from services.ranking import citizen_stats, rank_label
from services.service import create_campaign, format_collections_for_user, offer_to_users


class RegistrationTests(TestCase):
    def setUp(self) -> None:
        self.user = BotUser.objects.create(max_user_id="u1")
        self.pending, _ = PendingAction.objects.get_or_create(user=self.user)
        self.pipeline = MessagePipeline()

    def test_registration_flow(self):
        msg = start_registration(self.user, self.pending)
        self.assertIn("зовут", msg.lower())
        handle_registration_step(self.user, "Иван Петров", self.pending)
        handle_registration_step(self.user, "89625501111", self.pending)
        reply = handle_registration_step(
            self.user, "Казань, ул. Баумана, 1", self.pending
        )
        self.user.refresh_from_db()
        self.assertEqual(self.user.real_name, "Иван Петров")
        self.assertEqual(self.user.phone, "89625501111")
        self.assertEqual(self.user.locality, "Казань")
        self.assertEqual(self.user.profile_status, ProfileStatus.PENDING_REVIEW)
        self.assertIn("Администратор проверит", reply)


class ServiceCampaignTests(TestCase):
    def setUp(self) -> None:
        self.user = BotUser.objects.create(
            max_user_id="u2",
            real_name="Анна",
            phone="89000000000",
            address="Казань, ул. А, 2",
            locality="Казань",
            profile_status=ProfileStatus.VERIFIED,
        )

    def test_create_and_offer(self):
        campaign = create_campaign(
            category=ServiceCategory.SNOW,
            title="Чистка снега",
            description="ул. А",
            locality="Казань",
            total_amount=Decimal("10000"),
        )
        self.assertIn("от ", campaign.title)
        sent = offer_to_users(campaign, [self.user.id], Decimal("500"))
        self.assertEqual(sent, 1)
        campaign.refresh_from_db()
        self.assertEqual(campaign.status, CampaignStatus.ACTIVE)
        inv = campaign.invites.get(user=self.user)
        self.assertEqual(inv.amount_due, Decimal("500"))
        self.assertEqual(inv.status, InviteStatus.OFFERED)

        text = format_collections_for_user(self.user)
        self.assertIn("Чистка снега", text)
        self.assertIn("500", text)
        self.assertIn("[", text)

    def test_rank_labels(self):
        self.assertEqual(rank_label(85), "Образцовый гражданин")
        self.assertEqual(rank_label(60), "Хороший гражданин")
        self.assertEqual(rank_label(40), "Пассивный гражданин")
        self.assertEqual(rank_label(10), "Неактивный гражданин")

    def test_citizen_stats(self):
        campaign = create_campaign(
            category=ServiceCategory.ROAD,
            title="Ремонт",
            description="",
            locality="Казань",
            total_amount=Decimal("5000"),
        )
        offer_to_users(campaign, [self.user.id], Decimal("100"))
        inv = campaign.invites.get()
        inv.status = InviteStatus.PAID
        inv.amount_paid = Decimal("100")
        inv.save()
        stats = citizen_stats(self.user)
        self.assertEqual(stats.offered, 1)
        self.assertEqual(stats.paid, 1)
        self.assertEqual(stats.label, "Образцовый гражданин")


class ServiceIntentTests(TestCase):
    def test_collections_command(self):
        from ai.intent import IntentAnalyzer

        r = IntentAnalyzer().analyze("сборы")
        self.assertEqual(r.intent, "service_collections")
