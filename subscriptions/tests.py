from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from database.models import AccessState, BotUser, PaymentReceipt, ReceiptStatus
from subscriptions.receipts import names_match, normalize_phone
from subscriptions.service import (
    approve_receipt,
    period_from_amount,
    reject_receipt,
)


class SubscriptionTests(TestCase):
    def setUp(self) -> None:
        self.user = BotUser.objects.create(max_user_id="100", display_name="Client")

    def test_phone_normalize(self):
        self.assertEqual(normalize_phone("+7 (962) 550-78-32"), "89625507832")

    def test_name_match(self):
        self.assertTrue(
            names_match(
                "Григорьев Дмитрий Вячеславович",
                "Перевод Григорьев Дмитрий В.",
            )
        )

    def test_new_user_grace(self):
        self.assertEqual(self.user.access_state(), AccessState.GRACE)
        self.assertTrue(self.user.has_feature_access())

    def test_blocked_after_grace(self):
        self.user.subscription_until = timezone.now() - timedelta(days=5)
        self.user.grace_until = timezone.now() - timedelta(days=1)
        self.user.save()
        self.assertEqual(self.user.access_state(), AccessState.BLOCKED)
        self.assertFalse(self.user.has_feature_access())

    def test_period_from_amount_includes_days(self):
        self.assertEqual(period_from_amount(Decimal("450"), Decimal("100")), (4, 15))
        self.assertEqual(period_from_amount(Decimal("100"), Decimal("100")), (1, 0))
        self.assertEqual(period_from_amount(Decimal("50"), Decimal("100")), (0, 15))

    def test_approve_extends_subscription(self):
        receipt = PaymentReceipt.objects.create(
            user=self.user,
            amount=Decimal("50"),
            details_match=True,
            status=ReceiptStatus.PENDING,
        )
        before = timezone.now()
        approve_receipt(receipt, amount=Decimal("450"))
        receipt.refresh_from_db()
        self.user.refresh_from_db()
        self.assertEqual(receipt.status, ReceiptStatus.APPROVED)
        self.assertEqual(receipt.amount, Decimal("450"))
        self.assertEqual(receipt.months_granted, 4)
        self.assertEqual(receipt.days_granted, 15)
        self.assertEqual(receipt.period_label(), "4 мес. 15 дн.")
        self.assertTrue(receipt.details_match)
        self.assertEqual(receipt.transfer_date, timezone.localdate())
        self.assertIsNotNone(self.user.subscription_until)
        # 4*30 + 15 = 135 days
        delta = self.user.subscription_until - before
        self.assertGreaterEqual(delta.days, 134)
        self.assertLessEqual(delta.days, 135)
        self.assertEqual(self.user.access_state(), AccessState.ACTIVE)

    def test_recalculate_old_whole_months(self):
        """Old approved 450₽ → 4 months should become 4 months + 15 days."""
        from datetime import timedelta

        from subscriptions.service import recalculate_approved_receipt_periods

        receipt = PaymentReceipt.objects.create(
            user=self.user,
            amount=Decimal("450"),
            status=ReceiptStatus.APPROVED,
            months_granted=4,
            days_granted=0,
            details_match=False,
            reviewed_at=timezone.now(),
            transfer_date=None,
        )
        self.user.subscription_until = timezone.now() + timedelta(days=120)
        self.user.save(update_fields=["subscription_until"])

        n = recalculate_approved_receipt_periods()
        self.assertEqual(n, 1)
        receipt.refresh_from_db()
        self.user.refresh_from_db()
        self.assertEqual(receipt.months_granted, 4)
        self.assertEqual(receipt.days_granted, 15)
        self.assertTrue(receipt.details_match)
        self.assertIsNotNone(receipt.transfer_date)
        remaining = (self.user.subscription_until - timezone.now()).days
        self.assertGreaterEqual(remaining, 134)
        self.assertLessEqual(remaining, 135)
        # Idempotent
        self.assertEqual(recalculate_approved_receipt_periods(), 0)

    def test_approve_requires_manual_amount(self):
        receipt = PaymentReceipt.objects.create(
            user=self.user,
            amount=Decimal("100"),
            status=ReceiptStatus.PENDING,
        )
        with self.assertRaises(ValueError):
            approve_receipt(receipt)
        with self.assertRaises(ValueError):
            approve_receipt(receipt, amount=Decimal("0"))

    def test_reject_notifies_status(self):
        receipt = PaymentReceipt.objects.create(
            user=self.user,
            amount=Decimal("100"),
            status=ReceiptStatus.PENDING,
        )
        reject_receipt(receipt, comment="не тот получатель")
        receipt.refresh_from_db()
        self.assertEqual(receipt.status, ReceiptStatus.REJECTED)

    def test_unpaid_user_does_not_keep_free_month(self):
        """Users without approved receipt must not stay on gifted full access."""
        from subscriptions.service import revoke_unpaid_subscriptions

        self.user.subscription_until = timezone.now() + timedelta(days=30)
        self.user.grace_until = None
        self.user.save()
        self.assertEqual(self.user.access_state(), AccessState.ACTIVE)

        n = revoke_unpaid_subscriptions()
        self.user.refresh_from_db()
        self.assertEqual(n, 1)
        self.assertIsNone(self.user.subscription_until)
        self.assertEqual(self.user.access_state(), AccessState.GRACE)
        self.assertTrue(self.user.has_feature_access())

    def test_paid_user_keeps_subscription_after_revoke_pass(self):
        from subscriptions.service import revoke_unpaid_subscriptions

        self.user.subscription_until = timezone.now() + timedelta(days=20)
        self.user.save()
        PaymentReceipt.objects.create(
            user=self.user,
            amount=Decimal("100"),
            status=ReceiptStatus.APPROVED,
            months_granted=1,
        )
        revoke_unpaid_subscriptions()
        self.user.refresh_from_db()
        self.assertIsNotNone(self.user.subscription_until)
        self.assertEqual(self.user.access_state(), AccessState.ACTIVE)
