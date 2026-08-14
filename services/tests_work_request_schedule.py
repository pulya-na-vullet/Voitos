"""Tests for at-home scheduling flow."""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from database.models import (
    BotUser,
    ContractorProfile,
    ContractorStatus,
    ExecutorRole,
    PendingAction,
    ScheduledBotMessage,
    WorkRequest,
    WorkRequestOffer,
    WorkRequestOfferStatus,
    WorkRequestStatus,
)
from services.work_request_completion import process_due_scheduled_messages
from services.work_request_dispatch import accept_offer
from services.work_request_schedule import (
    handle_client_schedule_step,
    handle_master_schedule_step,
    handle_service_done_step,
    parse_slot_end_at,
    resolve_slot_end_at,
)


class HomeSchedulingTests(TestCase):
    def setUp(self):
        self.role = ExecutorRole.objects.create(
            code="r_home",
            name="Парикмахер",
            is_active=True,
            accepts_at_home=True,
        )
        self.client_user = BotUser.objects.create(
            max_user_id="hm-cl", real_name="Житель", address="ул. Мира, 1"
        )
        self.exec_user = BotUser.objects.create(
            max_user_id="hm-ex",
            real_name="Мастер Анна",
            address="ул. Салонная, 5",
            locality="Куюки",
        )
        self.contractor = ContractorProfile.objects.create(
            user=self.exec_user,
            role=self.role,
            equipment_type=self.role.code,
            locality="Куюки",
            status=ContractorStatus.VERIFIED,
        )
        self.req = WorkRequest.objects.create(
            user=self.client_user,
            role=self.role,
            description="стрижка",
            client_locality="Куюки",
            status=WorkRequestStatus.OFFERING,
        )
        self.offer = WorkRequestOffer.objects.create(
            work_request=self.req,
            contractor=self.contractor,
            status=WorkRequestOfferStatus.OFFERED,
        )
        self.sent = []

        def capture(user, text):
            self.sent.append((str(user), text))

        self.capture = capture

    def test_parse_slot_end_range(self):
        base = timezone.make_aware(datetime(2026, 3, 10, 9, 0))
        end = parse_slot_end_at("15.03 10:00–12:00", base=base)
        self.assertIsNotNone(end)
        local = timezone.localtime(end)
        self.assertEqual(local.day, 15)
        self.assertEqual(local.month, 3)
        self.assertEqual(local.hour, 12)
        self.assertEqual(local.minute, 0)

    def test_resolve_slot_end_fallback_two_hours(self):
        agreed = timezone.make_aware(datetime(2026, 3, 10, 9, 0))
        end = resolve_slot_end_at("без времени", agreed_at=agreed)
        self.assertEqual(end, agreed + timedelta(hours=2))

    def test_accept_starts_scheduling(self):
        with patch(
            "services.work_request_schedule._send", return_value=self.capture
        ), patch(
            "services.work_request_dispatch._default_send_fn",
            return_value=self.capture,
        ):
            msg = accept_offer(self.offer, send_fn=self.capture)
            self.req.refresh_from_db()
            self.assertEqual(self.req.status, WorkRequestStatus.SCHEDULING)
            self.assertIn("окна", msg.lower())
            pending = PendingAction.objects.get(user=self.exec_user)
            self.assertEqual(pending.pending_kind, "work_request_schedule_master")

            handle_master_schedule_step(self.exec_user, "завтра 10:00-12:00", pending)
            handle_master_schedule_step(self.exec_user, "завтра 14:00-16:00", pending)
            out = handle_master_schedule_step(self.exec_user, "готово", pending)
            self.assertIn("отправлены", out.lower())
            self.req.refresh_from_db()
            self.assertEqual(len(self.req.proposed_slots), 2)

            c_pending = PendingAction.objects.get(user=self.client_user)
            self.assertEqual(c_pending.pending_kind, "work_request_schedule_client")
            conf = handle_client_schedule_step(self.client_user, "1", c_pending)
            self.assertIn("Зафиксировали", conf)
            self.req.refresh_from_db()
            self.assertEqual(self.req.status, WorkRequestStatus.IN_PROGRESS)
            self.assertTrue(self.req.agreed_slot)
            self.assertIsNotNone(self.req.agreed_slot_end_at)
            self.assertGreaterEqual(len(self.sent), 3)

            ask = ScheduledBotMessage.objects.filter(
                user=self.client_user,
                kind="work_request_service_done_ask",
                cancelled_at__isnull=True,
            ).first()
            self.assertIsNotNone(ask)
            self.assertEqual(ask.meta.get("work_request_id"), self.req.id)

    def test_service_done_yes_starts_master_completion(self):
        self.req.assigned_contractor = self.contractor
        self.req.status = WorkRequestStatus.IN_PROGRESS
        self.req.agreed_slot = "завтра 10:00-12:00"
        self.req.agreed_slot_end_at = timezone.now() - timedelta(minutes=1)
        self.req.save()

        msg = ScheduledBotMessage.objects.create(
            user=self.client_user,
            kind="work_request_service_done_ask",
            text="Услуга оказана?",
            send_at=timezone.now() - timedelta(seconds=5),
            meta={"work_request_id": self.req.id},
        )
        with patch(
            "services.work_request_dispatch._default_send_fn",
            return_value=self.capture,
        ):
            n = process_due_scheduled_messages(send_fn=self.capture)
        self.assertEqual(n, 1)
        msg.refresh_from_db()
        self.assertIsNotNone(msg.sent_at)
        self.req.refresh_from_db()
        self.assertIsNotNone(self.req.service_done_asked_at)

        c_pending = PendingAction.objects.get(user=self.client_user)
        self.assertEqual(c_pending.pending_kind, "work_request_service_done")

        with patch(
            "services.work_request_schedule._send", return_value=self.capture
        ):
            out = handle_service_done_step(self.client_user, "да", c_pending)
        self.assertIn("Спасибо", out)
        self.req.refresh_from_db()
        self.assertIsNotNone(self.req.service_provided_at)
        m_pending = PendingAction.objects.get(user=self.exec_user)
        self.assertEqual(m_pending.pending_kind, "work_request_complete")
        self.assertEqual(m_pending.pending_payload.get("step"), "method")
        self.assertTrue(any("подтвердил" in t.lower() for _, t in self.sent))

    def test_service_done_no_notifies_master(self):
        self.req.assigned_contractor = self.contractor
        self.req.status = WorkRequestStatus.IN_PROGRESS
        self.req.save()
        pending, _ = PendingAction.objects.get_or_create(user=self.client_user)
        pending.pending_kind = "work_request_service_done"
        pending.pending_payload = {"work_request_id": self.req.id}
        pending.save()

        with patch(
            "services.work_request_schedule._send", return_value=self.capture
        ):
            out = handle_service_done_step(self.client_user, "нет", pending)
        self.assertIn("Понял", out)
        pending.refresh_from_db()
        self.assertFalse(pending.pending_kind)
        self.assertTrue(any("не оказана" in t.lower() for _, t in self.sent))
