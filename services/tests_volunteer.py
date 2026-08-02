from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.utils import timezone

from database.models import (
    BotUser,
    PendingAction,
    ServiceCategory,
    ServiceGroup,
    VolunteerHelpAsk,
    VolunteerReplyStatus,
)
from services.ranking import citizen_stats
from services.service import launch_campaign_to_group
from services.volunteer import (
    decline_penalty,
    handle_volunteer_help_reply,
)


class VolunteerHelpTests(TestCase):
    def setUp(self) -> None:
        self.user = BotUser.objects.create(
            max_user_id="vol-1",
            real_name="Житель",
            chat_id="cv1",
            citizen_score=100,
        )
        self.group = ServiceGroup.objects.create(name="Двор")
        self.group.members.add(self.user)
        self.sent: list[tuple[int, str]] = []

        def capture(user, text):
            self.sent.append((user.id, text))

        self.capture = capture

    def _launch(self, category: str, title: str = "Событие"):
        return launch_campaign_to_group(
            category=category,
            title=title,
            description="",
            group=self.group,
            total_amount=Decimal("1000"),
            amount_per_user=Decimal("100"),
            event_at=timezone.now() + timedelta(days=3),
            send_fn=self.capture,
        )

    def test_decline_penalty_ladder(self):
        self.assertEqual(decline_penalty(1), 1)
        self.assertEqual(decline_penalty(2), 3)
        self.assertEqual(decline_penalty(3), 5)
        self.assertEqual(decline_penalty(4), 10)
        self.assertEqual(decline_penalty(5), 15)
        self.assertEqual(decline_penalty(9), 15)

    def test_playground_asks_help_and_yes_adds_score(self):
        campaign, _ = self._launch(ServiceCategory.PLAYGROUND, "Площадка")
        ask = VolunteerHelpAsk.objects.get(campaign=campaign, user=self.user)
        self.assertEqual(ask.status, VolunteerReplyStatus.PENDING)
        self.assertTrue(any("Поможете ли вы" in t for _, t in self.sent))

        pending = PendingAction.objects.get(user=self.user)
        self.assertEqual(pending.pending_kind, "volunteer_help_reply")
        reply = handle_volunteer_help_reply(self.user, "да", pending)
        self.assertIn("поможете", reply.lower())
        self.user.refresh_from_db()
        ask.refresh_from_db()
        self.assertEqual(ask.status, VolunteerReplyStatus.YES)
        self.assertEqual(self.user.citizen_score, 100)  # clamped at 100
        self.assertEqual(ask.score_delta, 5)

        stats = citizen_stats(self.user)
        self.assertEqual(stats.playground_helps, 1)
        self.assertEqual(stats.help_yes, 1)
        self.assertEqual(stats.help_ratio, 100.0)

    def test_road_no_applies_penalty_ladder(self):
        for i in range(1, 6):
            self.sent.clear()
            campaign, _ = self._launch(ServiceCategory.ROAD, f"Дорога {i}")
            pending = PendingAction.objects.get(user=self.user)
            handle_volunteer_help_reply(self.user, "нет", pending)
            self.user.refresh_from_db()

        # 100 -1 -3 -5 -10 -15 = 66
        self.assertEqual(self.user.citizen_score, 66)
        stats = citizen_stats(self.user)
        self.assertEqual(stats.road_helps, 0)
        self.assertEqual(stats.help_asked, 5)
        self.assertEqual(stats.help_yes, 0)
        self.assertEqual(stats.help_ratio, 0.0)
        self.assertEqual(stats.label, "Хороший гражданин")

    def test_snow_does_not_ask_volunteer(self):
        campaign, _ = self._launch(ServiceCategory.SNOW, "Снег")
        self.assertFalse(
            VolunteerHelpAsk.objects.filter(campaign=campaign, user=self.user).exists()
        )
        self.assertFalse(any("Поможете ли вы" in t for _, t in self.sent))

    def test_score_not_mentioned_in_reply(self):
        self._launch(ServiceCategory.PLAYGROUND, "Площадка")
        pending = PendingAction.objects.get(user=self.user)
        reply = handle_volunteer_help_reply(self.user, "2", pending)
        low = reply.lower()
        self.assertNotIn("балл", low)
        self.assertNotIn("рейтинг", low)
        self.assertNotIn("+5", reply)
        self.assertNotIn("-1", reply)


class GroupsPageTests(TestCase):
    def setUp(self) -> None:
        self.admin = User.objects.create_user("grpadm", password="pass")
        self.client = Client()
        self.client.login(username="grpadm", password="pass")

    def test_groups_page_and_create(self):
        resp = self.client.get("/panel/services/groups/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Группы жителей", resp.content.decode())
        resp = self.client.post(
            "/panel/services/groups/",
            {"action": "create_group", "name": "Новая", "description": ""},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(ServiceGroup.objects.filter(name="Новая").exists())

    def test_services_home_has_no_group_create_form(self):
        resp = self.client.get("/panel/services/")
        body = resp.content.decode()
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn('value="create_group"', body)
        self.assertIn("Запустить сбор", body)
