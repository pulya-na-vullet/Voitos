"""Tests for work request completion, client confirm queue, commission 10%."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

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
    WorkRequestCommissionStatus,
    WorkRequestPayMethod,
    WorkRequestStatus,
)
from services.contractors import contractor_total_earned
from services.work_request_completion import (
    CLIENT_CONFIRM_PENDING,
    COMMISSION_PENDING,
    COMPLETE_PENDING,
    approve_commission,
    contractor_blocked_for_new_offers,
    handle_client_confirm_step,
    handle_commission_receipt_photo,
    handle_completion_step,
    process_due_scheduled_messages,
    route_contractor_receipt_photo,
    start_completion,
)
from services.work_request_dispatch import accept_offer, try_dispatch_request


class WorkRequestCompletionFlowTests(TestCase):
    def setUp(self):
        self.role = ExecutorRole.objects.create(code="r_e", name="Электрик", is_active=True)
        self.client_user = BotUser.objects.create(
            max_user_id="cl1",
            real_name="ИТ-МАСТЕРСКАЯ",
            locality="Куюки",
            address="ул. Ленина, д. 1",
            phone="89188028767",
        )
        self.exec_user = BotUser.objects.create(
            max_user_id="ex1", real_name="Дмитрий", locality="Куюки", phone="89625507832"
        )
        self.contractor = ContractorProfile.objects.create(
            user=self.exec_user,
            role=self.role,
            equipment_type=self.role.code,
            locality="Куюки",
            status=ContractorStatus.VERIFIED,
        )
        self.sent: list[tuple[str, str]] = []

        def capture(user, text):
            self.sent.append((str(user), text))

        self.capture = capture
        self.req = WorkRequest.objects.create(
            user=self.client_user,
            role=self.role,
            description="Нужно починить розетку",
            client_locality="Куюки",
            status=WorkRequestStatus.PENDING,
        )

    def test_accept_message_includes_address(self):
        offer = try_dispatch_request(self.req, send_fn=self.capture, use_ai=False)
        msg = accept_offer(offer, send_fn=self.capture)
        self.assertIn("закреплена", msg.lower())
        exec_msgs = [t for u, t in self.sent if "Вы приняли заявку" in t]
        self.assertTrue(exec_msgs)
        self.assertIn("ул. Ленина", exec_msgs[-1])
        self.assertIn("заявка выполнена", exec_msgs[-1].lower())

    def test_cash_flow_queue_commission_block(self):
        offer = try_dispatch_request(self.req, send_fn=self.capture, use_ai=False)
        accept_offer(offer, send_fn=self.capture)
        pending, _ = PendingAction.objects.get_or_create(user=self.exec_user)
        start_completion(self.exec_user, pending)
        handle_completion_step(self.exec_user, "наличные", pending)
        handle_completion_step(self.exec_user, "3000", pending)
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, WorkRequestStatus.AWAITING_CLIENT)
        self.assertEqual(self.req.reported_amount, Decimal("3000.00"))
        self.assertTrue(contractor_blocked_for_new_offers(self.contractor))

        sched = ScheduledBotMessage.objects.get(user=self.client_user)
        self.assertIsNone(sched.sent_at)
        sched.send_at = timezone.now() - timedelta(seconds=1)
        sched.save(update_fields=["send_at"])
        n = process_due_scheduled_messages(send_fn=self.capture)
        self.assertEqual(n, 1)
        c_pending = PendingAction.objects.get(user=self.client_user)
        self.assertEqual(c_pending.pending_kind, CLIENT_CONFIRM_PENDING)

        handle_client_confirm_step(self.client_user, "да", c_pending)
        self.req.refresh_from_db()
        self.assertEqual(self.req.confirmed_amount, Decimal("3000.00"))
        self.assertEqual(self.req.commission_amount, Decimal("300.00"))
        self.assertEqual(self.req.executor_earned_amount, Decimal("2700.00"))
        self.assertEqual(self.req.status, WorkRequestStatus.AWAITING_COMMISSION)

        e_pending = PendingAction.objects.get(user=self.exec_user)
        self.assertEqual(e_pending.pending_kind, COMMISSION_PENDING)
        handle_commission_receipt_photo(
            self.exec_user, e_pending, image_bytes=b"pdfbytes", filename="c.pdf"
        )
        self.req.refresh_from_db()
        self.assertEqual(
            self.req.commission_status, WorkRequestCommissionStatus.PENDING_REVIEW
        )
        self.assertTrue(contractor_blocked_for_new_offers(self.contractor))

        approve_commission(self.req)
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, WorkRequestStatus.DONE)
        self.assertFalse(contractor_blocked_for_new_offers(self.contractor))
        self.assertEqual(contractor_total_earned(self.contractor), Decimal("2700.00"))

    def test_transfer_asks_receipt(self):
        offer = try_dispatch_request(self.req, send_fn=self.capture, use_ai=False)
        accept_offer(offer, send_fn=self.capture)
        pending, _ = PendingAction.objects.get_or_create(user=self.exec_user)
        start_completion(self.exec_user, pending)
        handle_completion_step(self.exec_user, "перевод", pending)
        msg = handle_completion_step(self.exec_user, "1500", pending)
        self.assertIn("чек", msg.lower())
        pending.refresh_from_db()
        self.assertEqual(pending.pending_payload.get("step"), "receipt")

    def test_photo_while_job_open_not_subscription(self):
        offer = try_dispatch_request(self.req, send_fn=self.capture, use_ai=False)
        accept_offer(offer, send_fn=self.capture)
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, WorkRequestStatus.IN_PROGRESS)
        pending, _ = PendingAction.objects.get_or_create(user=self.exec_user)
        pending.clear_pending()
        msg = route_contractor_receipt_photo(
            self.exec_user,
            pending,
            image_bytes=b"fake-job-receipt",
            filename="job.jpg",
        )
        self.assertIsNotNone(msg)
        self.assertIn("не как оплата подписки", msg.lower())
        self.assertIn("перевод", msg.lower())
        pending.refresh_from_db()
        self.assertEqual(pending.pending_kind, COMPLETE_PENDING)
        self.assertTrue(pending.pending_payload.get("receipt_b64"))

        handle_completion_step(self.exec_user, "1", pending)
        reply = handle_completion_step(self.exec_user, "2500", pending)
        self.assertIn("принят", reply.lower())
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, WorkRequestStatus.AWAITING_CLIENT)
        self.assertEqual(self.req.reported_amount, Decimal("2500.00"))
        self.assertTrue(bool(self.req.job_receipt))

    def test_photo_blocked_while_awaiting_client(self):
        offer = try_dispatch_request(self.req, send_fn=self.capture, use_ai=False)
        accept_offer(offer, send_fn=self.capture)
        pending, _ = PendingAction.objects.get_or_create(user=self.exec_user)
        start_completion(self.exec_user, pending)
        handle_completion_step(self.exec_user, "наличные", pending)
        handle_completion_step(self.exec_user, "1000", pending)
        pending.clear_pending()
        msg = route_contractor_receipt_photo(
            self.exec_user, pending, image_bytes=b"x", filename="x.jpg"
        )
        self.assertIsNotNone(msg)
        self.assertIn("не принимаем", msg.lower())
        self.assertNotEqual(pending.pending_kind, COMPLETE_PENDING)
