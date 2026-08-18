"""Tests for comic onboarding (5 stories + month reward)."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from datetime import timedelta

from django.utils import timezone

from bot.onboarding import (
    STORIES,
    handle_onboarding_step,
    panel_progress,
    start_onboarding,
)
from bot.pipeline import MessagePipeline
from database.models import BotUser, PanelProfile, PanelRole, PendingAction, ProfileStatus

User = get_user_model()


class ComicOnboardingTests(TestCase):
    def setUp(self):
        self.user = BotUser.objects.create(
            max_user_id="ob1",
            real_name="Оля",
            phone="89625507832",
            address="ул. Тест 1",
            locality="Куюки",
            profile_status=ProfileStatus.VERIFIED,
        )
        self.pending, _ = PendingAction.objects.get_or_create(user=self.user)

    def test_full_flow_grants_month(self):
        msg = start_onboarding(self.user, self.pending)
        self.assertIn("комикс", msg.lower())
        msg = handle_onboarding_step(self.user, "да", self.pending)
        self.assertIn("уборка снега", msg.lower())
        self.assertTrue((self.pending.pending_payload or {}).get("attach_image"))

        for i in range(len(STORIES)):
            msg = handle_onboarding_step(self.user, "далее", self.pending)
            self.user.refresh_from_db()
            if i < len(STORIES) - 1:
                self.assertEqual(len(self.user.onboarding_steps), i + 1)
            else:
                self.assertIn("подарок", msg.lower())
                self.assertTrue(self.user.onboarding_reward_granted)
                self.assertTrue(self.user.onboarding_completed_at)
                self.assertIsNotNone(self.user.subscription_until)
                self.assertGreaterEqual(
                    (self.user.subscription_until - timezone.now()).days, 29
                )

    def test_reward_only_once(self):
        self.user.onboarding_steps = [s.code for s in STORIES]
        self.user.onboarding_reward_granted = True
        self.user.onboarding_completed_at = timezone.now()
        self.user.subscription_until = timezone.now() + timedelta(days=10)
        self.user.save()
        before = self.user.subscription_until
        msg = start_onboarding(self.user, self.pending)
        self.assertIn("уже пройдено", msg.lower())
        self.user.refresh_from_db()
        self.assertEqual(self.user.subscription_until, before)

    def test_pipeline_intent(self):
        pipe = MessagePipeline()
        reply = pipe.handle(self.user, "обучение")
        self.assertIn("комикс", reply.lower())

    def test_panel_progress_partial(self):
        self.user.onboarding_steps = ["snow"]
        self.user.save(update_fields=["onboarding_steps"])
        prog = panel_progress(self.user)
        self.assertEqual(prog["done_count"], 1)
        self.assertEqual(prog["total"], 5)
        self.assertFalse(prog["completed"])
        self.assertTrue(prog["steps"][0]["done"])
        self.assertFalse(prog["steps"][1]["done"])


class ComicOnboardingPanelTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser("adm_ob", "a@t.com", "pass")
        PanelProfile.objects.create(user=self.admin, role=PanelRole.ADMIN)
        self.bot = BotUser.objects.create(
            max_user_id="ob2",
            real_name="Пётр",
            onboarding_steps=["snow", "playground"],
        )
        self.client = Client()
        self.client.login(username="adm_ob", password="pass")

    def test_user_card_shows_progress(self):
        resp = self.client.get(reverse("panel:user_dashboard", args=[self.bot.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Обучение (комиксы)")
        self.assertContains(resp, "2 / 5")
        self.assertContains(resp, "Уборка снега")
        self.assertContains(resp, "пройдено")

    def test_users_list_shows_onboarding_counter(self):
        done = BotUser.objects.create(
            max_user_id="ob3",
            real_name="Готово",
            onboarding_steps=["snow", "playground", "electrician", "manicure", "computer"],
            onboarding_reward_granted=True,
        )
        resp = self.client.get(reverse("panel:users"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Прошли обучение")
        self.assertContains(resp, "Обучение")
        self.assertContains(resp, "2/5")  # Пётр partial
        self.assertContains(resp, "5/5")  # Готово
        self.assertContains(resp, "+1 мес")
        self.assertEqual(resp.context["stats"]["onboarding_done"], 1)
