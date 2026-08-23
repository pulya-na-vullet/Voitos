"""Tests for executor weekly schedule API and slot parsing."""

from __future__ import annotations

from datetime import timedelta

from django.test import Client, TestCase
from django.utils import timezone as dj_tz

from api.models import MobileAuthToken
from database.models import (
    BotUser,
    ContractorProfile,
    ContractorStatus,
    ExecutorRole,
    ProfileStatus,
    WorkRequest,
    WorkRequestStatus,
)
from services.work_request_schedule import format_slot_label, parse_slot_datetime_range


class SlotParseTests(TestCase):
    def test_parse_full_year_slot(self):
        now = dj_tz.localtime(dj_tz.now()).replace(
            year=2026, month=8, day=20, hour=12, minute=0, second=0, microsecond=0
        )
        parsed = parse_slot_datetime_range("23.08.2026 10:00–12:00", ref_now=now)
        self.assertIsNotNone(parsed)
        start, end = parsed
        self.assertEqual(start.day, 23)
        self.assertEqual(start.hour, 10)
        self.assertEqual(end.hour, 12)
        self.assertEqual(format_slot_label(start, end), "23.08.2026 10:00–12:00")

    def test_parse_rejects_garbage(self):
        self.assertIsNone(parse_slot_datetime_range("завтра утром"))


class ExecutorScheduleApiTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.role = ExecutorRole.objects.create(
            code="r_sched",
            name="Сантехник",
            is_active=True,
        )
        self.client_user = BotUser.objects.create(
            max_user_id="sch-cl",
            phone="89625500001",
            real_name="Клиент",
            profile_status=ProfileStatus.VERIFIED,
        )
        self.exec_user = BotUser.objects.create(
            max_user_id="sch-ex",
            phone="89625500002",
            real_name="Мастер",
            profile_status=ProfileStatus.VERIFIED,
            locality="Куюки",
        )
        self.contractor = ContractorProfile.objects.create(
            user=self.exec_user,
            role=self.role,
            equipment_type=self.role.code,
            locality="Куюки",
            status=ContractorStatus.VERIFIED,
        )
        self.tok = MobileAuthToken.objects.create(bot_user=self.exec_user)
        self.auth = {"HTTP_AUTHORIZATION": f"Bearer {self.tok.token}"}

        now = dj_tz.localtime(dj_tz.now())
        monday = (now - timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        self.week_start = monday.date()
        visit_day = monday + timedelta(days=2)  # среда
        self.slot_label = format_slot_label(
            visit_day.replace(hour=10, minute=0),
            visit_day.replace(hour=12, minute=0),
        )
        self.wr = WorkRequest.objects.create(
            user=self.client_user,
            role=self.role,
            description="Протечка",
            status=WorkRequestStatus.IN_PROGRESS,
            assigned_contractor=self.contractor,
            agreed_slot=self.slot_label,
        )

    def test_schedule_lists_agreed_visit(self):
        resp = self.client.get(
            f"/api/v1/executor/schedule?week_start={self.week_start.isoformat()}",
            **self.auth,
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["week_start"], self.week_start.isoformat())
        self.assertEqual(len(body["items"]), 1)
        item = body["items"][0]
        self.assertEqual(item["work_request_id"], self.wr.id)
        self.assertEqual(item["label"], self.slot_label)
        self.assertEqual(item["client_name"], "Клиент")

    def test_executor_can_open_assigned_detail(self):
        resp = self.client.get(f"/api/v1/work-requests/{self.wr.id}", **self.auth)
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["viewer"], "executor")
        self.assertEqual(body["client_name"], "Клиент")
        self.assertFalse(body["can_confirm_slot"])
        self.assertFalse(body["needs_confirm_amount"])
        self.assertFalse(body["needs_rating"])

    def test_other_executor_cannot_see(self):
        other = BotUser.objects.create(
            max_user_id="sch-other",
            phone="89625500003",
            real_name="Чужой",
            profile_status=ProfileStatus.VERIFIED,
        )
        tok = MobileAuthToken.objects.create(bot_user=other)
        resp = self.client.get(
            f"/api/v1/work-requests/{self.wr.id}",
            HTTP_AUTHORIZATION=f"Bearer {tok.token}",
        )
        self.assertEqual(resp.status_code, 404)
