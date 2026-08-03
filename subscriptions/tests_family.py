from __future__ import annotations

from datetime import timedelta

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.utils import timezone

from database.models import (
    AccessState,
    AdminTask,
    AdminTaskKind,
    AdminTaskStatus,
    BotUser,
    ProfileStatus,
)
from panel.admin_tasks import task_family_claim
from subscriptions.family import confirm_family_from_task, link_family_members


class FamilySubscriptionTests(TestCase):
    def setUp(self) -> None:
        self.dmitry = BotUser.objects.create(
            max_user_id="dm1",
            real_name="Дмитрий",
            address="Посёлок, ул. Лесная, 5",
            locality="Посёлок",
            profile_status=ProfileStatus.VERIFIED,
            subscription_until=timezone.now() + timedelta(days=90),
        )
        self.elena = BotUser.objects.create(
            max_user_id="el1",
            real_name="Елена",
            address="Посёлок, ул. Лесная, 5",
            locality="Посёлок",
            profile_status=ProfileStatus.VERIFIED,
        )
        self.admin = User.objects.create_user("adm", password="pass")
        self.client = Client()
        self.client.login(username="adm", password="pass")

    def test_link_mirrors_subscription_from_payer(self):
        payer = link_family_members([self.elena, self.dmitry])
        self.assertEqual(payer, self.dmitry)
        self.elena.refresh_from_db()
        self.assertEqual(self.elena.family_payer_id, self.dmitry.id)
        self.assertEqual(self.elena.access_state(), AccessState.ACTIVE)
        self.assertEqual(
            self.elena.effective_subscription_until(),
            self.dmitry.subscription_until,
        )
        label = self.elena.subscription_label()
        self.assertIn("Дмитрий", label)
        self.assertIn("оплачена", label)

    def test_family_claim_done_links_members(self):
        task = task_family_claim(
            self.elena,
            candidate_ids=[self.dmitry.id],
            claimed_family=True,
            address=self.elena.address,
        )
        resp = self.client.post(f"/panel/tasks/{task.id}/done/")
        self.assertEqual(resp.status_code, 302)
        self.elena.refresh_from_db()
        task.refresh_from_db()
        self.assertEqual(task.status, AdminTaskStatus.DONE)
        self.assertEqual(self.elena.family_payer_id, self.dmitry.id)
        self.assertEqual(self.elena.access_state(), AccessState.ACTIVE)

    def test_confirm_family_from_task_helper(self):
        task = AdminTask.objects.create(
            kind=AdminTaskKind.FAMILY_CLAIM,
            title="t",
            user=self.elena,
            status=AdminTaskStatus.OPEN,
            source_model="FamilyClaim",
            source_id=self.elena.id,
            meta={
                "candidate_user_ids": [self.dmitry.id],
                "claimed_family": True,
            },
        )
        payer = confirm_family_from_task(task)
        self.assertEqual(payer.id, self.dmitry.id)

    def test_users_list_shows_family_label(self):
        link_family_members([self.elena, self.dmitry], payer=self.dmitry)
        resp = self.client.get("/panel/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "оплачена")
        self.assertContains(resp, "Дмитрий")

    def test_manual_family_link_on_dashboard(self):
        resp = self.client.post(
            f"/panel/users/{self.elena.id}/family/",
            {
                "action": "link",
                "member_ids": [str(self.elena.id), str(self.dmitry.id)],
                "payer_id": str(self.dmitry.id),
            },
        )
        self.assertEqual(resp.status_code, 302)
        self.elena.refresh_from_db()
        self.assertEqual(self.elena.family_payer_id, self.dmitry.id)
        dash = self.client.get(f"/panel/users/{self.elena.id}/")
        self.assertContains(dash, "оплачена")
        self.assertContains(dash, "Дмитрий")
