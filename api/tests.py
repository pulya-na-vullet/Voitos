"""Tests for mobile API skeleton."""

from __future__ import annotations

import json

from django.test import Client, TestCase, override_settings

from api.models import AppNotification, MobileAuthToken
from api.notifications import notify_user
from database.models import BotUser, ProfileStatus


@override_settings(MOBILE_OTP_DEBUG=True)
class MobileApiTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = BotUser.objects.create(
            max_user_id="app_test1",
            phone="89625507832",
            real_name="Тест",
            profile_status=ProfileStatus.VERIFIED,
        )

    def test_health(self):
        resp = self.client.get("/api/v1/health")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["ok"])

    def test_phone_auth_flow(self):
        start = self.client.post(
            "/api/v1/auth/phone/start",
            data=json.dumps({"phone": "89625507832"}),
            content_type="application/json",
        )
        self.assertEqual(start.status_code, 200)
        code = start.json().get("debug_code")
        self.assertTrue(code)
        verify = self.client.post(
            "/api/v1/auth/phone/verify",
            data=json.dumps({"phone": "89625507832", "code": code}),
            content_type="application/json",
        )
        self.assertEqual(verify.status_code, 200)
        token = verify.json()["access_token"]
        me = self.client.get(
            "/api/v1/me",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.json()["phone"], "89625507832")

    def test_me_requires_auth(self):
        resp = self.client.get("/api/v1/me")
        self.assertEqual(resp.status_code, 401)

    def test_me_subscription_family_members(self):
        dependent = BotUser.objects.create(
            max_user_id="app_family2",
            phone="89625507833",
            real_name="Член семьи",
            profile_status=ProfileStatus.VERIFIED,
            family_payer=self.user,
        )
        tok = MobileAuthToken.objects.create(bot_user=self.user)
        resp = self.client.get(
            "/api/v1/me/subscription",
            HTTP_AUTHORIZATION=f"Bearer {tok.token}",
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn("family", body)
        self.assertTrue(body["family"]["is_payer"])
        names = [m["name"] for m in body["family"]["members"]]
        self.assertIn("Член семьи", names)

        tok2 = MobileAuthToken.objects.create(bot_user=dependent)
        resp2 = self.client.get(
            "/api/v1/me/subscription",
            HTTP_AUTHORIZATION=f"Bearer {tok2.token}",
        )
        self.assertEqual(resp2.status_code, 200)
        fam = resp2.json()["family"]
        self.assertFalse(fam["is_payer"])
        self.assertEqual(fam["payer_name"], "Тест")

    def test_notifications_inbox(self):
        tok = MobileAuthToken.objects.create(bot_user=self.user)
        n = notify_user(
            self.user,
            ntype="work_request.assigned",
            title="Мастер назначен",
            body="Электрик принял заявку",
            entity_type="work_request",
            entity_id=42,
        )
        self.assertIn("work-requests/42", n.deep_link)
        resp = self.client.get(
            "/api/v1/notifications",
            HTTP_AUTHORIZATION=f"Bearer {tok.token}",
        )
        self.assertEqual(resp.status_code, 200)
        items = resp.json()["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["type"], "work_request.assigned")

        read = self.client.post(
            f"/api/v1/notifications/{n.id}/read",
            HTTP_AUTHORIZATION=f"Bearer {tok.token}",
        )
        self.assertEqual(read.status_code, 200)
        n.refresh_from_db()
        self.assertIsNotNone(n.read_at)

    def test_onboarding_endpoint(self):
        tok = MobileAuthToken.objects.create(bot_user=self.user)
        resp = self.client.get(
            "/api/v1/onboarding",
            HTTP_AUTHORIZATION=f"Bearer {tok.token}",
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["total"], 5)
        self.assertEqual(len(data["steps"]), 5)
        self.assertEqual(data["done_count"], 0)
        self.assertFalse(data.get("reward_just_granted"))
        assets = [s.get("asset") for s in data["steps"]]
        self.assertEqual(
            assets,
            [
                "01_snow.jpg",
                "02_playground.jpg",
                "03_electrician.jpg",
                "04_manicure.jpg",
                "05_computer.jpg",
            ],
        )

    def test_register_device(self):
        tok = MobileAuthToken.objects.create(bot_user=self.user)
        resp = self.client.post(
            "/api/v1/devices",
            data=json.dumps(
                {"push_token": "fcm-test-token-abc", "platform": "android"}
            ),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {tok.token}",
        )
        self.assertEqual(resp.status_code, 200)
        tok.refresh_from_db()
        self.assertEqual(tok.push_token, "fcm-test-token-abc")
        self.assertEqual(tok.push_platform, "android")


@override_settings(MOBILE_OTP_DEBUG=True, FCM_SERVER_KEY="", FCM_DRY_RUN=True)
class FcmPushTests(TestCase):
    def setUp(self):
        from api.push import clear_push_log

        clear_push_log()
        self.user = BotUser.objects.create(
            max_user_id="push_u1",
            phone="89625507899",
            real_name="Push",
            profile_status=ProfileStatus.VERIFIED,
        )
        self.tok = MobileAuthToken.objects.create(
            bot_user=self.user,
            push_token="fcm-dry-run-token-001",
            push_platform="android",
        )

    def test_notify_dispatches_dry_run_and_marks_sent(self):
        from api.push import recent_pushes

        n = notify_user(
            self.user,
            ntype="collection.offered",
            title="Снег во дворе",
            body="Нужно 5 участников",
            entity_type="collection",
            entity_id=7,
        )
        n.refresh_from_db()
        self.assertIsNotNone(n.push_sent_at)
        self.assertIn("collections/7", n.deep_link)
        pushes = recent_pushes()
        self.assertEqual(len(pushes), 1)
        self.assertEqual(pushes[0]["type"], "collection.offered")
        self.assertEqual(pushes[0]["notification_id"], n.id)

    def test_no_token_skips_push(self):
        self.tok.push_token = ""
        self.tok.save(update_fields=["push_token"])
        from api.push import recent_pushes

        n = notify_user(
            self.user,
            ntype="work_request.confirm_amount",
            title="Подтвердите сумму",
            entity_type="work_request",
            entity_id=9,
        )
        n.refresh_from_db()
        self.assertIsNone(n.push_sent_at)
        self.assertEqual(recent_pushes(), [])


class AppEmitHookTests(TestCase):
    """Ключевые стори пишут inbox для KMP/пушей."""

    def setUp(self):
        self.client_user = BotUser.objects.create(
            max_user_id="em-c", real_name="Клиент", phone="89001112233", locality="Куюки"
        )
        self.exec_user = BotUser.objects.create(
            max_user_id="em-e", real_name="Мастер", phone="89001112234", locality="Куюки"
        )
        from database.models import ExecutorRole, ContractorProfile, ContractorStatus

        self.role = ExecutorRole.objects.create(
            code="em_elec", name="Электрик", requires_work_photos=True
        )
        self.profile = ContractorProfile.objects.create(
            user=self.exec_user,
            role=self.role,
            status=ContractorStatus.VERIFIED,
            phone="89001112234",
            locality="Куюки",
        )

    def test_accept_offer_emits_assigned(self):
        from database.models import (
            WorkRequest,
            WorkRequestOffer,
            WorkRequestOfferStatus,
            WorkRequestStatus,
        )
        from services.work_request_dispatch import accept_offer

        wr = WorkRequest.objects.create(
            user=self.client_user,
            role=self.role,
            description="Розетка искрит на кухне",
            status=WorkRequestStatus.OFFERING,
            client_locality="Куюки",
        )
        offer = WorkRequestOffer.objects.create(
            work_request=wr,
            contractor=self.profile,
            status=WorkRequestOfferStatus.OFFERED,
        )
        accept_offer(offer, send_fn=lambda *a, **k: None)
        n = AppNotification.objects.filter(
            bot_user=self.client_user, type="work_request.assigned"
        ).first()
        self.assertIsNotNone(n)
        self.assertIn(f"work-requests/{wr.id}", n.deep_link)

    def test_collection_offer_emits(self):
        from decimal import Decimal

        from database.models import ServiceCampaign, ServiceCategory, ServiceGroup, CampaignStatus
        from services.service import offer_to_users

        g = ServiceGroup.objects.create(name="Двор")
        g.members.add(self.client_user)
        camp = ServiceCampaign.objects.create(
            title="Уборка снега",
            category=ServiceCategory.SNOW,
            group=g,
            status=CampaignStatus.DRAFT,
            total_amount=Decimal("1000"),
            amount_per_user=Decimal("100"),
        )
        offer_to_users(camp, [self.client_user.id], Decimal("100"), send_fn=lambda *a, **k: None)
        n = AppNotification.objects.filter(
            bot_user=self.client_user, type="collection.offered"
        ).first()
        self.assertIsNotNone(n)
        self.assertIn(f"collections/{camp.id}", n.deep_link)

    def test_confirm_amount_api(self):
        from decimal import Decimal

        from database.models import (
            WorkRequest,
            WorkRequestPayMethod,
            WorkRequestStatus,
        )

        wr = WorkRequest.objects.create(
            user=self.client_user,
            role=self.role,
            description="Готово",
            status=WorkRequestStatus.AWAITING_CLIENT,
            pay_method=WorkRequestPayMethod.CASH,
            reported_amount=Decimal("2000"),
            assigned_contractor=self.profile,
        )
        tok = MobileAuthToken.objects.create(bot_user=self.client_user)
        resp = self.client.post(
            f"/api/v1/work-requests/{wr.id}/confirm-amount",
            data=json.dumps({"confirmed": True}),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {tok.token}",
        )
        self.assertEqual(resp.status_code, 200)
        wr.refresh_from_db()
        self.assertEqual(wr.status, "awaiting_commission")

    def test_receipt_approve_emits(self):
        from decimal import Decimal
        from unittest.mock import patch

        from database.models import PaymentReceipt, ReceiptStatus
        from subscriptions.service import approve_receipt, reject_receipt

        with self.settings(MEDIA_ROOT="/tmp/voitos_kmp_receipt"):
            with patch("subscriptions.service.ocr_image_bytes", return_value=""):
                from subscriptions.service import submit_receipt

                r = submit_receipt(
                    self.client_user, b"%PDF-1.4 kmp receipt test", filename="k.pdf"
                )
        approve_receipt(r, amount=Decimal("100"), force_duplicate=True)
        n = AppNotification.objects.filter(
            bot_user=self.client_user, type="subscription.receipt_approved"
        ).first()
        self.assertIsNotNone(n)
        self.assertIn("subscription", n.deep_link)

        r2 = PaymentReceipt.objects.create(
            user=self.client_user,
            status=ReceiptStatus.PENDING,
            amount=Decimal("100"),
        )
        reject_receipt(r2, comment="нечитаемо")
        n2 = AppNotification.objects.filter(
            bot_user=self.client_user, type="subscription.receipt_rejected"
        ).first()
        self.assertIsNotNone(n2)

    def test_renewal_mark_sent_emits(self):
        from datetime import timedelta

        from django.utils import timezone

        from database.models import Reminder
        from reminders.service import ReminderService
        from subscriptions.renewal_reminders import RENEWAL_MARKER, payer_renewal_text

        until = timezone.now() + timedelta(days=5)
        rem = Reminder.objects.create(
            user=self.client_user,
            text=payer_renewal_text(until, "за 4 дня"),
            due_at=timezone.now() - timedelta(minutes=1),
        )
        self.assertTrue(rem.text.startswith(RENEWAL_MARKER))
        ReminderService().mark_sent(rem)
        n = AppNotification.objects.filter(
            bot_user=self.client_user, type="subscription.renewal_4d"
        ).first()
        self.assertIsNotNone(n)

    def test_collection_detail(self):
        from decimal import Decimal

        from database.models import (
            CampaignStatus,
            ServiceCampaign,
            ServiceCategory,
            ServiceGroup,
            ServiceInvite,
        )

        g = ServiceGroup.objects.create(name="Двор-2")
        g.members.add(self.client_user)
        camp = ServiceCampaign.objects.create(
            title="Снег",
            category=ServiceCategory.SNOW,
            group=g,
            status=CampaignStatus.ACTIVE,
            total_amount=Decimal("500"),
            amount_per_user=Decimal("100"),
        )
        ServiceInvite.objects.create(
            campaign=camp, user=self.client_user, amount_due=Decimal("100")
        )
        tok = MobileAuthToken.objects.create(bot_user=self.client_user)
        resp = self.client.get(
            f"/api/v1/collections/{camp.id}",
            HTTP_AUTHORIZATION=f"Bearer {tok.token}",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["title"], "Снег")
        self.assertEqual(resp.json()["amount_due"], 100.0)

    def test_create_work_request_api(self):
        tok = MobileAuthToken.objects.create(bot_user=self.client_user)
        resp = self.client.post(
            "/api/v1/work-requests",
            data=json.dumps(
                {"role_id": self.role.id, "description": "Розетка не работает"}
            ),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {tok.token}",
        )
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertTrue(data["needs_photos"])
        self.assertEqual(data["status"], "draft")

    def test_work_request_photo_and_submit(self):
        import base64

        from database.models import WorkRequest, WorkRequestStatus

        wr = WorkRequest.objects.create(
            user=self.client_user,
            role=self.role,
            description="Нужен электрик срочно",
            status=WorkRequestStatus.DRAFT,
            client_locality="Куюки",
        )
        tok = MobileAuthToken.objects.create(bot_user=self.client_user)
        # minimal jpeg bytes
        jpeg = base64.b64decode(
            "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkS"
            "Ew8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJ"
            "CQwLDBgNDRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIy"
            "MjIyMjIyMjIyMjIyMjL/wAARCAABAAEDASIAAhEBAxEB/8QAFQABAQAAAAAAAAAA"
            "AAAAAAAAAAn/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/8QAFQEBAQAAAAAAAAAAAAAA"
            "AAAAAAX/xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oADAMBAAIQAxAAAAGcP//EABQQ"
            "AQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAQUCf//EABQRAQAAAAAAAAAAAAAAAAAA"
            "AAD/2gAIAQMBAT8Bf//EABQRAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQIBAT8Bf//Z"
        )
        b64 = base64.b64encode(jpeg).decode("ascii")
        with self.settings(MEDIA_ROOT="/tmp/voitos_kmp_wr_photos"):
            add = self.client.post(
                f"/api/v1/work-requests/{wr.id}/photos",
                data=json.dumps({"content_base64": b64, "filename": "t.jpg"}),
                content_type="application/json",
                HTTP_AUTHORIZATION=f"Bearer {tok.token}",
            )
            self.assertEqual(add.status_code, 201)
            self.assertEqual(add.json()["photo_count"], 1)
            # второе фото с тем же filename — не должно затереть первое
            add2 = self.client.post(
                f"/api/v1/work-requests/{wr.id}/photos",
                data=json.dumps({"content_base64": b64, "filename": "t.jpg"}),
                content_type="application/json",
                HTTP_AUTHORIZATION=f"Bearer {tok.token}",
            )
            self.assertEqual(add2.status_code, 201)
            self.assertEqual(add2.json()["photo_count"], 2)
            self.assertNotEqual(add.json()["photo_id"], add2.json()["photo_id"])
            self.assertEqual(wr.photos.count(), 2)
            submit = self.client.post(
                f"/api/v1/work-requests/{wr.id}/submit",
                data=json.dumps({}),
                content_type="application/json",
                HTTP_AUTHORIZATION=f"Bearer {tok.token}",
            )
        self.assertEqual(submit.status_code, 200)
        wr.refresh_from_db()
        self.assertNotEqual(wr.status, WorkRequestStatus.DRAFT)
        self.assertIn(wr.status, {WorkRequestStatus.PENDING, WorkRequestStatus.OFFERING})
        self.assertEqual(wr.photos.count(), 2)

    def test_work_request_cancel_api(self):
        from database.models import WorkRequest, WorkRequestStatus

        wr = WorkRequest.objects.create(
            user=self.client_user,
            role=self.role,
            description="Нужен электрик",
            status=WorkRequestStatus.OFFERING,
            client_locality="Куюки",
        )
        tok = MobileAuthToken.objects.create(bot_user=self.client_user)
        resp = self.client.post(
            f"/api/v1/work-requests/{wr.id}/cancel",
            data=json.dumps({}),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {tok.token}",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "cancelled")
        wr.refresh_from_db()
        self.assertEqual(wr.status, WorkRequestStatus.CANCELLED)

    def test_onboarding_complete_step(self):
        tok = MobileAuthToken.objects.create(bot_user=self.client_user)
        resp = self.client.post(
            "/api/v1/onboarding/steps/snow/complete",
            HTTP_AUTHORIZATION=f"Bearer {tok.token}",
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["done_count"], 1)
        self.assertEqual(len(data["steps"]), 5)
        self.assertTrue(any(s["code"] == "snow" and s["done"] for s in data["steps"]))
        self.assertIn("caption", data["steps"][0])
        self.assertFalse(data.get("reward_just_granted"))

    def test_onboarding_full_grants_month_once(self):
        from bot.onboarding import STORY_CODES

        tok = MobileAuthToken.objects.create(bot_user=self.client_user)
        last = None
        for code in STORY_CODES:
            last = self.client.post(
                f"/api/v1/onboarding/steps/{code}/complete",
                HTTP_AUTHORIZATION=f"Bearer {tok.token}",
            )
            self.assertEqual(last.status_code, 200)
        data = last.json()
        self.assertEqual(data["done_count"], 5)
        self.assertTrue(data["completed"])
        self.assertTrue(data["reward_granted"])
        self.assertTrue(data["reward_just_granted"])
        self.client_user.refresh_from_db()
        self.assertTrue(self.client_user.onboarding_reward_granted)
        self.assertIsNotNone(self.client_user.subscription_until)
        note = AppNotification.objects.filter(
            bot_user=self.client_user, type="subscription.onboarding_reward"
        ).first()
        self.assertIsNotNone(note)

        again = self.client.post(
            f"/api/v1/onboarding/steps/{STORY_CODES[-1]}/complete",
            HTTP_AUTHORIZATION=f"Bearer {tok.token}",
        )
        self.assertFalse(again.json().get("reward_just_granted"))

    def test_upload_subscription_receipt(self):
        import base64
        from unittest.mock import patch

        tok = MobileAuthToken.objects.create(bot_user=self.client_user)
        jpeg = base64.b64decode(
            "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkS"
            "Ew8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJ"
            "CQwLDBgNDRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIy"
            "MjIyMjIyMjIyMjIyMjL/wAARCAABAAEDASIAAhEBAxEB/8QAFQABAQAAAAAAAAAA"
            "AAAAAAAAAAn/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/8QAFQEBAQAAAAAAAAAAAAAA"
            "AAAAAAX/xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oADAMBAAIQAxAAAAGcP//EABQQ"
            "AQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAQUCf//EABQRAQAAAAAAAAAAAAAAAAAA"
            "AAD/2gAIAQMBAT8Bf//EABQRAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQIBAT8Bf//Z"
        )
        b64 = base64.b64encode(jpeg).decode("ascii")
        with self.settings(MEDIA_ROOT="/tmp/voitos_kmp_receipts"):
            with patch("subscriptions.service.ocr_image_bytes", return_value=""):
                resp = self.client.post(
                    "/api/v1/me/receipts",
                    data=json.dumps(
                        {"content_base64": b64, "filename": "pay.jpg"}
                    ),
                    content_type="application/json",
                    HTTP_AUTHORIZATION=f"Bearer {tok.token}",
                )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.json()["status"], "pending")
        listed = self.client.get(
            "/api/v1/me/receipts",
            HTTP_AUTHORIZATION=f"Bearer {tok.token}",
        )
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(len(listed.json()["items"]), 1)
