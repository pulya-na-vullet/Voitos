"""Security and bugfix coverage."""

from __future__ import annotations

from decimal import Decimal
from io import BytesIO

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from database.models import (
    AccessState,
    AppSettings,
    BotUser,
    ExecutorRole,
    MemoryItem,
    PanelProfile,
    PanelRole,
    PaymentReceipt,
    ReceiptStatus,
    ServiceGroup,
    WorkRequest,
    WorkRequestCommissionStatus,
    WorkRequestOffer,
    WorkRequestOfferStatus,
    WorkRequestStatus,
)
from panel.security import safe_redirect_target
from panel.roles import assign_group_manager
from services.work_request_cancel import cancel_work_flow
from services.work_request_completion import approve_commission
from services.work_request_dispatch import try_dispatch_request
from subscriptions.service import access_message, approve_receipt

User = get_user_model()


class SafeRedirectTests(TestCase):
    def test_rejects_open_redirects(self):
        self.assertEqual(safe_redirect_target("//evil.com", fallback="/ok/"), "/ok/")
        self.assertEqual(
            safe_redirect_target("https://evil.com", fallback="/ok/"), "/ok/"
        )
        self.assertEqual(
            safe_redirect_target("/\\evil.com", fallback="/ok/"), "/ok/"
        )
        self.assertEqual(
            safe_redirect_target("/panel/receipts/", fallback="/ok/"),
            "/panel/receipts/",
        )


class DraftNotDispatchedTests(TestCase):
    def setUp(self):
        self.role = ExecutorRole.objects.create(
            code="r_sec_draft", name="электрик", is_active=True
        )
        self.client_user = BotUser.objects.create(
            max_user_id="sec_c1", real_name="Клиент", locality="Куюки"
        )

    def test_draft_not_dispatched(self):
        req = WorkRequest.objects.create(
            user=self.client_user,
            role=self.role,
            description="розетка",
            status=WorkRequestStatus.DRAFT,
            client_locality="Куюки",
        )
        self.assertIsNone(try_dispatch_request(req, send_fn=lambda *a, **k: None, use_ai=False))


class CancelAwaitingClientTests(TestCase):
    def setUp(self):
        self.role = ExecutorRole.objects.create(
            code="r_sec_cancel", name="сварщик", is_active=True
        )
        self.user = BotUser.objects.create(max_user_id="sec_u1", real_name="Житель")
        from database.models import PendingAction

        self.pending = PendingAction.objects.create(user=self.user)

    def test_cancel_awaiting_client_frees_executor(self):
        req = WorkRequest.objects.create(
            user=self.user,
            role=self.role,
            description="шов",
            status=WorkRequestStatus.AWAITING_CLIENT,
            commission_status=WorkRequestCommissionStatus.AWAITING,
            commission_amount=Decimal("100"),
        )
        self.pending.pending_kind = "work_request_client_confirm"
        self.pending.pending_payload = {"request_id": req.id}
        self.pending.save()
        cancel_work_flow(self.user, self.pending)
        req.refresh_from_db()
        self.assertEqual(req.status, WorkRequestStatus.CANCELLED)
        self.assertEqual(req.commission_status, WorkRequestCommissionStatus.NONE)


class AtomicReceiptApproveTests(TestCase):
    def setUp(self):
        AppSettings.load()
        self.user = BotUser.objects.create(max_user_id="sec_r1", real_name="Плательщик")
        self.receipt = PaymentReceipt.objects.create(
            user=self.user,
            status=ReceiptStatus.PENDING,
            amount=Decimal("100"),
        )
        self.receipt.image.save("t.jpg", ContentFile(b"fake"), save=True)

    def test_double_approve_idempotent(self):
        r1 = approve_receipt(self.receipt, amount=Decimal("100"))
        r2 = approve_receipt(self.receipt, amount=Decimal("100"))
        self.assertEqual(r1.status, ReceiptStatus.APPROVED)
        self.assertEqual(r2.status, ReceiptStatus.APPROVED)
        self.user.refresh_from_db()
        # не удвоили срок: один approve реально продлил
        self.assertIsNotNone(self.user.subscription_until)


class CommissionApproveGuardTests(TestCase):
    def setUp(self):
        self.role = ExecutorRole.objects.create(
            code="r_sec_com", name="грузчик", is_active=True
        )
        self.user = BotUser.objects.create(max_user_id="sec_com", real_name="К")
        self.req = WorkRequest.objects.create(
            user=self.user,
            role=self.role,
            description="х",
            status=WorkRequestStatus.AWAITING_COMMISSION,
            commission_status=WorkRequestCommissionStatus.APPROVED,
            commission_amount=Decimal("50"),
        )

    def test_cannot_reapprove(self):
        with self.assertRaises(ValueError):
            approve_commission(self.req)


class IdorDeleteMemoryTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser("secadm", "a@t.com", "pass")
        self.g1 = ServiceGroup.objects.create(name="G1")
        self.u1 = BotUser.objects.create(max_user_id="sec_m1", real_name="В группе")
        self.u2 = BotUser.objects.create(max_user_id="sec_m2", real_name="Вне группы")
        self.g1.members.add(self.u1)
        self.mgr_user, _ = assign_group_manager(self.g1, self.u1, password="MgrPass99")
        self.mem_ok = MemoryItem.objects.create(user=self.u1, text="своё")
        self.mem_bad = MemoryItem.objects.create(user=self.u2, text="чужое")
        self.client = Client()

    def test_manager_cannot_delete_out_of_scope_memory(self):
        self.assertTrue(
            self.client.login(username=self.mgr_user.username, password="MgrPass99")
        )
        resp = self.client.post(f"/panel/delete/memory/{self.mem_bad.id}/")
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(MemoryItem.objects.filter(pk=self.mem_bad.id).exists())
        resp2 = self.client.post(f"/panel/delete/memory/{self.mem_ok.id}/")
        self.assertEqual(resp2.status_code, 302)
        self.assertFalse(MemoryItem.objects.filter(pk=self.mem_ok.id).exists())


class CommissionAdminOnlyTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser("comadm", "c@t.com", "pass")
        self.g1 = ServiceGroup.objects.create(name="GCom")
        self.u1 = BotUser.objects.create(max_user_id="com_u", real_name="Ж")
        self.g1.members.add(self.u1)
        self.mgr_user, _ = assign_group_manager(self.g1, self.u1, password="MgrPass99")
        self.role = ExecutorRole.objects.create(
            code="r_com_adm", name="каменщик", is_active=True
        )
        self.req = WorkRequest.objects.create(
            user=self.u1,
            role=self.role,
            description="кладка",
            status=WorkRequestStatus.AWAITING_COMMISSION,
            commission_status=WorkRequestCommissionStatus.PENDING_REVIEW,
            commission_amount=Decimal("100"),
        )
        self.client = Client()

    def test_manager_cannot_approve_commission(self):
        self.assertTrue(
            self.client.login(username=self.mgr_user.username, password="MgrPass99")
        )
        resp = self.client.post(
            f"/panel/work-requests/{self.req.id}/",
            {"action": "approve_commission", "note": "ok"},
        )
        self.assertEqual(resp.status_code, 302)
        self.req.refresh_from_db()
        self.assertEqual(
            self.req.commission_status, WorkRequestCommissionStatus.PENDING_REVIEW
        )


class TrialGraceMessageTests(TestCase):
    def test_trial_copy(self):
        AppSettings.load()
        user = BotUser.objects.create(max_user_id="trial1", real_name="Новый")
        user.ensure_grace_period()
        msg = access_message(user)
        self.assertIsNotNone(msg)
        self.assertIn("пробный", msg.lower())


class PlateYesNotSkipTests(TestCase):
    def test_yes_asks_again(self):
        from database.models import PendingAction
        from bot.contractor_registration import handle_contractor_registration_step

        role = ExecutorRole.objects.create(
            code="r_plate", name="тракторист", is_equipment=True, is_active=True
        )
        user = BotUser.objects.create(max_user_id="plate1", real_name="И")
        pending = PendingAction.objects.create(
            user=user,
            pending_kind="contractor_registration",
            pending_payload={"step": "plate", "role_id": role.id},
        )
        reply = handle_contractor_registration_step(user, "да", pending)
        self.assertIn("госномер", reply.lower())
        pending.refresh_from_db()
        self.assertEqual(pending.pending_payload.get("step"), "plate")
