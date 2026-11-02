"""Tests for work request auto-dispatch by role + locality."""

from __future__ import annotations

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

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
from services.work_request_dispatch import (
    WORK_OFFER_PENDING,
    accept_offer,
    decline_offer,
    dispatch_for_new_contractor,
    expire_stale_work_offers,
    handle_work_offer_reply,
    localities_match,
    try_dispatch_request,
)


class LocalityMatchTests(TestCase):
    def test_normalize_match(self):
        self.assertTrue(localities_match("Куюки", "село Куюки"))
        self.assertTrue(localities_match("г. Казань", "Казань"))
        self.assertFalse(localities_match("Куюки", "Лаишево"))


class WorkRequestDispatchTests(TestCase):
    def setUp(self):
        self.role = ExecutorRole.objects.create(code="r_elec", name="Электрик", is_active=True)
        self.client_user = BotUser.objects.create(
            max_user_id="c1", real_name="Житель", locality="Куюки", phone="89001112233"
        )
        self.exec_user = BotUser.objects.create(
            max_user_id="e1", real_name="Дмитрий", locality="Куюки", phone="89625507832"
        )
        self.contractor = ContractorProfile.objects.create(
            user=self.exec_user,
            role=self.role,
            equipment_type=self.role.code,
            locality="Куюки",
            phone="89625507832",
            status=ContractorStatus.VERIFIED,
        )
        self.sent: list[tuple[str, str]] = []

        def capture(user, text):
            self.sent.append((str(user), text))

        self.capture = capture

    def test_dispatch_offers_and_accept(self):
        req = WorkRequest.objects.create(
            user=self.client_user,
            role=self.role,
            description="Нужно починить розетку",
            client_locality="Куюки",
            status=WorkRequestStatus.PENDING,
        )
        offer = try_dispatch_request(req, send_fn=self.capture, use_ai=False)
        self.assertIsNotNone(offer)
        self.assertEqual(offer.contractor_id, self.contractor.id)
        req.refresh_from_db()
        self.assertEqual(req.status, WorkRequestStatus.OFFERING)
        self.assertTrue(any("Новая заявка" in t for _, t in self.sent))

        pending, _ = PendingAction.objects.get_or_create(user=self.exec_user)
        pending.pending_kind = WORK_OFFER_PENDING
        pending.pending_payload = {"offer_id": offer.id}
        pending.save()
        msg = handle_work_offer_reply(self.exec_user, "да", pending)
        self.assertIn("заявка ваша", msg.lower())
        req.refresh_from_db()
        self.assertEqual(req.status, WorkRequestStatus.SCHEDULING)
        self.assertEqual(req.assigned_contractor_id, self.contractor.id)

    def test_timeout_goes_to_next_or_notifies_client(self):
        other = BotUser.objects.create(max_user_id="e2", real_name="Пётр", locality="Куюки")
        c2 = ContractorProfile.objects.create(
            user=other,
            role=self.role,
            equipment_type=self.role.code,
            locality="Куюки",
            status=ContractorStatus.VERIFIED,
        )
        req = WorkRequest.objects.create(
            user=self.client_user,
            role=self.role,
            description="Розетка",
            client_locality="Куюки",
        )
        offer = try_dispatch_request(req, send_fn=self.capture, use_ai=False)
        self.assertEqual(offer.contractor_id, self.contractor.id)
        offer.respond_deadline = timezone.now() - timedelta(minutes=1)
        offer.save(update_fields=["respond_deadline"])
        n = expire_stale_work_offers(send_fn=self.capture)
        self.assertEqual(n, 1)
        offer.refresh_from_db()
        self.assertEqual(offer.status, WorkRequestOfferStatus.EXPIRED)
        # next offer to second contractor
        active = WorkRequestOffer.objects.filter(
            work_request=req, status=WorkRequestOfferStatus.OFFERED
        ).first()
        self.assertIsNotNone(active)
        self.assertEqual(active.contractor_id, c2.id)

    def test_no_executor_notifies_client(self):
        req = WorkRequest.objects.create(
            user=self.client_user,
            role=self.role,
            description="Розетка",
            client_locality="ДругойПосёлок",
        )
        offer = try_dispatch_request(req, send_fn=self.capture, use_ai=False)
        self.assertIsNone(offer)
        req.refresh_from_db()
        self.assertIsNotNone(req.no_executor_notified_at)
        self.assertTrue(any("нет мастера" in t.lower() for _, t in self.sent))

    def test_old_request_when_contractor_verified(self):
        self.contractor.status = ContractorStatus.PENDING_REVIEW
        self.contractor.save(update_fields=["status"])
        req = WorkRequest.objects.create(
            user=self.client_user,
            role=self.role,
            description="Старая заявка",
            client_locality="Куюки",
            status=WorkRequestStatus.PENDING,
        )
        # no verified yet
        self.assertIsNone(try_dispatch_request(req, send_fn=self.capture, use_ai=False))
        req.refresh_from_db()
        self.assertIsNotNone(req.no_executor_notified_at)

        self.contractor.status = ContractorStatus.VERIFIED
        self.contractor.save(update_fields=["status"])
        n = dispatch_for_new_contractor(self.contractor, send_fn=self.capture, use_ai=False)
        self.assertEqual(n, 1)
        self.assertTrue(
            WorkRequestOffer.objects.filter(
                work_request=req, contractor=self.contractor, status=WorkRequestOfferStatus.OFFERED
            ).exists()
        )

    def test_decline_passes_along(self):
        other = BotUser.objects.create(max_user_id="e3", real_name="Олег", locality="Куюки")
        c2 = ContractorProfile.objects.create(
            user=other,
            role=self.role,
            equipment_type=self.role.code,
            locality="Куюки",
            status=ContractorStatus.VERIFIED,
        )
        req = WorkRequest.objects.create(
            user=self.client_user,
            role=self.role,
            description="Розетка",
            client_locality="Куюки",
        )
        offer = try_dispatch_request(req, send_fn=self.capture, use_ai=False)
        decline_offer(offer, send_fn=self.capture)
        nxt = WorkRequestOffer.objects.filter(
            work_request=req, status=WorkRequestOfferStatus.OFFERED
        ).first()
        self.assertIsNotNone(nxt)
        self.assertEqual(nxt.contractor_id, c2.id)
        client_msgs = [t for who, t in self.sent if "Житель" in who or who == str(self.client_user)]
        self.assertTrue(
            any("отказался" in t.lower() and "ищем другого" in t.lower() for t in client_msgs),
            self.sent,
        )
        self.assertTrue(
            any("отменить заявку" in t.lower() for t in client_msgs),
            self.sent,
        )
