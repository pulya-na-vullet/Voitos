from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.utils import timezone

from database.models import BotUser, PaymentReceipt, ReceiptStatus
from subscriptions.service import approve_receipt, delete_receipt, submit_receipt


class DeleteReceiptTests(TestCase):
    def setUp(self) -> None:
        self.user = BotUser.objects.create(max_user_id="del1", display_name="Евген")
        self.admin = User.objects.create_superuser("adm", "adm@t.com", "pass")
        from database.models import PanelProfile, PanelRole
        PanelProfile.objects.create(user=self.admin, role=PanelRole.ADMIN)
        self.client = Client()
        self.client.login(username="adm", password="pass")
        self.pdf_a = b"%PDF-1.4 cheque A unique bytes"
        self.pdf_b = b"%PDF-1.4 cheque B other unique"
        self.ocr_patch = patch("subscriptions.service.ocr_image_bytes", return_value="")
        self.ocr_patch.start()

    def tearDown(self) -> None:
        self.ocr_patch.stop()

    def test_delete_requires_reason(self):
        with self.settings(MEDIA_ROOT="/tmp/voitos_del_media"):
            r = submit_receipt(self.user, self.pdf_a, filename="a.pdf")
        with self.assertRaises(ValueError):
            delete_receipt(r, reason="  ")

    def test_delete_approved_rebuilds_subscription(self):
        with self.settings(MEDIA_ROOT="/tmp/voitos_del_media"):
            first = submit_receipt(self.user, self.pdf_a, filename="a.pdf")
            second = submit_receipt(self.user, self.pdf_b, filename="b.pdf")
        approve_receipt(first, amount=Decimal("450"), force_duplicate=True)
        approve_receipt(second, amount=Decimal("450"), force_duplicate=True)
        self.user.refresh_from_db()
        until_both = self.user.subscription_until
        self.assertIsNotNone(until_both)

        delete_receipt(second, reason="Ошибочно принят повторный одинаковый чек")
        self.user.refresh_from_db()
        self.assertFalse(PaymentReceipt.objects.filter(pk=second.pk).exists())
        self.assertIsNotNone(self.user.subscription_until)
        # After removing one 4.5 month grant, remaining until should be earlier
        self.assertLess(self.user.subscription_until, until_both)

        # Only first receipt remains → ~4.5 months from its review
        remaining = PaymentReceipt.objects.filter(
            user=self.user, status=ReceiptStatus.APPROVED
        ).count()
        self.assertEqual(remaining, 1)

    def test_panel_delete_notifies(self):
        with self.settings(MEDIA_ROOT="/tmp/voitos_del_media"):
            r = submit_receipt(self.user, self.pdf_a, filename="a.pdf")
        approve_receipt(r, amount=Decimal("100"))
        with patch("panel.views._notify_user") as notify:
            resp = self.client.post(
                f"/panel/receipts/{r.id}/delete/",
                {
                    "reason": "Дубль чека, срок пересчитан",
                    "next": "/panel/receipts/?find_dupes=1",
                },
            )
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(PaymentReceipt.objects.filter(pk=r.id).exists())
        notify.assert_called_once()
        msg = notify.call_args[0][1]
        self.assertIn("удалил", msg.lower())
        self.assertIn("Дубль чека", msg)

    def test_find_dupes_marks_row_class(self):
        digest = __import__(
            "subscriptions.duplicates", fromlist=["file_sha256"]
        ).file_sha256(self.pdf_a)
        from django.core.files.base import ContentFile

        a = PaymentReceipt(
            user=self.user,
            amount=Decimal("450"),
            status=ReceiptStatus.APPROVED,
            content_hash=digest,
        )
        a.image.save("a.pdf", ContentFile(self.pdf_a), save=False)
        a.save()
        b = PaymentReceipt(
            user=self.user,
            amount=Decimal("450"),
            status=ReceiptStatus.APPROVED,
            content_hash=digest,
        )
        b.image.save("b.pdf", ContentFile(self.pdf_a), save=False)
        b.save()
        resp = self.client.get("/panel/receipts/", {"find_dupes": "1"})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "dupe-row")
        self.assertContains(resp, "background:rgba(230,192,123")
        self.assertContains(resp, "Дубль")
