"""Tests: заявки на новую роль исполнителя."""

from __future__ import annotations

import json

from datetime import timedelta

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.utils import timezone

from api.models import MobileAuthToken
from database.models import (
    AdminTask,
    AdminTaskKind,
    AdminTaskStatus,
    BotUser,
    ExecutorRole,
    ExecutorRoleProposal,
    ExecutorRoleProposalStatus,
    PanelProfile,
    PanelRole,
)
from services.role_proposals import approve_proposal, propose_role, reject_proposal


class RoleProposalTests(TestCase):
    def setUp(self) -> None:
        self.user = BotUser.objects.create(
            max_user_id="rp-u1",
            real_name="Житель",
            phone="89625509901",
            chat_id="c-rp-1",
        )
        self.user.subscription_until = timezone.now() + timedelta(days=30)
        self.user.save(update_fields=["subscription_until"])
        self.client = Client()
        self.tok = MobileAuthToken.objects.create(bot_user=self.user)
        self.admin = User.objects.create_superuser("rpadm", "r@t.com", "pass")
        PanelProfile.objects.create(user=self.admin, role=PanelRole.ADMIN)

    def _auth(self) -> dict:
        return {"HTTP_AUTHORIZATION": f"Bearer {self.tok.token}"}

    def test_propose_creates_task(self):
        resp = self.client.post(
            "/api/v1/executor/role-proposals",
            data=json.dumps({"name": "Мастер по окнам"}),
            content_type="application/json",
            **self._auth(),
        )
        self.assertEqual(resp.status_code, 201)
        proposal = ExecutorRoleProposal.objects.get()
        self.assertEqual(proposal.proposed_name, "Мастер по окнам")
        self.assertEqual(proposal.status, ExecutorRoleProposalStatus.OPEN)
        task = AdminTask.objects.filter(
            kind=AdminTaskKind.ROLE_PROPOSAL,
            source_id=proposal.id,
            status=AdminTaskStatus.OPEN,
        ).first()
        self.assertIsNotNone(task)
        self.assertIn(f"/panel/role-proposals/{proposal.id}/", task.action_url)

    def test_one_proposal_per_day(self):
        propose_role(self.user, "Сантехник")
        with self.assertRaises(ValueError):
            propose_role(self.user, "Электрик")

    def test_approve_creates_active_role(self):
        proposal = propose_role(self.user, "Мастер по окнам")
        role = approve_proposal(
            proposal,
            admin_user=self.admin,
            accepts_at_home=True,
            requires_work_photos=False,
        )
        self.assertTrue(role.is_active)
        self.assertEqual(role.name, "Мастер по окнам")
        self.assertTrue(role.accepts_at_home)
        self.assertFalse(role.requires_work_photos)
        proposal.refresh_from_db()
        self.assertEqual(proposal.status, ExecutorRoleProposalStatus.APPROVED)
        self.assertEqual(proposal.created_role_id, role.id)
        self.assertFalse(
            AdminTask.objects.filter(
                kind=AdminTaskKind.ROLE_PROPOSAL,
                source_id=proposal.id,
                status=AdminTaskStatus.OPEN,
            ).exists()
        )
        # Роль видна в API каталога
        listed = self.client.get("/api/v1/executor-roles", **self._auth())
        self.assertEqual(listed.status_code, 200)
        names = {i["name"] for i in listed.json()["items"]}
        self.assertIn("Мастер по окнам", names)

    def test_reject_closes_task(self):
        proposal = propose_role(self.user, "Неизвестная роль")
        reject_proposal(proposal, admin_user=self.admin, admin_note="Не нужно")
        proposal.refresh_from_db()
        self.assertEqual(proposal.status, ExecutorRoleProposalStatus.REJECTED)
        self.assertFalse(
            AdminTask.objects.filter(
                kind=AdminTaskKind.ROLE_PROPOSAL,
                source_id=proposal.id,
                status=AdminTaskStatus.OPEN,
            ).exists()
        )

    def test_panel_approve_flow(self):
        proposal = propose_role(self.user, "Плиточник")
        panel = Client()
        panel.login(username="rpadm", password="pass")
        resp = panel.post(
            f"/panel/role-proposals/{proposal.id}/",
            {"action": "approve", "role_name": "Плиточник", "admin_note": "Ок"},
        )
        self.assertEqual(resp.status_code, 302)
        proposal.refresh_from_db()
        self.assertEqual(proposal.status, ExecutorRoleProposalStatus.APPROVED)
        self.assertTrue(ExecutorRole.objects.filter(name="Плиточник", is_active=True).exists())
