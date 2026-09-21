from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from database.models import BotUser, PaymentReceipt, ReceiptStatus, Reminder
from subscriptions.family import link_family_members, unlink_family_member
from subscriptions.renewal_reminders import (
    RENEWAL_MARKER,
    RENEWAL_OFFSETS,
    schedule_subscription_renewal_reminders,
    strip_renewal_marker,
)
from subscriptions.service import approve_receipt


class RenewalReminderTests(TestCase):
    def setUp(self) -> None:
        self.dmitry = BotUser.objects.create(
            max_user_id="ren-dm",
            real_name="Дмитрий",
        )
        self.elena = BotUser.objects.create(
            max_user_id="ren-el",
            real_name="Елена",
        )

    def test_approve_schedules_4d_2d_2h_for_payer(self):
        receipt = PaymentReceipt.objects.create(
            user=self.dmitry,
            amount=Decimal("100"),
            status=ReceiptStatus.PENDING,
        )
        approve_receipt(receipt, amount=Decimal("100"))
        self.dmitry.refresh_from_db()
        self.assertIsNotNone(self.dmitry.subscription_until)

        reminders = list(
            Reminder.objects.filter(
                user=self.dmitry,
                is_done=False,
                text__startswith=RENEWAL_MARKER,
            ).order_by("due_at")
        )
        self.assertEqual(len(reminders), 3)
        until = self.dmitry.subscription_until
        expected = sorted(until - delta for delta, _ in RENEWAL_OFFSETS)
        actual = [r.due_at for r in reminders]
        for a, e in zip(actual, expected):
            self.assertAlmostEqual(a.timestamp(), e.timestamp(), delta=2)
        body = reminders[0].text
        self.assertIn("Продлите доступ", body)
        self.assertIsNotNone(strip_renewal_marker(body))
        self.assertFalse(strip_renewal_marker(body).startswith(RENEWAL_MARKER))

    def test_approve_schedules_family_nudge_for_dependent(self):
        link_family_members([self.elena, self.dmitry], payer=self.dmitry)
        receipt = PaymentReceipt.objects.create(
            user=self.dmitry,
            amount=Decimal("100"),
            status=ReceiptStatus.PENDING,
        )
        approve_receipt(receipt, amount=Decimal("100"))

        elena_rems = list(
            Reminder.objects.filter(
                user=self.elena,
                is_done=False,
                text__startswith=RENEWAL_MARKER,
            )
        )
        self.assertEqual(len(elena_rems), 3)
        sample = strip_renewal_marker(elena_rems[0].text) or ""
        self.assertIn("Напомните Дмитрий", sample)
        self.assertIn("подписка", sample.lower())

    def test_reschedule_replaces_old_reminders(self):
        self.dmitry.subscription_until = timezone.now() + timedelta(days=40)
        self.dmitry.save(update_fields=["subscription_until"])
        schedule_subscription_renewal_reminders(self.dmitry)
        first_ids = set(
            Reminder.objects.filter(user=self.dmitry, text__startswith=RENEWAL_MARKER).values_list(
                "id", flat=True
            )
        )
        self.assertEqual(len(first_ids), 3)

        self.dmitry.subscription_until = timezone.now() + timedelta(days=70)
        self.dmitry.save(update_fields=["subscription_until"])
        schedule_subscription_renewal_reminders(self.dmitry)
        second = list(
            Reminder.objects.filter(
                user=self.dmitry,
                is_done=False,
                text__startswith=RENEWAL_MARKER,
            )
        )
        self.assertEqual(len(second), 3)
        self.assertTrue(first_ids.isdisjoint({r.id for r in second}))

    def test_skip_past_offsets_for_short_subscription(self):
        self.dmitry.subscription_until = timezone.now() + timedelta(hours=5)
        self.dmitry.save(update_fields=["subscription_until"])
        n = schedule_subscription_renewal_reminders(self.dmitry)
        # only 2h window is in the future
        self.assertEqual(n, 1)
        rem = Reminder.objects.get(user=self.dmitry, text__startswith=RENEWAL_MARKER)
        self.assertIn("за 2 часа", rem.text)

    def test_unlink_cancels_family_reminders(self):
        link_family_members([self.elena, self.dmitry], payer=self.dmitry)
        self.dmitry.subscription_until = timezone.now() + timedelta(days=30)
        self.dmitry.save(update_fields=["subscription_until"])
        schedule_subscription_renewal_reminders(self.dmitry)
        self.assertEqual(
            Reminder.objects.filter(user=self.elena, text__startswith=RENEWAL_MARKER).count(),
            3,
        )
        unlink_family_member(self.elena)
        self.assertEqual(
            Reminder.objects.filter(user=self.elena, text__startswith=RENEWAL_MARKER).count(),
            0,
        )
