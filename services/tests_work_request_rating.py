"""Tests for work request ratings and default list filter."""

from __future__ import annotations

from decimal import Decimal

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.utils import timezone

from database.models import (
    BotUser,
    ContractorProfile,
    ContractorStatus,
    ExecutorRole,
    PanelProfile,
    PanelRole,
    PendingAction,
    WorkRequest,
    WorkRequestCommissionStatus,
    WorkRequestRating,
    WorkRequestStatus,
)
from services.work_request_rating import (
    ask_client_for_rating,
    backfill_rating_asks,
    handle_rating_step,
    parse_score,
)


class WorkRequestRatingTests(TestCase):
    def setUp(self):
        self.role = ExecutorRole.objects.create(code="r_rate", name="Сантехник", is_active=True)
        self.client_user = BotUser.objects.create(max_user_id="rt-cl", real_name="Клиент")
        self.exec_user = BotUser.objects.create(max_user_id="rt-ex", real_name="Мастер")
        self.contractor = ContractorProfile.objects.create(
            user=self.exec_user,
            role=self.role,
            equipment_type=self.role.code,
            status=ContractorStatus.VERIFIED,
        )
        self.req = WorkRequest.objects.create(
            user=self.client_user,
            role=self.role,
            description="кран",
            assigned_contractor=self.contractor,
            confirmed_amount=Decimal("1000.00"),
            commission_amount=Decimal("100.00"),
            executor_earned_amount=Decimal("900.00"),
            commission_status=WorkRequestCommissionStatus.APPROVED,
            status=WorkRequestStatus.DONE,
            client_confirmed_at=timezone.now(),
        )
        self.sent = []

        def capture(user, text):
            self.sent.append((str(user), text))

        self.capture = capture

    def test_parse_score(self):
        self.assertEqual(parse_score("5"), 5)
        self.assertEqual(parse_score("оценка 4"), 4)
        self.assertIsNone(parse_score("0"))
        self.assertIsNone(parse_score("отлично"))

    def test_ask_and_save_rating(self):
        ok = ask_client_for_rating(self.req, send_fn=self.capture)
        self.assertTrue(ok)
        self.req.refresh_from_db()
        self.assertIsNotNone(self.req.rating_asked_at)
        pending = PendingAction.objects.get(user=self.client_user)
        self.assertEqual(pending.pending_kind, "work_request_rating")
        msg = handle_rating_step(self.client_user, "5", pending)
        self.assertIn("5/5", msg)
        msg2 = handle_rating_step(self.client_user, "Всё супер", pending)
        self.assertIn("Спасибо", msg2)
        rating = WorkRequestRating.objects.get(work_request=self.req)
        self.assertEqual(rating.score, 5)
        self.assertEqual(rating.comment, "Всё супер")
        self.assertEqual(rating.contractor_id, self.contractor.id)

    def test_backfill_asks_closed(self):
        n = backfill_rating_asks(send_fn=self.capture, limit=10)
        self.assertEqual(n, 1)
        self.assertTrue(any("Оцените" in t for _, t in self.sent))

    def test_work_requests_default_hides_done(self):
        admin = User.objects.create_user("rtadm", password="pass")
        PanelProfile.objects.create(user=admin, role=PanelRole.ADMIN)
        open_req = WorkRequest.objects.create(
            user=self.client_user,
            role=self.role,
            description="открытая",
            status=WorkRequestStatus.PENDING,
        )
        cancelled = WorkRequest.objects.create(
            user=self.client_user,
            role=self.role,
            description="отменённая-заявка-xyz",
            status=WorkRequestStatus.CANCELLED,
        )
        c = Client()
        c.login(username="rtadm", password="pass")
        resp = c.get("/panel/work-requests/")
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        self.assertIn(str(open_req.id), body)
        # done + cancelled should not appear by default
        self.assertNotIn(self.req.description, body)
        self.assertNotIn(cancelled.description, body)
        resp_all = c.get("/panel/work-requests/?status=all")
        all_body = resp_all.content.decode()
        self.assertIn(self.req.description, all_body)
        self.assertIn(cancelled.description, all_body)

    def test_admin_can_delete_work_request(self):
        admin = User.objects.create_user("rtdel", password="pass")
        PanelProfile.objects.create(user=admin, role=PanelRole.ADMIN)
        c = Client()
        c.login(username="rtdel", password="pass")
        rid = self.req.id
        resp = c.post(
            "/panel/work-requests/",
            {"action": "delete", "request_id": str(rid)},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(WorkRequest.objects.filter(pk=rid).exists())
