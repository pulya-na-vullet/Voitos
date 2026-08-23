"""Tests for at-home scheduling flow."""

from __future__ import annotations

from django.test import TestCase

from database.models import (
    BotUser,
    ContractorProfile,
    ContractorStatus,
    ExecutorRole,
    PendingAction,
    WorkRequest,
    WorkRequestOffer,
    WorkRequestOfferStatus,
    WorkRequestStatus,
)
from services.work_request_dispatch import accept_offer
from services.work_request_schedule import (
    handle_client_schedule_step,
    handle_master_schedule_step,
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

    def test_accept_starts_scheduling(self):
        from unittest.mock import patch

        with patch(
            "services.work_request_schedule._send", return_value=self.capture
        ), patch(
            "services.work_request_dispatch._default_send_fn",
            return_value=self.capture,
        ):
            msg = accept_offer(self.offer, send_fn=self.capture)
            self.req.refresh_from_db()
            self.assertEqual(self.req.status, WorkRequestStatus.SCHEDULING)
            self.assertIn("согласуйте", msg.lower())
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
            self.assertGreaterEqual(len(self.sent), 3)

    def test_master_visit_address_no_duplicate_locality(self):
        from services.work_request_schedule import master_visit_address

        self.req.assigned_contractor = self.contractor
        self.exec_user.address = "Куюки, ул. Салонная, 5"
        self.exec_user.save(update_fields=["address"])
        addr = master_visit_address(self.req)
        self.assertEqual(addr.count("Куюки"), 1)
        self.assertIn("ул. Салонная", addr)

    def test_publish_slots_from_app_and_block_empty_confirm(self):
        from unittest.mock import patch

        from services.work_request_schedule import (
            confirm_slot_for_client,
            publish_slots_for_master,
        )

        with patch(
            "services.work_request_schedule._send", return_value=self.capture
        ), patch(
            "services.work_request_dispatch._default_send_fn",
            return_value=self.capture,
        ):
            accept_offer(self.offer, send_fn=self.capture)
            self.req.refresh_from_db()
            with self.assertRaises(ValueError):
                confirm_slot_for_client(self.req, slot="")
            out = publish_slots_for_master(
                self.req,
                self.exec_user,
                ["завтра 10:00-12:00", "завтра 14:00-16:00"],
            )
            self.assertIn("отправлены", out.lower())
            self.req.refresh_from_db()
            self.assertEqual(len(self.req.proposed_slots), 2)
            confirm_slot_for_client(self.req, slot="завтра 10:00-12:00")
            self.req.refresh_from_db()
            self.assertEqual(self.req.status, WorkRequestStatus.IN_PROGRESS)
            self.assertEqual(self.req.agreed_slot, "завтра 10:00-12:00")
            # Клиенту ушли контакты мастера
            client_msgs = [t for u, t in self.sent if "Житель" in u or u == "Житель"]
            joined = "\n".join(client_msgs)
            self.assertTrue(
                any("Телефон" in t or "контакты" in t.lower() for _, t in self.sent)
                or "Мастер" in joined
            )
