"""Tests for work request completion, client confirm queue, commission 10%."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.test import Client, TestCase
from django.utils import timezone

from api.models import MobileAuthToken
from database.models import (
    AdminTask,
    AdminTaskKind,
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
    mark_work_done,
    mismatch_topup_amount,
    notify_executor_amount_mismatch,
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

    def _accept_in_progress(self):
        offer = try_dispatch_request(self.req, send_fn=self.capture, use_ai=False)
        accept_offer(offer, send_fn=self.capture)
        self.req.refresh_from_db()
        if self.req.status == WorkRequestStatus.SCHEDULING:
            self.req.status = WorkRequestStatus.IN_PROGRESS
            self.req.agreed_slot = "завтра 10:00–12:00"
            self.req.save(update_fields=["status", "agreed_slot", "updated_at"])
        return self.req

    def _both_mark_done(self):
        self._accept_in_progress()
        mark_work_done(self.client_user, self.req)
        self.req.refresh_from_db()
        return mark_work_done(self.exec_user, self.req)

    def test_accept_message_includes_address(self):
        offer = try_dispatch_request(self.req, send_fn=self.capture, use_ai=False)
        msg = accept_offer(offer, send_fn=self.capture)
        self.assertTrue(msg)
        exec_msgs = [t for u, t in self.sent if "Вы приняли заявку" in t or "заявк" in t.lower()]
        self.assertTrue(exec_msgs)
        joined = "\n".join(exec_msgs).lower()
        self.assertTrue("ленина" in joined or "заявка выполнена" in joined or "согласуйте" in joined)

    def test_both_must_mark_done_before_payment_ask(self):
        self._accept_in_progress()
        pending, _ = PendingAction.objects.get_or_create(user=self.exec_user)
        msg = start_completion(self.exec_user, pending)
        self.assertIn("клиент", msg.lower())
        pending.refresh_from_db()
        self.assertNotEqual(pending.pending_kind, COMPLETE_PENDING)

    def test_cash_flow_queue_commission_block(self):
        self._both_mark_done()
        pending, _ = PendingAction.objects.get_or_create(user=self.exec_user)
        start_completion(self.exec_user, pending, require_both_done=False)
        handle_completion_step(self.exec_user, "наличные", pending)
        reply = handle_completion_step(self.exec_user, "3000", pending)
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, WorkRequestStatus.AWAITING_COMMISSION)
        self.assertEqual(self.req.reported_amount, Decimal("3000.00"))
        self.assertEqual(self.req.commission_amount, Decimal("300.00"))
        self.assertIn("комиссия", reply.lower())
        self.assertTrue(contractor_blocked_for_new_offers(self.contractor))

        sched = ScheduledBotMessage.objects.get(user=self.client_user)
        self.assertIsNone(sched.sent_at)
        self.assertIn("актуализируйте", sched.text.lower())
        sched.send_at = timezone.now() - timedelta(seconds=1)
        sched.save(update_fields=["send_at"])
        n = process_due_scheduled_messages(send_fn=self.capture)
        self.assertEqual(n, 1)
        c_pending = PendingAction.objects.get(user=self.client_user)
        self.assertEqual(c_pending.pending_kind, CLIENT_CONFIRM_PENDING)

        handle_client_confirm_step(self.client_user, "наличные", c_pending)
        handle_client_confirm_step(self.client_user, "3000", c_pending)
        self.req.refresh_from_db()
        self.assertEqual(self.req.confirmed_amount, Decimal("3000.00"))
        self.assertIsNone(self.req.amount_mismatch_due)

        e_pending = PendingAction.objects.get(user=self.exec_user)
        self.assertEqual(e_pending.pending_kind, COMMISSION_PENDING)
        handle_commission_receipt_photo(
            self.exec_user, e_pending, image_bytes=b"pdfbytes", filename="c.pdf"
        )
        self.req.refresh_from_db()
        self.assertEqual(
            self.req.commission_status, WorkRequestCommissionStatus.PENDING_REVIEW
        )

        approve_commission(self.req)
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, WorkRequestStatus.DONE)
        self.assertFalse(contractor_blocked_for_new_offers(self.contractor))
        self.assertEqual(contractor_total_earned(self.contractor), Decimal("2700.00"))

    def test_client_higher_amount_creates_mismatch_task(self):
        self._both_mark_done()
        pending, _ = PendingAction.objects.get_or_create(user=self.exec_user)
        start_completion(self.exec_user, pending, require_both_done=False)
        handle_completion_step(self.exec_user, "наличные", pending)
        handle_completion_step(self.exec_user, "1000", pending)
        c_pending, _ = PendingAction.objects.get_or_create(user=self.client_user)
        c_pending.pending_kind = CLIENT_CONFIRM_PENDING
        c_pending.pending_payload = {"work_request_id": self.req.id, "step": "method"}
        c_pending.save()
        handle_client_confirm_step(self.client_user, "перевод", c_pending)
        handle_client_confirm_step(self.client_user, "10000", c_pending)
        self.req.refresh_from_db()
        self.assertEqual(self.req.confirmed_amount, Decimal("10000.00"))
        self.assertEqual(self.req.amount_mismatch_due, Decimal("900.00"))
        self.assertEqual(mismatch_topup_amount(self.req), Decimal("900.00"))
        task = AdminTask.objects.filter(
            kind=AdminTaskKind.WORK_AMOUNT_MISMATCH, source_id=self.req.id
        ).first()
        self.assertIsNotNone(task)
        body = notify_executor_amount_mismatch(self.req)
        self.assertIn("900", body)
        self.req.refresh_from_db()
        self.assertTrue(self.req.amount_mismatch_message)
        self.assertTrue(contractor_blocked_for_new_offers(self.contractor))

    def test_transfer_asks_receipt(self):
        self._both_mark_done()
        pending, _ = PendingAction.objects.get_or_create(user=self.exec_user)
        start_completion(self.exec_user, pending, require_both_done=False)
        handle_completion_step(self.exec_user, "перевод", pending)
        msg = handle_completion_step(self.exec_user, "1500", pending)
        self.assertIn("чек", msg.lower())
        pending.refresh_from_db()
        self.assertEqual(pending.pending_payload.get("step"), "receipt")

    def test_photo_while_job_open_not_subscription(self):
        self._accept_in_progress()
        # Без отметки клиента фото всё равно начинает отчёт (бот), но оба done
        # выставляем для финализации через complete path с чеком.
        self.req.client_marked_done_at = timezone.now()
        self.req.executor_marked_done_at = timezone.now()
        self.req.save(update_fields=["client_marked_done_at", "executor_marked_done_at", "updated_at"])
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
        self.assertIn("комиссия", reply.lower())
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, WorkRequestStatus.AWAITING_COMMISSION)
        self.assertEqual(self.req.reported_amount, Decimal("2500.00"))
        self.assertTrue(bool(self.req.job_receipt))

    def test_mark_done_api_and_cancel_comments(self):
        self._accept_in_progress()
        http = Client()
        c_tok = MobileAuthToken.objects.create(bot_user=self.client_user)
        e_tok = MobileAuthToken.objects.create(bot_user=self.exec_user)
        r1 = http.post(
            f"/api/v1/work-requests/{self.req.id}/mark-done",
            content_type="application/json",
            data="{}",
            HTTP_AUTHORIZATION=f"Bearer {c_tok.token}",
        )
        self.assertEqual(r1.status_code, 200)
        self.assertFalse(r1.json()["both_done"])
        r2 = http.post(
            f"/api/v1/work-requests/{self.req.id}/mark-done",
            content_type="application/json",
            data="{}",
            HTTP_AUTHORIZATION=f"Bearer {e_tok.token}",
        )
        self.assertEqual(r2.status_code, 200)
        self.assertTrue(r2.json()["both_done"])

        detail = http.get(
            f"/api/v1/work-requests/{self.req.id}",
            HTTP_AUTHORIZATION=f"Bearer {e_tok.token}",
        ).json()
        self.assertTrue(detail["needs_executor_payment_report"])

        pay = http.post(
            f"/api/v1/work-requests/{self.req.id}/report-payment",
            data='{"pay_method":"cash","amount":1200}',
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {e_tok.token}",
        )
        self.assertEqual(pay.status_code, 200)
        self.req.refresh_from_db()
        self.assertEqual(self.req.reported_amount, Decimal("1200.00"))
        self.assertEqual(self.req.commission_amount, Decimal("120.00"))

        # Новая заявка для отмены с комментариями
        req2 = WorkRequest.objects.create(
            user=self.client_user,
            role=self.role,
            description="Отмена",
            client_locality="Куюки",
            status=WorkRequestStatus.IN_PROGRESS,
            assigned_contractor=self.contractor,
            agreed_slot="слот",
        )
        cancel = http.post(
            f"/api/v1/work-requests/{req2.id}/cancel",
            data='{"comment":"Передумал"}',
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {c_tok.token}",
        )
        self.assertEqual(cancel.status_code, 200)
        req2.refresh_from_db()
        self.assertEqual(req2.status, WorkRequestStatus.CANCELLED)
        self.assertEqual(req2.client_cancel_comment, "Передумал")
