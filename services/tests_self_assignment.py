
"""Запрет назначать себя исполнителем своей заявки."""

from __future__ import annotations

from django.test import TestCase

from database.models import (
    BotUser,
    ContractorProfile,
    ContractorStatus,
    ExecutorRole,
    WorkRequest,
    WorkRequestStatus,
)
from services.master_booking import create_client_booking, list_masters_for_role
from services.work_request_dispatch import SELF_ASSIGNMENT_MSG, send_offer


class SelfAssignmentBlockTests(TestCase):
    def setUp(self):
        self.role = ExecutorRole.objects.create(
            code="plumber", name="Сантехник", is_active=True, client_books_master=True
        )
        self.user = BotUser.objects.create(
            max_user_id="self1",
            real_name="Иван",
            locality="Куюки",
            phone="9001112233",
        )
        self.other = BotUser.objects.create(
            max_user_id="oth1",
            real_name="Пётр",
            locality="Куюки",
            phone="9001112244",
        )
        self.self_profile = ContractorProfile.objects.create(
            user=self.user,
            role=self.role,
            equipment_type=self.role.code,
            locality="Куюки",
            status=ContractorStatus.VERIFIED,
        )
        self.other_profile = ContractorProfile.objects.create(
            user=self.other,
            role=self.role,
            equipment_type=self.role.code,
            locality="Куюки",
            status=ContractorStatus.VERIFIED,
        )

    def test_list_masters_excludes_self(self):
        items = list_masters_for_role(self.role, self.user)
        ids = {i["contractor_id"] for i in items}
        self.assertNotIn(self.self_profile.id, ids)
        self.assertIn(self.other_profile.id, ids)

    def test_create_booking_rejects_self(self):
        with self.assertRaises(ValueError) as ctx:
            create_client_booking(
                client=self.user,
                role=self.role,
                description="Починить кран на кухне",
                contractor_id=self.self_profile.id,
                slot_label="24.08.2026 10:00–12:00",
            )
        self.assertIn("себя", str(ctx.exception).lower())

    def test_send_offer_rejects_self(self):
        req = WorkRequest.objects.create(
            user=self.user,
            role=self.role,
            description="Заявка",
            client_locality="Куюки",
            status=WorkRequestStatus.PENDING,
        )
        with self.assertRaises(ValueError) as ctx:
            send_offer(req, self.self_profile, score=1.0, reason="test")
        self.assertEqual(str(ctx.exception), SELF_ASSIGNMENT_MSG)
