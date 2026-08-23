"""Tests for client→master booking with free calendar slots."""

from __future__ import annotations

from decimal import Decimal

from django.test import Client, TestCase
from django.utils import timezone as dj_tz

from api.models import MobileAuthToken
from database.models import (
    BotUser,
    ContractorProfile,
    ContractorStatus,
    ExecutorRole,
    ProfileStatus,
    WorkRequest,
    WorkRequestCommissionStatus,
    WorkRequestStatus,
)
from services.master_booking import (
    COMMISSION_BLOCK_MSG,
    contractor_blocked_for_commission,
    create_client_booking,
    free_slots_for_contractor,
    role_is_dispatch_only,
    role_uses_client_booking,
)
from services.work_request_schedule import format_slot_label


class MasterBookingUnitTests(TestCase):
    def test_dispatch_only_roles(self):
        tractor = ExecutorRole(code="tractor", name="Тракторист")
        truck = ExecutorRole(code="r_x", name="Водитель грузовой машины")
        pc = ExecutorRole(code="computer_master", name="Компьютерный мастер")
        nails = ExecutorRole(code="r_nails", name="Маникюр")
        self.assertTrue(role_is_dispatch_only(tractor))
        self.assertTrue(role_is_dispatch_only(truck))
        self.assertTrue(role_is_dispatch_only(pc))
        self.assertFalse(role_is_dispatch_only(nails))
        nails.client_books_master = True
        self.assertTrue(role_uses_client_booking(nails))
        tractor.client_books_master = False
        self.assertFalse(role_uses_client_booking(tractor))


class MasterBookingApiTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.role = ExecutorRole.objects.create(
            code="r_plumb",
            name="Сантехник",
            is_active=True,
            requires_work_photos=False,
            client_books_master=True,
        )
        self.tractor = ExecutorRole.objects.create(
            code="tractor",
            name="Тракторист",
            is_active=True,
            requires_work_photos=True,
            client_books_master=False,
        )
        self.client_user = BotUser.objects.create(
            max_user_id="bk-cl",
            phone="89625501001",
            real_name="Житель",
            locality="Куюки",
            profile_status=ProfileStatus.VERIFIED,
        )
        self.master_user = BotUser.objects.create(
            max_user_id="bk-ex",
            phone="89625501002",
            real_name="Мастер Иван",
            locality="Куюки",
            profile_status=ProfileStatus.VERIFIED,
        )
        self.contractor = ContractorProfile.objects.create(
            user=self.master_user,
            role=self.role,
            equipment_type=self.role.code,
            locality="Куюки",
            status=ContractorStatus.VERIFIED,
        )
        self.tok = MobileAuthToken.objects.create(bot_user=self.client_user)
        self.auth = {"HTTP_AUTHORIZATION": f"Bearer {self.tok.token}"}
        self.master_tok = MobileAuthToken.objects.create(bot_user=self.master_user)
        self.master_auth = {"HTTP_AUTHORIZATION": f"Bearer {self.master_tok.token}"}

    def test_roles_flag_and_masters_list(self):
        roles = self.client.get("/api/v1/executor-roles", **self.auth)
        self.assertEqual(roles.status_code, 200)
        by_code = {r["code"]: r for r in roles.json()["items"]}
        self.assertTrue(by_code["r_plumb"]["client_books_master"])
        self.assertFalse(by_code["tractor"]["client_books_master"])

        masters = self.client.get(
            f"/api/v1/executor-roles/{self.role.id}/masters",
            **self.auth,
        )
        self.assertEqual(masters.status_code, 200)
        items = masters.json()["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["contractor_id"], self.contractor.id)
        self.assertTrue(items[0]["can_accept"])

    def test_free_slots_and_booking_confirm(self):
        slots = free_slots_for_contractor(self.contractor, days=7)
        self.assertGreater(len(slots), 0)
        label = slots[0]["label"]

        resp = self.client.post(
            "/api/v1/work-requests",
            data=__import__("json").dumps(
                {
                    "role_id": self.role.id,
                    "description": "Течёт кран на кухне",
                    "contractor_id": self.contractor.id,
                    "slot": label,
                }
            ),
            content_type="application/json",
            **self.auth,
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        body = resp.json()
        self.assertTrue(body["client_prebooked"])
        self.assertEqual(body["status"], WorkRequestStatus.SCHEDULING)
        wr_id = body["id"]

        jobs = self.client.get("/api/v1/executor/jobs", **self.master_auth)
        self.assertEqual(jobs.status_code, 200)
        job = next(j for j in jobs.json()["items"] if j["id"] == wr_id)
        self.assertTrue(job["needs_confirm_booking"])
        self.assertEqual(job["agreed_slot"], label)

        confirm = self.client.post(
            f"/api/v1/work-requests/{wr_id}/confirm-booking",
            data="{}",
            content_type="application/json",
            **self.master_auth,
        )
        self.assertEqual(confirm.status_code, 200, confirm.content)
        self.assertEqual(confirm.json()["status"], WorkRequestStatus.IN_PROGRESS)

    def test_commission_blocks_booking(self):
        other_client = BotUser.objects.create(
            max_user_id="bk-cl2",
            phone="89625501003",
            real_name="Другой",
            locality="Куюки",
            profile_status=ProfileStatus.VERIFIED,
        )
        WorkRequest.objects.create(
            user=other_client,
            role=self.role,
            description="Старый заказ",
            status=WorkRequestStatus.AWAITING_COMMISSION,
            assigned_contractor=self.contractor,
            commission_status=WorkRequestCommissionStatus.AWAITING,
            commission_amount=Decimal("100"),
            confirmed_amount=Decimal("1000"),
        )
        self.assertTrue(contractor_blocked_for_commission(self.contractor))

        masters = self.client.get(
            f"/api/v1/executor-roles/{self.role.id}/masters",
            **self.auth,
        )
        item = masters.json()["items"][0]
        self.assertFalse(item["can_accept"])
        self.assertEqual(item["blocked_reason"], "commission")
        self.assertIn("комиссию", item["blocked_message"])

        slots = free_slots_for_contractor(self.contractor, days=3)
        with self.assertRaises(ValueError) as ctx:
            create_client_booking(
                client=self.client_user,
                role=self.role,
                description="Новая протечка крана",
                contractor_id=self.contractor.id,
                slot_label=slots[0]["label"] if slots else format_slot_label(
                    dj_tz.localtime(dj_tz.now()).replace(hour=10, minute=0, second=0, microsecond=0)
                    + __import__("datetime").timedelta(days=1),
                    dj_tz.localtime(dj_tz.now()).replace(hour=11, minute=0, second=0, microsecond=0)
                    + __import__("datetime").timedelta(days=1),
                ),
            )
        self.assertEqual(str(ctx.exception), COMMISSION_BLOCK_MSG)

    def test_dispatch_role_rejects_direct_booking_fields(self):
        resp = self.client.post(
            "/api/v1/work-requests",
            data=__import__("json").dumps(
                {
                    "role_id": self.tractor.id,
                    "description": "Нужен трактор во дворе",
                    "contractor_id": self.contractor.id,
                    "slot": "01.01.2030 10:00–11:00",
                }
            ),
            content_type="application/json",
            **self.auth,
        )
        # Без client_books_master — обычный create (игнор contractor/slot)
        self.assertEqual(resp.status_code, 201)
        wr = WorkRequest.objects.get(pk=resp.json()["id"])
        self.assertFalse(wr.client_prebooked)
        self.assertIsNone(wr.assigned_contractor_id)
