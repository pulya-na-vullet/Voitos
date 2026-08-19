from __future__ import annotations

from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.test import TestCase

from database.models import (
    BotUser,
    FeedbackKind,
    FeedbackStatus,
    FeedbackTicket,
    ManagerSurveyAILog,
    PanelProfile,
    PanelRole,
    ServiceGroup,
)
from panel.roles import assign_group_manager
from services.feedback import create_feedback_ticket, managers_for_user, resolve_manager_group


class ManagerFeedbackRoutingTests(TestCase):
    def setUp(self) -> None:
        self.resident = BotUser.objects.create(
            max_user_id="fb-r1",
            real_name="ЖительОС",
            chat_id="c-fb-r1",
        )
        self.mgr_bot_a = BotUser.objects.create(
            max_user_id="fb-ma",
            real_name="МенеджерА",
            phone="9625507801",
            chat_id="c-fb-ma",
        )
        self.mgr_bot_b = BotUser.objects.create(
            max_user_id="fb-mb",
            real_name="МенеджерБ",
            phone="9625507802",
            chat_id="c-fb-mb",
        )
        self.group_a = ServiceGroup.objects.create(name="Группа А")
        self.group_b = ServiceGroup.objects.create(name="Группа Б")
        self.group_a.members.add(self.resident, self.mgr_bot_a)
        self.group_b.members.add(self.resident, self.mgr_bot_b)
        self.mgr_a, _ = assign_group_manager(
            self.group_a, self.mgr_bot_a, password="MgrPassA1"
        )
        self.mgr_b, _ = assign_group_manager(
            self.group_b, self.mgr_bot_b, password="MgrPassB1"
        )
        self.admin = User.objects.create_superuser("fbadm", "fb@t.com", "pass")
        PanelProfile.objects.create(user=self.admin, role=PanelRole.ADMIN)

    def test_managers_for_user_lists_both(self):
        items = managers_for_user(self.resident)
        self.assertEqual(len(items), 2)
        ids = {(i["group_id"], i["manager_id"]) for i in items}
        self.assertEqual(
            ids,
            {(self.group_a.id, self.mgr_a.id), (self.group_b.id, self.mgr_b.id)},
        )

    def test_resolve_manager_group_by_id(self):
        g, m = resolve_manager_group(self.resident, group_id=self.group_b.id)
        self.assertEqual(g.id, self.group_b.id)
        self.assertEqual(m.id, self.mgr_b.id)

    @patch("services.feedback.ai_answer_manager_feedback")
    def test_create_binds_selected_manager(self, ai_mock):
        def fake_ai(ticket):
            ticket.status = FeedbackStatus.ANSWERED
            ticket.admin_reply = "Спасибо за отзыв"
            ticket.save(update_fields=["status", "admin_reply", "updated_at"])
            return ticket

        ai_mock.side_effect = fake_ai
        ticket = create_feedback_ticket(
            self.resident,
            kind=FeedbackKind.MANAGER,
            body="Всё хорошо, спасибо за помощь в районе",
            score=5,
            group_id=self.group_b.id,
        )
        self.assertEqual(ticket.manager_id, self.mgr_b.id)
        self.assertEqual(ticket.group_id, self.group_b.id)
        ai_mock.assert_called_once()

    @patch("ai.factory.get_llm_provider")
    def test_ai_answer_logs_to_manager(self, get_llm):
        llm = MagicMock()
        llm.complete_text.return_value = (
            "Спасибо за высокую оценку. Мы передадим тёплые слова менеджеру."
        )
        get_llm.return_value = llm
        ticket = create_feedback_ticket(
            self.resident,
            kind=FeedbackKind.MANAGER,
            body="Менеджер быстро помог с вопросом по дому",
            score=5,
            group_id=self.group_a.id,
        )
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, FeedbackStatus.ANSWERED)
        self.assertTrue(ticket.admin_reply)
        self.assertIsNone(ticket.admin_replied_by_id)
        self.assertEqual(ticket.manager_id, self.mgr_a.id)
        logs = ManagerSurveyAILog.objects.filter(
            manager=self.mgr_a, period__isnull=True
        )
        self.assertEqual(logs.count(), 1)
        # оценка не должна попасть на другого менеджера
        self.assertFalse(
            FeedbackTicket.objects.filter(
                manager=self.mgr_b, kind=FeedbackKind.MANAGER
            ).exists()
        )
