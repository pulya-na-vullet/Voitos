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
    ContractorProfile,
    ContractorStatus,
    EquipmentType,
    PendingAction,
    ServiceCategory,
    ServiceGroup,
)
from services.contractors import (
    accept_assignment,
    approve_counter_offer,
    assign_contractor,
    expire_stale_counter_offers,
    handle_offer_reply,
    reject_counter_offer,
    suggested_equipment_for_campaign,
)
from services.service import create_campaign
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
        self.admin = User.objects.create_user("ctradm", password="pass")
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
        user = BotUser.objects.create(max_user_id="ctr-reg", real_name="")
        pending, _ = PendingAction.objects.get_or_create(user=user)
        start_contractor_registration(user, pending, equipment_type=EquipmentType.TRUCK)
        handle_contractor_registration_step(user, "Камаз 55111", pending)
        handle_contractor_registration_step(user, "А123ВС116", pending)
        handle_contractor_registration_step(user, "89005554433", pending)
        reply = handle_contractor_registration_step(user, "Казань", pending)
        self.assertIn("отправлена", reply.lower())
        profile = ContractorProfile.objects.get(user=user)
        self.assertEqual(profile.equipment_type, EquipmentType.TRUCK)
        self.assertEqual(profile.status, ContractorStatus.PENDING_REVIEW)

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
        self.assertIn("Исполнители", resp.content.decode())
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
