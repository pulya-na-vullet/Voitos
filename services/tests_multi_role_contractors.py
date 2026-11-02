"""Несколько ролей исполнителя на одной УЗ + контакты при accept."""

from __future__ import annotations

from django.test import TestCase
from django.utils import timezone

from bot.contractor_registration import (
    handle_contractor_registration_step,
    start_contractor_registration,
)
from database.models import (
    BotUser,
    ContractorProfile,
    ContractorStatus,
    ExecutorRole,
    PendingAction,
    WorkRequest,
    WorkRequestOffer,
    WorkRequestOfferStatus,
    WorkRequestStatus,
)
from services.work_request_dispatch import accept_offer


class MultiRoleContractorTests(TestCase):
    def setUp(self) -> None:
        self.elec = ExecutorRole.objects.create(
            code="r_elec_mr", name="электрик", is_active=True
        )
        self.mani = ExecutorRole.objects.create(
            code="r_mani_mr",
            name="мастер по маникюру",
            is_active=True,
            accepts_at_home=True,
        )
        self.user = BotUser.objects.create(
            max_user_id="mr1",
            real_name="Дмитрий",
            phone="89621112233",
            username="dmitry_master",
            locality="Куюки",
        )
        self.pending, _ = PendingAction.objects.get_or_create(user=self.user)

    def _register(self, role: ExecutorRole, label: str = "опыт 5 лет") -> ContractorProfile:
        start_contractor_registration(self.user, self.pending, role=role)
        handle_contractor_registration_step(self.user, label, self.pending)
        # phone skipped if user.phone set → locality
        handle_contractor_registration_step(self.user, "Куюки", self.pending)
        handle_contractor_registration_step(self.user, "Сбер", self.pending)
        handle_contractor_registration_step(self.user, "89621112233", self.pending)
        return ContractorProfile.objects.get(user=self.user, equipment_type=role.code)

    def test_second_role_keeps_first(self):
        p1 = self._register(self.elec, "розетки и свет")
        self.assertEqual(p1.role_id, self.elec.id)
        p1.status = ContractorStatus.VERIFIED
        p1.verified_at = timezone.now()
        p1.save(update_fields=["status", "verified_at"])

        p2 = self._register(self.mani, "гель-лак")
        self.assertEqual(ContractorProfile.objects.filter(user=self.user).count(), 2)
        p1.refresh_from_db()
        self.assertEqual(p1.role_id, self.elec.id)
        self.assertEqual(p1.status, ContractorStatus.VERIFIED)
        self.assertEqual(p2.role_id, self.mani.id)
        self.assertEqual(p2.status, ContractorStatus.PENDING_REVIEW)
        labels = {p.role_label for p in self.user.contractor_profiles.all()}
        self.assertEqual(labels, {"электрик", "мастер по маникюру"})

    def test_reregister_same_role_updates_not_duplicates(self):
        self._register(self.elec, "старое")
        self._register(self.elec, "новое описание")
        self.assertEqual(
            ContractorProfile.objects.filter(user=self.user, equipment_type=self.elec.code).count(),
            1,
        )
        p = ContractorProfile.objects.get(user=self.user, equipment_type=self.elec.code)
        self.assertEqual(p.experience_text, "новое описание")


class AcceptOfferContactsTests(TestCase):
    def setUp(self) -> None:
        self.role = ExecutorRole.objects.create(
            code="r_mani_ct",
            name="мастер по маникюру",
            is_active=True,
            accepts_at_home=True,
        )
        self.client_user = BotUser.objects.create(
            max_user_id="cl_ct", real_name="Клиент", locality="Куюки"
        )
        self.exec_user = BotUser.objects.create(
            max_user_id="ex_ct",
            real_name="Анна",
            phone="89001112233",
            username="anna_nails",
            locality="Куюки",
        )
        self.contractor = ContractorProfile.objects.create(
            user=self.exec_user,
            role=self.role,
            equipment_type=self.role.code,
            phone="89001112233",
            locality="Куюки",
            status=ContractorStatus.VERIFIED,
        )
        self.req = WorkRequest.objects.create(
            user=self.client_user,
            role=self.role,
            description="маникюр",
            client_locality="Куюки",
            status=WorkRequestStatus.OFFERING,
        )
        self.offer = WorkRequestOffer.objects.create(
            work_request=self.req,
            contractor=self.contractor,
            status=WorkRequestOfferStatus.OFFERED,
        )
        self.sent: list[tuple[str, str]] = []

        def capture(user, text):
            self.sent.append((str(user), text))

        self.capture = capture

    def test_accept_home_includes_phone_and_max_link(self):
        accept_offer(self.offer, send_fn=self.capture)
        client_msgs = [t for u, t in self.sent if "Клиент" in u or u == str(self.client_user)]
        # capture uses str(user) which is real_name
        client_msgs = [t for u, t in self.sent if u == "Клиент"]
        self.assertTrue(client_msgs, self.sent)
        text = client_msgs[0]
        self.assertIn("89001112233", text)
        self.assertIn("@anna_nails", text)
        self.assertIn("https://max.ru/anna_nails", text)
