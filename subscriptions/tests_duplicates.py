from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.test import Client, TestCase

from database.models import BotUser, PaymentReceipt, ReceiptStatus
from subscriptions.duplicates import duplicate_labels_for_items, file_sha256
from subscriptions.service import approve_receipt, submit_receipt


class ReceiptDuplicateTests(TestCase):
    def setUp(self) -> None:
        self.user = BotUser.objects.create(max_user_id="dup1", display_name="Евген")
        self.admin = User.objects.create_user("adm", password="pass")
        self.client = Client()
        self.client.login(username="adm", password="pass")
        self.pdf = b"%PDF-1.4 identical cheque payload for tests"
        self.ocr_patch = patch("subscriptions.service.ocr_image_bytes", return_value="")
        self.ocr_patch.start()

    def tearDown(self) -> None:
        self.ocr_patch.stop()

    def test_submit_sets_content_hash(self):
        with self.settings(MEDIA_ROOT="/tmp/voitos_test_media"):
            r = submit_receipt(self.user, self.pdf, filename="a.pdf")
        self.assertEqual(r.content_hash, file_sha256(self.pdf))

    def test_second_submit_flags_duplicate_notes(self):
        with self.settings(MEDIA_ROOT="/tmp/voitos_test_media"):
            first = submit_receipt(self.user, self.pdf, filename="a.pdf")
            second = submit_receipt(self.user, self.pdf, filename="b.pdf")
        self.assertEqual(first.content_hash, second.content_hash)
        self.assertIn("pixel_duplicate_of", second.ai_notes)
        self.assertFalse(second.details_match)

    def test_approve_blocks_pixel_duplicate(self):
        with self.settings(MEDIA_ROOT="/tmp/voitos_test_media"):
            first = submit_receipt(self.user, self.pdf, filename="a.pdf")
            second = submit_receipt(self.user, self.pdf, filename="b.pdf")
        approve_receipt(first, amount=Decimal("450"))
        with self.assertRaises(ValueError) as ctx:
            approve_receipt(second, amount=Decimal("450"))
        self.assertIn("попиксельно", str(ctx.exception).lower())

        # force allows it
        approve_receipt(second, amount=Decimal("450"), force_duplicate=True)
        second.refresh_from_db()
        self.assertEqual(second.status, ReceiptStatus.APPROVED)

    def test_find_dupes_page_highlights(self):
        digest = file_sha256(self.pdf)
        a = PaymentReceipt(user=self.user, amount=Decimal("450"), status=ReceiptStatus.APPROVED, content_hash=digest)
        a.image.save("a.pdf", ContentFile(self.pdf), save=False)
        a.save()
        b = PaymentReceipt(user=self.user, amount=Decimal("450"), status=ReceiptStatus.APPROVED, content_hash=digest)
        b.image.save("b.pdf", ContentFile(self.pdf), save=False)
        b.save()

        labels, groups = duplicate_labels_for_items([a, b])
        self.assertEqual(len(groups), 1)
        self.assertIn(a.id, labels)
        self.assertIn(b.id, labels)

        resp = self.client.get("/panel/receipts/", {"find_dupes": "1"})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Дубль")
        self.assertContains(resp, "Дубли найдены")
        self.assertContains(resp, "Одинаковые чеки")
