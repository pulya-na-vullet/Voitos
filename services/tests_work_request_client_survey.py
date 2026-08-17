"""Tests: panel → awaiting_client starts MAX service survey."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from database.models import (
    BotUser,
    ContractorProfile,
    ContractorStatus,
    ExecutorRole,
    PanelProfile,
    PanelRole,
    PendingAction,
    WorkRequest,
    WorkRequestStatus,
)
from services.work_request_client_survey import (
    handle_service_survey_step,
    start_client_service_survey,
)


class ClientServiceSurveyTests(TestCase):
    def setUp(self):
        self.role = ExecutorRole.objects.create(
            code="sv_role", name="Сантехник", is_active=True
        )
        self.client_user = BotUser.objects.create(
            max_user_id="sv-cl", real_name="Клиент", chat_id="111"
        )
        self.exec_user = BotUser.objects.create(
            max_user_id="sv-ex", real_name="Мастер", chat_id="222"
        )
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
            status=WorkRequestStatus.IN_PROGRESS,
            assigned_contractor=self.contractor,
        )
        self.sent = []

        def capture(user, text):
            self.sent.append((str(user), text))

        self.capture = capture

        self.admin = User.objects.create_superuser("svadmin", "sv@t.com", "pass")
        PanelProfile.objects.create(user=self.admin, role=PanelRole.ADMIN)

    def test_start_survey_sends_max_and_sets_pending(self):
        self.req.status = WorkRequestStatus.AWAITING_CLIENT
        self.req.save(update_fields=["status"])
        ok, detail = start_client_service_survey(self.req, send_fn=self.capture)
        self.assertTrue(ok)
        self.assertIn("отправлен", detail.lower())
        self.assertTrue(any("Услуга была оказана" in t for _, t in self.sent))
        pending = PendingAction.objects.get(user=self.client_user)
        self.assertEqual(pending.pending_kind, "work_request_service_survey")

    def test_yes_without_reported_asks_amount(self):
        self.req.status = WorkRequestStatus.AWAITING_CLIENT
        self.req.save(update_fields=["status"])
        pending, _ = PendingAction.objects.get_or_create(user=self.client_user)
        pending.pending_kind = "work_request_service_survey"
        pending.pending_payload = {"work_request_id": self.req.id}
        pending.save()
        out = handle_service_survey_step(self.client_user, "да", pending)
        self.assertIn("сумму", out.lower())
        pending.refresh_from_db()
        self.assertEqual(pending.pending_kind, "work_request_client_confirm")
        self.assertEqual(pending.pending_payload.get("step"), "amount_only")

    def test_yes_with_reported_asks_confirm(self):
        self.req.status = WorkRequestStatus.AWAITING_CLIENT
        self.req.reported_amount = Decimal("1500.00")
        self.req.save(update_fields=["status", "reported_amount"])
        pending, _ = PendingAction.objects.get_or_create(user=self.client_user)
        pending.pending_kind = "work_request_service_survey"
        pending.pending_payload = {"work_request_id": self.req.id}
        pending.save()
        out = handle_service_survey_step(self.client_user, "да", pending)
        self.assertIn("1500", out)
        pending.refresh_from_db()
        self.assertEqual(pending.pending_kind, "work_request_client_confirm")

    def test_no_cancels_and_asks_service_rating(self):
        self.req.status = WorkRequestStatus.AWAITING_CLIENT
        self.req.save(update_fields=["status"])
        pending, _ = PendingAction.objects.get_or_create(user=self.client_user)
        pending.pending_kind = "work_request_service_survey"
        pending.pending_payload = {"work_request_id": self.req.id}
        pending.save()
        with patch(
            "services.work_request_client_survey._send_or_raise",
            side_effect=lambda u, t, send_fn=None: self.capture(u, t),
        ):
            out = handle_service_survey_step(self.client_user, "нет", pending)
        self.assertIn("не оказана", out.lower())
        self.assertIn("качеств", out.lower())
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, WorkRequestStatus.CANCELLED)
        pending.refresh_from_db()
        self.assertEqual(pending.pending_kind, "work_request_rating")
        self.assertFalse(pending.pending_payload.get("service_provided", True))

    def test_panel_status_change_triggers_survey(self):
        c = Client()
        self.assertTrue(c.login(username="svadmin", password="pass"))
        with patch(
            "services.work_request_client_survey._send_or_raise",
            side_effect=lambda u, t, send_fn=None: self.capture(u, t),
        ):
            resp = c.post(
                reverse("panel:work_request_detail", args=[self.req.id]),
                {
                    "action": "save",
                    "status": WorkRequestStatus.AWAITING_CLIENT,
                    "note": "",
                    "client_locality": "",
                },
            )
        self.assertEqual(resp.status_code, 302)
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, WorkRequestStatus.AWAITING_CLIENT)
        self.assertTrue(any("Услуга была оказана" in t for _, t in self.sent))
        pending = PendingAction.objects.get(user=self.client_user)
        self.assertEqual(pending.pending_kind, "work_request_service_survey")

    def test_panel_resend_survey_button(self):
        self.req.status = WorkRequestStatus.AWAITING_CLIENT
        self.req.save(update_fields=["status"])
        c = Client()
        self.assertTrue(c.login(username="svadmin", password="pass"))
        with patch(
            "services.work_request_client_survey._send_or_raise",
            side_effect=lambda u, t, send_fn=None: self.capture(u, t),
        ):
            resp = c.post(
                reverse("panel:work_request_detail", args=[self.req.id]),
                {"action": "send_client_survey"},
            )
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(any("Услуга была оказана" in t for _, t in self.sent))
