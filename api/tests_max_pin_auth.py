"""Tests for MAX login code + permanent PIN."""

from __future__ import annotations

import json

from django.test import Client, TestCase, override_settings

from api.models import MobileAuthToken, PinChallenge
from database.models import BotUser, ProfileStatus
from services import mobile_auth


@override_settings(MOBILE_OTP_DEBUG=True, AUTH_BOT_INTERNAL_TOKEN="test-internal")
class MaxPinAuthTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = BotUser.objects.create(
            max_user_id="max_auth_1",
            phone="89625507832",
            chat_id="chat1",
            real_name="Тест",
            profile_status=ProfileStatus.VERIFIED,
        )

    def test_phone_login_requires_max_registration(self):
        # Только app_* — не считается зарегистрированным в Max
        BotUser.objects.create(
            max_user_id="app_89625509999",
            phone="89625509999",
        )
        resp = self.client.post(
            "/api/v1/auth/phone/login-request",
            data=json.dumps({"phone": "89625509999"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()["error"], "not_registered")

    def test_phone_login_sends_code_for_max_user(self):
        req = self.client.post(
            "/api/v1/auth/phone/login-request",
            data=json.dumps({"phone": "89625507832"}),
            content_type="application/json",
        )
        self.assertEqual(req.status_code, 200)
        code = req.json()["debug_code"]
        verify = self.client.post(
            "/api/v1/auth/phone/login-verify",
            data=json.dumps({"phone": "89625507832", "code": code}),
            content_type="application/json",
        )
        self.assertEqual(verify.status_code, 200)
        self.assertTrue(verify.json()["access_token"])
        self.assertTrue(verify.json().get("needs_onboarding", True))

    def test_max_start_and_verify_then_set_pin(self):
        start = self.client.post(
            "/api/v1/auth/max/start",
            data=json.dumps(
                {
                    "max_user_id": "max_auth_1",
                    "phone": "89625507832",
                    "chat_id": "chat1",
                    "send": False,
                }
            ),
            content_type="application/json",
            HTTP_X_VOITOS_INTERNAL="test-internal",
        )
        self.assertEqual(start.status_code, 200)
        code = start.json()["debug_code"]
        self.assertEqual(len(code), 4)

        verify = self.client.post(
            "/api/v1/auth/max/verify",
            data=json.dumps({"phone": "89625507832", "code": code}),
            content_type="application/json",
        )
        self.assertEqual(verify.status_code, 200)
        data = verify.json()
        self.assertTrue(data["access_token"])
        self.assertTrue(data["needs_pin_setup"])

        pin_set = self.client.post(
            "/api/v1/auth/pin/set",
            data=json.dumps({"pin": "1937"}),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {data['access_token']}",
        )
        self.assertEqual(pin_set.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.pin_hash)

        login = self.client.post(
            "/api/v1/auth/pin/login",
            data=json.dumps({"phone": "89625507832", "pin": "1937"}),
            content_type="application/json",
        )
        self.assertEqual(login.status_code, 200)
        self.assertFalse(login.json()["needs_pin_setup"])

    def test_merge_app_phone_user(self):
        app_user = BotUser.objects.create(
            max_user_id="app_89625501111",
            phone="89625501111",
            display_name="AppOnly",
        )
        mobile_auth.start_max_login(
            max_user_id="max_merged",
            phone="89625501111",
            chat_id="c2",
            send=False,
        )
        app_user.refresh_from_db()
        self.assertEqual(app_user.max_user_id, "max_merged")
        self.assertEqual(app_user.chat_id, "c2")

    def test_pin_reset_flow(self):
        mobile_auth.set_pin(self.user, "1111")
        req = self.client.post(
            "/api/v1/auth/pin/reset-request",
            data=json.dumps({"phone": "89625507832"}),
            content_type="application/json",
        )
        self.assertEqual(req.status_code, 200)
        code = req.json()["debug_code"]
        confirm = self.client.post(
            "/api/v1/auth/pin/reset-confirm",
            data=json.dumps({"phone": "89625507832", "code": code, "pin": "2222"}),
            content_type="application/json",
        )
        self.assertEqual(confirm.status_code, 200)
        login = self.client.post(
            "/api/v1/auth/pin/login",
            data=json.dumps({"phone": "89625507832", "pin": "2222"}),
            content_type="application/json",
        )
        self.assertEqual(login.status_code, 200)

    def test_max_start_requires_internal_token(self):
        resp = self.client.post(
            "/api/v1/auth/max/start",
            data=json.dumps(
                {"max_user_id": "x", "phone": "89625507832", "send": False}
            ),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 403)

    def test_app_registration_with_max_code(self):
        phone = "89625506666"
        start = self.client.post(
            "/api/v1/auth/register/start",
            data=json.dumps(
                {
                    "phone": phone,
                    "real_name": "Иван Иванов",
                    "gender": "male",
                    "birth_date": "15.05.1990",
                    "address": "ул. Ленина, 1",
                    "locality": "Куюки",
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(start.status_code, 200)
        body = start.json()
        self.assertTrue(body["ok"])
        self.assertIn("?start=appreg-", body["max_bot_open_url"])

        max_user = BotUser.objects.create(
            max_user_id="max_reg_new",
            chat_id="chat_reg",
            display_name="MaxReg",
        )
        draft_id = int(body["max_bot_open_url"].rsplit("-", 1)[-1])
        auto = mobile_auth.bot_handle_app_register_start_payload(
            max_user, f"appreg-{draft_id}"
        )
        self.assertIsNotNone(auto)
        self.assertIn("Код для завершения регистрации", auto)
        code = auto.split(":")[1].split()[0].strip()
        self.assertEqual(len(code), 4)

        confirm = self.client.post(
            "/api/v1/auth/register/confirm",
            data=json.dumps({"phone": phone, "code": code}),
            content_type="application/json",
        )
        self.assertEqual(confirm.status_code, 200)
        data = confirm.json()
        self.assertTrue(data["access_token"])
        self.assertTrue(data["needs_pin_setup"])

        max_user.refresh_from_db()
        self.assertEqual(max_user.phone, phone)
        self.assertEqual(max_user.real_name, "Иван Иванов")
        self.assertEqual(max_user.gender, "male")
        self.assertEqual(str(max_user.birth_date), "1990-05-15")
        self.assertEqual(max_user.address, "ул. Ленина, 1")
        self.assertEqual(max_user.locality, "Куюки")
        self.assertEqual(max_user.profile_status, ProfileStatus.PENDING_REVIEW)

    def test_register_start_rejects_existing_max_user(self):
        resp = self.client.post(
            "/api/v1/auth/register/start",
            data=json.dumps(
                {
                    "phone": "89625507832",
                    "real_name": "Уже Есть",
                    "gender": "female",
                    "birth_date": "1991-01-01",
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.json()["error"], "already_registered")

    def test_plain_kod_triggers_register_flow(self):
        self.assertTrue(mobile_auth.looks_like_app_register("код"))
        self.assertTrue(mobile_auth.looks_like_app_register("Код"))
        phone = "89625505555"
        mobile_auth.start_app_registration(
            phone=phone,
            real_name="Петр",
            gender="male",
            birth_date="1992-02-02",
            address="д. 5",
            locality="Куюки",
        )
        user = BotUser.objects.create(max_user_id="max_kod", chat_id="c")
        reply = mobile_auth.bot_start_app_register(user)
        self.assertIn("телефон", reply.lower())
        reply2 = mobile_auth.bot_issue_register_code(user, phone=phone)
        self.assertIn("Код для завершения регистрации", reply2)

    def test_register_refuses_when_max_already_has_other_phone(self):
        """Нельзя перепривязать занятый Max к новому телефону (семья/данные)."""
        owner = BotUser.objects.create(
            max_user_id="max_owner_832",
            phone="89625507832",
            chat_id="chat_owner",
            real_name="Хозяин",
            profile_status=ProfileStatus.VERIFIED,
        )
        phone_new = "89625507833"
        mobile_auth.start_app_registration(
            phone=phone_new,
            real_name="Новый",
            gender="male",
            birth_date="1995-03-03",
            address="ул. Новая, 2",
            locality="Куюки",
        )
        reply = mobile_auth.bot_issue_register_code(owner, phone=phone_new)
        self.assertIn("уже привязан", reply.lower())
        self.assertNotIn("Код для завершения регистрации", reply)
        owner.refresh_from_db()
        self.assertEqual(owner.phone, "89625507832")
        self.assertEqual(owner.real_name, "Хозяин")

    def test_expired_subscription_still_logs_in_with_needs_payment(self):
        from datetime import timedelta

        from django.utils import timezone

        self.user.subscription_until = timezone.now() - timedelta(days=40)
        self.user.grace_until = timezone.now() - timedelta(days=10)
        self.user.is_active = True
        self.user.save(update_fields=["subscription_until", "grace_until", "is_active"])
        self.user.ensure_grace_period()
        self.assertEqual(self.user.access_state(), "blocked")

        req = self.client.post(
            "/api/v1/auth/phone/login-request",
            data=json.dumps({"phone": "89625507832"}),
            content_type="application/json",
        )
        self.assertEqual(req.status_code, 200)
        code = req.json()["debug_code"]
        verify = self.client.post(
            "/api/v1/auth/phone/login-verify",
            data=json.dumps({"phone": "89625507832", "code": code}),
            content_type="application/json",
        )
        self.assertEqual(verify.status_code, 200)
        body = verify.json()
        self.assertTrue(body["access_token"])
        self.assertTrue(body["needs_payment"])
        self.assertEqual(body["access"]["state"], "blocked")

        access = self.client.get(
            "/api/v1/me/access",
            HTTP_AUTHORIZATION=f"Bearer {body['access_token']}",
        )
        self.assertEqual(access.status_code, 200)
        self.assertTrue(access.json()["needs_payment"])

    def test_deactivated_user_cannot_login(self):
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        req = self.client.post(
            "/api/v1/auth/phone/login-request",
            data=json.dumps({"phone": "89625507832"}),
            content_type="application/json",
        )
        self.assertEqual(req.status_code, 200)
        code = req.json()["debug_code"]
        verify = self.client.post(
            "/api/v1/auth/phone/login-verify",
            data=json.dumps({"phone": "89625507832", "code": code}),
            content_type="application/json",
        )
        self.assertEqual(verify.status_code, 403)
        self.assertEqual(verify.json()["error"], "user_deactivated")

