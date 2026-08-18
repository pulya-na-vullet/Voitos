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
        self.assertEqual(data["done_count"], 0)
