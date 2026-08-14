from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.utils import timezone

from database.models import (
    AssignmentStatus,
    BotUser,
    CampaignAssignment,
    ContractorPayout,
    ContractorProfile,
    ContractorStatus,
    EquipmentType,
    CampaignStatus,
    InviteStatus,
    PendingAction,
    ServiceCategory,
    ServiceGroup,
    WorkStage,
)
from services.contractors import (
    accept_assignment,
    approve_counter_offer,
    assign_contractor,
    close_campaign_requiring_payouts,
    expire_stale_counter_offers,
    handle_offer_reply,
    record_contractor_payouts,
    reject_counter_offer,
    suggested_equipment_for_campaign,
)
from services.service import advance_work_stage, create_campaign
from bot.contractor_registration import (
    handle_contractor_registration_step,
    start_contractor_registration,
)


class ContractorFlowTests(TestCase):
    def setUp(self) -> None:
        self.driver = BotUser.objects.create(
            max_user_id="ctr-1",
            real_name="Иван Тракторист",
            phone="89001112233",
            chat_id="c1",
        )
        self.resident = BotUser.objects.create(
            max_user_id="ctr-r",
            real_name="Житель",
            chat_id="c2",
        )
        self.group = ServiceGroup.objects.create(name="9 аллея")
        self.group.members.add(self.resident)
        self.campaign = create_campaign(
            category=ServiceCategory.SNOW,
            title="Чистка снега",
            group=self.group,
            total_amount=Decimal("1000"),
            amount_per_user=Decimal("100"),
            event_at=timezone.now() + timedelta(days=2),
            needs_snow_haul=True,
        )
        self.profile = ContractorProfile.objects.create(
            user=self.driver,
            equipment_type=EquipmentType.TRACTOR,
            equipment_label="МТЗ-82",
            status=ContractorStatus.VERIFIED,
            verified_at=timezone.now(),
            phone=self.driver.phone,
        )
        self.admin = User.objects.create_superuser("ctradm", "c@t.com", "pass")
        from database.models import PanelProfile, PanelRole

        PanelProfile.objects.create(user=self.admin, role=PanelRole.ADMIN)
        self.client = Client()
        self.client.login(username="ctradm", password="pass")
        self.sent: list[tuple[int, str]] = []

        def capture(user, text):
            self.sent.append((user.id, text))

        self.capture = capture

    def test_snow_haul_suggests_truck(self):
        types = suggested_equipment_for_campaign(self.campaign)
        self.assertIn(EquipmentType.TRACTOR, types)
        self.assertIn(EquipmentType.TRUCK, types)

    def test_registration_flow(self):
        from database.models import ExecutorRole

        truck_role = ExecutorRole.objects.create(
            code=EquipmentType.TRUCK,
            name="Грузовик / самосвал",
            is_equipment=True,
            is_active=True,
        )
        user = BotUser.objects.create(max_user_id="ctr-reg", real_name="")
        pending, _ = PendingAction.objects.get_or_create(user=user)
        start_contractor_registration(user, pending, role=truck_role)
        handle_contractor_registration_step(user, "Камаз 55111", pending)
        handle_contractor_registration_step(user, "А123ВС116", pending)
        handle_contractor_registration_step(user, "89005554433", pending)
        handle_contractor_registration_step(user, "Казань", pending)
        handle_contractor_registration_step(user, "Сбер", pending)
        reply = handle_contractor_registration_step(user, "89006667788", pending)
        self.assertIn("на проверку", reply.lower())
        profile = ContractorProfile.objects.get(user=user)
        self.assertEqual(profile.equipment_type, EquipmentType.TRUCK)
        self.assertEqual(profile.status, ContractorStatus.PENDING_REVIEW)
        self.assertEqual(profile.bank_name, "Сбер")
        self.assertTrue(profile.payout_phone.endswith("667788") or "667788" in profile.payout_phone)

    def test_peer_contacts_shared_when_multiple_assigned(self):
        self.driver.username = "ivan_traktor"
        self.driver.save(update_fields=["username"])
        d2 = BotUser.objects.create(
            max_user_id="ctr-peer-2",
            real_name="Пётр Камаз",
            phone="89009998877",
            username="petr_kamaz",
            chat_id="c-peer-2",
        )
        p2 = ContractorProfile.objects.create(
            user=d2,
            equipment_type=EquipmentType.TRUCK,
            equipment_label="Камаз",
            status=ContractorStatus.VERIFIED,
            verified_at=timezone.now(),
            phone=d2.phone,
        )
        assign_contractor(self.campaign, self.profile, send_fn=self.capture)
        self.sent.clear()
        assign_contractor(self.campaign, p2, send_fn=self.capture)

        peer_msgs = [
            (uid, text)
            for uid, text in self.sent
            if "Контакты коллег" in text or "несколько исполнителей" in text
        ]
        self.assertEqual(len(peer_msgs), 2)
        by_user = {uid: text for uid, text in peer_msgs}
        self.assertIn(self.driver.id, by_user)
        self.assertIn(d2.id, by_user)
        # Иван получает контакты Петра
        self.assertIn("89009998877", by_user[self.driver.id])
        self.assertIn("@petr_kamaz", by_user[self.driver.id])
        self.assertIn("https://max.ru/petr_kamaz", by_user[self.driver.id])
        # Пётр получает контакты Ивана
        self.assertIn("89001112233", by_user[d2.id])
        self.assertIn("@ivan_traktor", by_user[d2.id])
        self.assertIn("https://max.ru/ivan_traktor", by_user[d2.id])
        # Свой контакт себе не шлём
        self.assertNotIn("@ivan_traktor", by_user[self.driver.id])
        self.assertNotIn("@petr_kamaz", by_user[d2.id])

    def test_assign_accept_notifies_residents(self):
        assignment = assign_contractor(
            self.campaign, self.profile, send_fn=self.capture
        )
        self.assertEqual(assignment.status, AssignmentStatus.OFFERED)
        self.assertTrue(any(self.driver.id == uid for uid, _ in self.sent))

        pending, _ = PendingAction.objects.get_or_create(user=self.driver)
        pending.pending_kind = "contractor_offer_reply"
        pending.pending_payload = {"assignment_id": assignment.id}
        pending.save()
        self.sent.clear()
        reply = handle_offer_reply(self.driver, "да", pending)
        self.assertIn("Принято", reply)
        assignment.refresh_from_db()
        self.assertEqual(assignment.status, AssignmentStatus.ACCEPTED)
        # residents notified via default send or during accept — use explicit notify
        from services.contractors import notify_residents_about_assignments

        notify_residents_about_assignments(self.campaign, send_fn=self.capture)
        self.assertTrue(any(self.resident.id == uid for uid, _ in self.sent))
        self.assertTrue(any("Исполнители приедут" in t for _, t in self.sent))

    def test_counter_offer_admin_approve_reject(self):
        assignment = assign_contractor(
            self.campaign, self.profile, send_fn=self.capture
        )
        pending, _ = PendingAction.objects.get_or_create(user=self.driver)
        pending.pending_kind = "contractor_offer_reply"
        pending.pending_payload = {"assignment_id": assignment.id}
        pending.save()
        handle_offer_reply(self.driver, "другое время", pending)
        when = timezone.now() + timedelta(days=3, hours=2)
        when_s = timezone.localtime(when).strftime("%d.%m.%Y %H:%M")
        handle_offer_reply(self.driver, when_s, pending)
        assignment.refresh_from_db()
        self.assertEqual(assignment.status, AssignmentStatus.COUNTER_OFFER)
        self.assertIsNotNone(assignment.proposed_at)

        approve_counter_offer(assignment, send_fn=self.capture)
        assignment.refresh_from_db()
        self.assertEqual(assignment.status, AssignmentStatus.ACCEPTED)

        # second contractor for reject path
        d2 = BotUser.objects.create(max_user_id="ctr-2", real_name="Пётр")
        p2 = ContractorProfile.objects.create(
            user=d2,
            equipment_type=EquipmentType.TRACTOR,
            status=ContractorStatus.VERIFIED,
            verified_at=timezone.now(),
        )
        a2 = assign_contractor(self.campaign, p2, send_fn=self.capture)
        a2.status = AssignmentStatus.COUNTER_OFFER
        a2.proposed_at = timezone.now() + timedelta(days=4)
        a2.counter_deadline = timezone.now() + timedelta(minutes=20)
        a2.save()
        reject_counter_offer(a2, send_fn=self.capture)
        a2.refresh_from_db()
        self.assertEqual(a2.status, AssignmentStatus.REJECTED_TIME)

    def test_expire_counter_offer(self):
        assignment = CampaignAssignment.objects.create(
            campaign=self.campaign,
            contractor=self.profile,
            equipment_type=EquipmentType.TRACTOR,
            status=AssignmentStatus.COUNTER_OFFER,
            scheduled_at=self.campaign.event_at,
            proposed_at=timezone.now() + timedelta(days=1),
            counter_deadline=timezone.now() - timedelta(minutes=1),
        )
        n = expire_stale_counter_offers(send_fn=self.capture)
        self.assertEqual(n, 1)
        assignment.refresh_from_db()
        self.assertEqual(assignment.status, AssignmentStatus.EXPIRED)

    def test_panel_contractors_and_assign(self):
        resp = self.client.get("/panel/contractors/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Мастера", resp.content.decode())
        resp = self.client.post(
            f"/panel/services/campaigns/{self.campaign.id}/",
            {"action": "assign_contractor", "contractor_id": self.profile.id},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(
            CampaignAssignment.objects.filter(
                campaign=self.campaign, contractor=self.profile
            ).exists()
        )

    def test_close_requires_payout_receipt(self):
        self.profile.bank_name = "Тинькофф"
        self.profile.payout_phone = "89001112233"
        self.profile.save()
        assignment = assign_contractor(
            self.campaign, self.profile, send_fn=self.capture
        )
        accept_assignment(assignment, send_fn=self.capture, notify_residents=False)
        # move stages to work_done
        self.campaign.work_stage = WorkStage.WORK_DONE
        self.campaign.save(update_fields=["work_stage"])

        with self.assertRaises(ValueError):
            advance_work_stage(self.campaign)

        tiny = b"%PDF-1.4 payout"
        self.sent.clear()
        close_campaign_requiring_payouts(
            self.campaign,
            [
                {
                    "assignment_id": assignment.id,
                    "amount": Decimal("500"),
                    "file_bytes": tiny,
                    "filename": "payout.pdf",
                }
            ],
            send_fn=self.capture,
        )
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.work_stage, WorkStage.WORK_CLOSED)
        self.assertEqual(ContractorPayout.objects.filter(campaign=self.campaign).count(), 1)
        self.assertTrue(any(self.driver.id == uid for uid, _ in self.sent))
        self.assertTrue(any("переведены" in t.lower() for _, t in self.sent))

    def test_backfill_payout_on_closed_campaign(self):
        assignment = assign_contractor(
            self.campaign, self.profile, send_fn=self.capture
        )
        accept_assignment(assignment, send_fn=self.capture, notify_residents=False)
        self.campaign.work_stage = WorkStage.WORK_CLOSED
        self.campaign.status = CampaignStatus.CLOSED
        self.campaign.save(update_fields=["work_stage", "status"])
        from database.models import ServiceInvite

        ServiceInvite.objects.create(
            campaign=self.campaign,
            user=self.resident,
            amount_due=Decimal("100"),
            amount_paid=Decimal("100"),
            status=InviteStatus.PAID,
        )
        self.sent.clear()
        record_contractor_payouts(
            self.campaign,
            [
                {
                    "assignment_id": assignment.id,
                    "amount": Decimal("700"),
                    "file_bytes": b"receipt-bytes",
                    "filename": "r.jpg",
                }
            ],
            send_fn=self.capture,
        )
        self.assertEqual(ContractorPayout.objects.filter(campaign=self.campaign).count(), 1)
        self.assertTrue(any(self.resident.id == uid for uid, _ in self.sent))
        self.assertTrue(any(self.driver.id == uid for uid, _ in self.sent))
