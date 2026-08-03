from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.utils import timezone

from database.models import (
    AppSettings,
    BotUser,
    CampaignStatus,
    InviteStatus,
    PaymentReceipt,
    ProfileStatus,
    ReceiptStatus,
    ServiceCampaign,
    ServiceCategory,
    ServiceGroup,
    ServiceInvite,
    ServiceReceipt,
)
from services.tax import compute_self_employed_collected, sync_self_employed_tax_collected
from subscriptions.service import approve_receipt


class SelfEmployedTaxSyncTests(TestCase):
    def setUp(self) -> None:
        self.user = BotUser.objects.create(
            max_user_id="tax1",
            real_name="Иван",
            locality="Казань",
            profile_status=ProfileStatus.VERIFIED,
        )
        self.group = ServiceGroup.objects.create(name="ул. А")
        self.group.members.add(self.user)
        self.campaign = ServiceCampaign.objects.create(
            category=ServiceCategory.SNOW,
            title="Снег",
            group=self.group,
            total_amount=Decimal("1000"),
            status=CampaignStatus.ACTIVE,
            event_at=timezone.now() + timedelta(days=3),
        )
        self.invite = ServiceInvite.objects.create(
            campaign=self.campaign,
            user=self.user,
            amount_due=Decimal("500"),
            status=InviteStatus.OFFERED,
        )
        cfg = AppSettings.load()
        cfg.service_tax_collected = Decimal("0")
        cfg.save(update_fields=["service_tax_collected"])

    def test_compute_sums_subscription_and_service(self):
        PaymentReceipt.objects.create(
            user=self.user,
            amount=Decimal("200"),
            status=ReceiptStatus.APPROVED,
            reviewed_at=timezone.now(),
        )
        ServiceReceipt.objects.create(
            invite=self.invite,
            campaign=self.campaign,
            user=self.user,
            amount=Decimal("500"),
            status=ReceiptStatus.APPROVED,
            reviewed_at=timezone.now(),
        )
        stats = compute_self_employed_collected()
        self.assertEqual(stats["subscriptions"], Decimal("200.00"))
        self.assertEqual(stats["services"], Decimal("500.00"))
        self.assertEqual(stats["total"], Decimal("700.00"))

    def test_sync_updates_settings_field(self):
        PaymentReceipt.objects.create(
            user=self.user,
            amount=Decimal("100"),
            status=ReceiptStatus.APPROVED,
            reviewed_at=timezone.now(),
        )
        stats = sync_self_employed_tax_collected()
        cfg = AppSettings.load()
        self.assertEqual(stats["total"], Decimal("100.00"))
        self.assertEqual(cfg.service_tax_collected, Decimal("100.00"))

    def test_approve_subscription_updates_tax(self):
        receipt = PaymentReceipt.objects.create(
            user=self.user,
            amount=Decimal("100"),
            status=ReceiptStatus.PENDING,
        )
        approve_receipt(receipt, amount=Decimal("150"))
        cfg = AppSettings.load()
        self.assertEqual(cfg.service_tax_collected, Decimal("150.00"))

    def test_services_page_shows_synced_total(self):
        PaymentReceipt.objects.create(
            user=self.user,
            amount=Decimal("300"),
            status=ReceiptStatus.APPROVED,
            reviewed_at=timezone.now(),
        )
        ServiceReceipt.objects.create(
            invite=self.invite,
            campaign=self.campaign,
            user=self.user,
            amount=Decimal("500"),
            status=ReceiptStatus.APPROVED,
            reviewed_at=timezone.now(),
        )
        admin = User.objects.create_user("adm", password="pass")
        client = Client()
        client.login(username="adm", password="pass")
        resp = client.get("/panel/services/")
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode("utf-8")
        self.assertTrue("800.00" in body or "800,00" in body)
        self.assertIn("подписки", body)
        self.assertIn("сборы", body)
        cfg = AppSettings.load()
        self.assertEqual(cfg.service_tax_collected, Decimal("800.00"))
