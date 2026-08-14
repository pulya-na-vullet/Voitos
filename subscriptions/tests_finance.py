from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.utils import timezone

from ai.usage import estimate_llm_cost, log_ai_usage
from database.models import (
    AiUsageKind,
    AiUsageLog,
    BotUser,
    PaymentReceipt,
    ReceiptStatus,
    YandexBillingEntry,
)
from subscriptions.finance import build_finance_snapshot, collected_between, period_bounds
from subscriptions.service import approve_receipt


class SubscriptionFinanceTests(TestCase):
    def setUp(self) -> None:
        self.user = BotUser.objects.create(max_user_id="fin1", display_name="Fin")
        self.admin = User.objects.create_superuser("admin", "a@t.com", "pass")
        from database.models import PanelProfile, PanelRole

        PanelProfile.objects.create(user=self.admin, role=PanelRole.ADMIN)
        self.client = Client()
        self.client.login(username="admin", password="pass")

    def test_collected_and_net_with_billing(self):
        receipt = PaymentReceipt.objects.create(
            user=self.user,
            amount=Decimal("300"),
            status=ReceiptStatus.PENDING,
        )
        approve_receipt(receipt, amount=Decimal("300"))
        receipt.refresh_from_db()
        self.assertEqual(receipt.status, ReceiptStatus.APPROVED)

        log_ai_usage(
            kind=AiUsageKind.LLM,
            estimated_cost_rub=Decimal("12.50"),
            model_name="yandexgpt-lite",
            input_tokens=1000,
            output_tokens=200,
        )
        YandexBillingEntry.objects.create(
            for_date=timezone.localdate(),
            amount_rub=Decimal("40.00"),
            note="тест",
        )

        snap = build_finance_snapshot()
        month = snap["periods"]["month"]
        self.assertEqual(month["collected"], Decimal("300.00"))
        self.assertEqual(month["ai_estimated"], Decimal("12.50"))
        self.assertEqual(month["ai_billed"], Decimal("40.00"))
        self.assertEqual(month["ai_used"], Decimal("40.00"))
        self.assertEqual(month["net"], Decimal("260.00"))
        self.assertGreaterEqual(month["new_users"], 1)

    def test_estimate_without_billing(self):
        bounds = period_bounds()
        start, end = bounds["week"]
        AiUsageLog.objects.create(
            kind=AiUsageKind.OCR,
            estimated_cost_rub=Decimal("1.25"),
            units=1,
        )
        collected, n = collected_between(start, end)
        self.assertEqual(collected, Decimal("0.00"))
        self.assertEqual(n, 0)
        snap = build_finance_snapshot()
        self.assertEqual(snap["periods"]["week"]["ai_source"], "estimate")
        self.assertEqual(snap["periods"]["week"]["ai_used"], Decimal("1.25"))

    def test_llm_cost_estimate(self):
        self.assertEqual(estimate_llm_cost(1000), Decimal("0.4000"))

    def test_receipts_page_shows_finance(self):
        PaymentReceipt.objects.create(
            user=self.user,
            amount=Decimal("100"),
            status=ReceiptStatus.APPROVED,
            reviewed_at=timezone.now(),
        )
        resp = self.client.get("/panel/receipts/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Оплата подписок")
        self.assertContains(resp, "Собрано за месяц")
        self.assertContains(resp, "Чистыми за месяц")
        self.assertContains(resp, "Новые за неделю")

    def test_add_yandex_spend_post(self):
        resp = self.client.post(
            "/panel/receipts/",
            {
                "action": "add_yandex_spend",
                "for_date": timezone.localdate().isoformat(),
                "amount": "15.5",
                "note": "cloud",
            },
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(YandexBillingEntry.objects.count(), 1)
        entry = YandexBillingEntry.objects.get()
        self.assertEqual(entry.amount_rub, Decimal("15.50"))
