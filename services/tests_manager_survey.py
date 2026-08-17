from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.utils import timezone

from database.models import (
    BotUser,
    ManagerSurveyAILog,
    ManagerSurveyPeriod,
    ManagerSurveyPeriodStatus,
    ManagerSurveyResponse,
    PanelProfile,
    PanelRole,
    PendingAction,
    ServiceGroup,
)
from panel.roles import assign_group_manager
from services.manager_survey import (
    close_period,
    handle_manager_survey_step,
    process_manager_surveys,
    start_manager_survey,
    summarize_period_with_ai,
)


class ManagerSurveyTests(TestCase):
    def setUp(self) -> None:
        self.resident = BotUser.objects.create(
            max_user_id="ms-r1",
            real_name="Житель",
            chat_id="c-r1",
        )
        self.mgr_bot = BotUser.objects.create(
            max_user_id="ms-m1",
            real_name="МенеджерБот",
            phone="9625507832",
            chat_id="c-m1",
        )
        self.group = ServiceGroup.objects.create(name="Шоруньжа")
        self.group.members.add(self.resident, self.mgr_bot)
        self.mgr_user, _ = assign_group_manager(
            self.group, self.mgr_bot, password="MgrPass99"
        )
        self.admin = User.objects.create_superuser("msadm", "a@t.com", "pass")
        PanelProfile.objects.create(user=self.admin, role=PanelRole.ADMIN)
        self.client = Client()
        self.client.login(username="msadm", password="pass")

    def test_first_survey_starts_immediately(self):
        sent = []

        def send_fn(user, text):
            sent.append((user.id, text))

        period = start_manager_survey(self.group, send_fn=send_fn)
        self.assertIsNotNone(period)
        assert period is not None
        self.assertEqual(period.status, ManagerSurveyPeriodStatus.COLLECTING)
        # менеджер сам не получает опрос
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0][0], self.resident.id)
        self.assertIn("1 до 5", sent[0][1])
        pending = PendingAction.objects.get(user=self.resident)
        self.assertEqual(pending.pending_kind, "manager_survey")

    def test_monthly_gate_blocks_second_immediate(self):
        start_manager_survey(self.group, send_fn=lambda *_: None)
        second = start_manager_survey(self.group, send_fn=lambda *_: None)
        self.assertIsNone(second)

    def test_vote_and_feedback_comment(self):
        period = start_manager_survey(self.group, send_fn=lambda *_: None)
        assert period is not None
        pending, _ = PendingAction.objects.get_or_create(user=self.resident)
        pending.pending_kind = "manager_survey"
        pending.pending_payload = {"period_id": period.id, "step": "score"}
        pending.save()
        reply = handle_manager_survey_step(self.resident, "3", pending)
        self.assertIn("3/5", reply or "")
        reply2 = handle_manager_survey_step(
            self.resident, "Мало отвечает в чате", pending
        )
        self.assertIn("Спасибо", reply2 or "")
        resp = ManagerSurveyResponse.objects.get(period=period, user=self.resident)
        self.assertEqual(resp.score, 3)
        self.assertIn("чате", resp.comment)

    @patch("ai.factory.get_llm_provider")
    def test_close_runs_ai_and_logs(self, mock_llm):
        class Fake:
            def complete_text(self, system, user, **kwargs):
                return "Менеджеру стоит быстрее отвечать жителям."

        mock_llm.return_value = Fake()
        period = start_manager_survey(self.group, send_fn=lambda *_: None)
        assert period is not None
        ManagerSurveyResponse.objects.create(
            period=period,
            user=self.resident,
            score=2,
            comment="Долго не пишет",
        )
        close_period(period, run_ai=True)
        period.refresh_from_db()
        self.assertEqual(period.status, ManagerSurveyPeriodStatus.CLOSED)
        self.assertIn("быстрее", period.ai_summary)
        roles = set(
            ManagerSurveyAILog.objects.filter(period=period).values_list("role", flat=True)
        )
        self.assertIn("system", roles)
        self.assertIn("user", roles)
        self.assertIn("assistant", roles)

    def test_process_closes_due_and_admin_page(self):
        period = start_manager_survey(self.group, send_fn=lambda *_: None)
        assert period is not None
        ManagerSurveyResponse.objects.create(
            period=period, user=self.resident, score=4, comment="Можно лучше"
        )
        period.ends_at = timezone.now() - timedelta(minutes=1)
        period.save(update_fields=["ends_at"])
        with patch("services.manager_survey.summarize_period_with_ai", return_value="ok") as mock_sum:
            stats = process_manager_surveys(send_fn=lambda *_: None)
        self.assertEqual(stats["closed"], 1)
        self.assertTrue(mock_sum.called)
        period.refresh_from_db()
        self.assertEqual(period.status, ManagerSurveyPeriodStatus.CLOSED)

        resp = self.client.get("/panel/managers/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Менеджеры", resp.content.decode())
        detail = self.client.get(f"/panel/managers/{self.mgr_user.id}/")
        self.assertEqual(detail.status_code, 200)
        self.assertIn("Отзывы 1–4", detail.content.decode())
