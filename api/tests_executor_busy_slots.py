"""Tests for executor personal busy slots (tractor / truck / PC master)."""

from __future__ import annotations

import json
from datetime import timedelta

from django.test import Client, TestCase
from django.utils import timezone as dj_tz

from api.models import MobileAuthToken
from database.models import (
    BotUser,
    ContractorProfile,
    ContractorStatus,
    ExecutorBusySlot,
    ExecutorRole,
    ProfileStatus,
)
from services.master_booking import (
    busy_intervals_for_user,
    user_can_manage_busy_slots,
)
from services.work_request_schedule import format_slot_label


class ExecutorBusySlotApiTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.role_tractor = ExecutorRole.objects.create(
            code="tractor",
            name="Тракторист",
            is_active=True,
            client_books_master=False,
        )
        self.role_plumber = ExecutorRole.objects.create(
            code="plumber",
            name="Сантехник",
            is_active=True,
            client_books_master=True,
        )
        self.tractor_user = BotUser.objects.create(
            max_user_id="busy-tr",
            phone="89625501001",
            real_name="Тракторист",
            profile_status=ProfileStatus.VERIFIED,
            locality="Куюки",
        )
        self.plumber_user = BotUser.objects.create(
            max_user_id="busy-pl",
            phone="89625501002",
            real_name="Сантехник",
            profile_status=ProfileStatus.VERIFIED,
            locality="Куюки",
        )
        self.tractor = ContractorProfile.objects.create(
            user=self.tractor_user,
            role=self.role_tractor,
            equipment_type="tractor",
            locality="Куюки",
            status=ContractorStatus.VERIFIED,
        )
        self.plumber = ContractorProfile.objects.create(
            user=self.plumber_user,
            role=self.role_plumber,
            equipment_type="plumber",
            locality="Куюки",
            status=ContractorStatus.VERIFIED,
        )
        self.tok_tr = MobileAuthToken.objects.create(bot_user=self.tractor_user)
        self.tok_pl = MobileAuthToken.objects.create(bot_user=self.plumber_user)
        self.auth_tr = {"HTTP_AUTHORIZATION": f"Bearer {self.tok_tr.token}"}
        self.auth_pl = {"HTTP_AUTHORIZATION": f"Bearer {self.tok_pl.token}"}

        now = dj_tz.localtime(dj_tz.now())
        monday = (now - timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        self.week_start = monday.date()
        self.visit_day = monday + timedelta(days=2)
        self.slot_label = format_slot_label(
            self.visit_day.replace(hour=10, minute=0),
            self.visit_day.replace(hour=14, minute=0),
        )

    def test_tractor_can_add_and_list_busy(self):
        self.assertTrue(user_can_manage_busy_slots(self.tractor_user))
        resp = self.client.post(
            "/api/v1/executor/schedule/busy",
            data=json.dumps({"label": self.slot_label, "note": "Частный заказ"}),
            content_type="application/json",
            **self.auth_tr,
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        body = resp.json()
        self.assertTrue(body["ok"])
        busy_id = body["item"]["busy_id"]
        self.assertGreater(busy_id, 0)
        self.assertEqual(body["item"]["kind"], "busy")

        sched = self.client.get(
            f"/api/v1/executor/schedule?week_start={self.week_start.isoformat()}",
            **self.auth_tr,
        )
        self.assertEqual(sched.status_code, 200)
        sbody = sched.json()
        self.assertTrue(sbody["can_add_busy"])
        kinds = {i["kind"] for i in sbody["items"]}
        self.assertIn("busy", kinds)
        busy_item = next(i for i in sbody["items"] if i["kind"] == "busy")
        self.assertEqual(busy_item["busy_id"], busy_id)
        self.assertIn("Частный", busy_item["note"])

        # Occupies calendar for free-slot / capacity logic.
        from services.master_booking import slot_overlaps_user_busy

        start = self.visit_day.replace(hour=10, minute=0)
        end = self.visit_day.replace(hour=14, minute=0)
        self.assertTrue(
            slot_overlaps_user_busy(self.tractor_user.id, start, end)
        )
        busy = busy_intervals_for_user(
            self.tractor_user.id,
            range_start=self.visit_day.replace(hour=0),
            range_end=self.visit_day.replace(hour=23, minute=59),
        )
        self.assertTrue(busy)

        del_resp = self.client.delete(
            f"/api/v1/executor/schedule/busy/{busy_id}",
            **self.auth_tr,
        )
        self.assertEqual(del_resp.status_code, 200, del_resp.content)
        self.assertFalse(ExecutorBusySlot.objects.filter(pk=busy_id).exists())
        self.assertFalse(
            slot_overlaps_user_busy(self.tractor_user.id, start, end)
        )

    def test_plumber_cannot_add_busy(self):
        self.assertFalse(user_can_manage_busy_slots(self.plumber_user))
        resp = self.client.post(
            "/api/v1/executor/schedule/busy",
            data=json.dumps({"label": self.slot_label}),
            content_type="application/json",
            **self.auth_pl,
        )
        self.assertEqual(resp.status_code, 400)
        sched = self.client.get(
            f"/api/v1/executor/schedule?week_start={self.week_start.isoformat()}",
            **self.auth_pl,
        )
        self.assertEqual(sched.status_code, 200)
        self.assertFalse(sched.json()["can_add_busy"])

    def test_computer_master_allowed(self):
        role = ExecutorRole.objects.create(
            code="computer_master",
            name="Компьютерный мастер",
            is_active=True,
        )
        user = BotUser.objects.create(
            max_user_id="busy-cm",
            phone="89625501003",
            real_name="Мастер ПК",
            profile_status=ProfileStatus.VERIFIED,
        )
        ContractorProfile.objects.create(
            user=user,
            role=role,
            equipment_type="computer_master",
            status=ContractorStatus.VERIFIED,
        )
        self.assertTrue(user_can_manage_busy_slots(user))
