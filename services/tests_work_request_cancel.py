"""Cancel work-request dialog + generic «вызвать мастера» role list."""

from __future__ import annotations

from django.test import TestCase

from bot.work_request import handle_work_request_step, start_work_request
from database.models import (
    BotUser,
    ExecutorRole,
    PendingAction,
    WorkRequest,
    WorkRequestStatus,
)
from services.executor_roles import extract_role_from_call_phrase, match_role_from_text
from services.work_request_cancel import is_cancel_message, maybe_cancel_work_flow


class RolePickAndCancelTests(TestCase):
    def setUp(self):
        self.comp = ExecutorRole.objects.create(
            code="r_comp", name="компьютерный мастер", is_active=True
        )
        self.elec = ExecutorRole.objects.create(
            code="r_elec", name="Электрик", is_active=True
        )
        self.user = BotUser.objects.create(max_user_id="cx1", real_name="Клиент")
        self.pending, _ = PendingAction.objects.get_or_create(user=self.user)

    def test_call_master_shows_role_list(self):
        self.assertIsNone(extract_role_from_call_phrase("вызвать мастера"))
        self.assertIsNone(match_role_from_text("мастера"))
        msg = start_work_request(self.user, self.pending, text="вызвать мастера")
        self.assertIn("Кого вызвать", msg)
        self.assertIn("1.", msg)
        self.assertIn("компьютерный мастер", msg)
        self.assertIn("Электрик", msg)
        self.assertEqual(self.pending.pending_payload.get("step"), "role")

    def test_specific_role_still_matches(self):
        self.assertEqual(
            extract_role_from_call_phrase("нужен электрик").id, self.elec.id
        )
        self.assertEqual(
            match_role_from_text("компьютерный мастер").id, self.comp.id
        )

    def test_cancel_during_description(self):
        start_work_request(self.user, self.pending, text="вызвать мастера")
        handle_work_request_step(self.user, "2", self.pending)
        self.assertEqual(self.pending.pending_payload.get("step"), "description")
        self.assertTrue(is_cancel_message("отмена", use_ai=False))
        self.assertTrue(is_cancel_message("я ошибся", use_ai=False))
        reply = maybe_cancel_work_flow(
            self.user, "я ошибся тут", self.pending, use_ai=False
        )
        self.assertIsNotNone(reply)
        self.assertTrue(
            "отменил" in reply.lower() or "остановил" in reply.lower(),
            reply,
        )
        self.pending.refresh_from_db()
        self.assertFalse(bool(self.pending.pending_kind))

    def test_cancel_drops_draft_request(self):
        start_work_request(self.user, self.pending, text="нужен электрик")
        handle_work_request_step(self.user, "Починить розетку дома", self.pending)
        # create draft via photo path simulation
        from bot.work_request import handle_work_request_photo

        handle_work_request_photo(
            self.user, self.pending, image_bytes=b"x", filename="a.jpg"
        )
        req = WorkRequest.objects.get(user=self.user)
        self.assertEqual(req.status, WorkRequestStatus.DRAFT)
        reply = handle_work_request_step(self.user, "отмена", self.pending)
        self.assertIn("отменил", reply.lower())
        req.refresh_from_db()
        self.assertEqual(req.status, WorkRequestStatus.CANCELLED)
