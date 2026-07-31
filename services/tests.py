from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from database.models import (
    AdminTask,
    AdminTaskKind,
    AdminTaskStatus,
    BotUser,
    CampaignNoticeKind,
    CampaignStatus,
    InviteStatus,
    PendingAction,
    ProfileStatus,
    ReceiptStatus,
    ServiceCampaignNotice,
    ServiceCategory,
    ServiceGroup,
    ServiceReceipt,
    WorkStage,
)
from bot.registration import handle_registration_step, start_registration
from services.ranking import citizen_stats, rank_label, ranking_list
from services.service import (
    advance_work_stage,
    approve_service_receipt,
    format_collections_for_user,
    format_receipt_pick_menu,
    invite_new_members_to_group_campaigns,
    launch_campaign_to_group,
    open_invites_for_user,
    process_unpaid_reminders,
    resend_to_unpaid,
    user_groups_list_message,
)


class RegistrationTests(TestCase):
    def setUp(self) -> None:
        self.user = BotUser.objects.create(max_user_id="u1")
        self.pending, _ = PendingAction.objects.get_or_create(user=self.user)

    def test_registration_flow(self):
        msg = start_registration(self.user, self.pending)
        self.assertIn("зовут", msg.lower())
        handle_registration_step(self.user, "Иван Петров", self.pending)
        handle_registration_step(self.user, "89625501111", self.pending)
        reply = handle_registration_step(
            self.user, "Казань, ул. Баумана, 1", self.pending
        )
        self.user.refresh_from_db()
        self.assertEqual(self.user.real_name, "Иван Петров")
        self.assertEqual(self.user.phone, "89625501111")
        self.assertEqual(self.user.locality, "Казань")
        self.assertEqual(self.user.profile_status, ProfileStatus.PENDING_REVIEW)
        self.assertIn("Администратор проверит", reply)

    def test_incomplete_profile_message_lists_fields(self):
        from bot.registration import begin_incomplete_profile_flow, missing_profile_fields

        self.user.real_name = "Иван"
        self.user.save()
        missing = missing_profile_fields(self.user)
        labels = [label for _, label in missing]
        self.assertIn("Телефон", labels)
        self.assertIn("Адрес места жительства", labels)
        text = begin_incomplete_profile_flow(self.user, self.pending, admin_note="нужен телефон")
        self.assertIn("Администратор проверил", text)
        self.assertIn("Телефон", text)
        self.assertIn("нужен телефон", text)

    def test_family_check_on_duplicate_address_without_leaking_data(self):
        BotUser.objects.create(
            max_user_id="neighbor",
            real_name="Секретный Сосед",
            phone="89001112233",
            address="Казань, ул. Баумана, 1",
            locality="Казань",
            profile_status=ProfileStatus.VERIFIED,
        )
        start_registration(self.user, self.pending)
        handle_registration_step(self.user, "Иван Петров", self.pending)
        handle_registration_step(self.user, "89625501111", self.pending)
        reply = handle_registration_step(
            self.user, "Казань, ул. Баумана, 1", self.pending
        )
        self.assertIn("членом семьи", reply.lower())
        self.assertNotIn("Секретный", reply)
        self.assertNotIn("89001112233", reply)
        self.pending.refresh_from_db()
        self.assertEqual(self.pending.pending_payload.get("step"), "family_check")

        final = handle_registration_step(self.user, "да", self.pending)
        self.assertIn("Администратор", final)
        self.user.refresh_from_db()
        self.assertEqual(self.user.profile_status, ProfileStatus.PENDING_REVIEW)
        task = AdminTask.objects.filter(
            kind=AdminTaskKind.FAMILY_CLAIM,
            user=self.user,
            status=AdminTaskStatus.OPEN,
        ).first()
        self.assertIsNotNone(task)
        self.assertTrue(task.meta.get("claimed_family"))
        self.assertTrue(
            AdminTask.objects.filter(
                kind=AdminTaskKind.PROFILE_REVIEW, user=self.user
            ).exists()
        )


class ServiceCampaignTests(TestCase):
    def setUp(self) -> None:
        self.user = BotUser.objects.create(
            max_user_id="u2",
            real_name="Анна",
            phone="89000000000",
            address="Казань, ул. А, 2",
            locality="Казань",
            profile_status=ProfileStatus.VERIFIED,
        )
        self.group = ServiceGroup.objects.create(name="ул. А")
        self.group.members.add(self.user)
        self.event_at = timezone.now() + timedelta(days=5)

    def _launch(self, **kwargs):
        defaults = dict(
            category=ServiceCategory.SNOW,
            title="Чистка снега",
            description="двор",
            group=self.group,
            total_amount=Decimal("10000"),
            amount_per_user=Decimal("500"),
            event_at=self.event_at,
        )
        defaults.update(kwargs)
        return launch_campaign_to_group(**defaults)

    def test_launch_to_group(self):
        campaign, sent = self._launch()
        self.assertEqual(sent, 1)
        self.assertIn("от ", campaign.title)
        self.assertEqual(campaign.status, CampaignStatus.ACTIVE)
        self.assertEqual(campaign.group_id, self.group.id)
        self.assertEqual(campaign.event_at, self.event_at)
        inv = campaign.invites.get(user=self.user)
        self.assertEqual(inv.amount_due, Decimal("500"))

        text = format_collections_for_user(self.user)
        self.assertIn("Чистка снега", text)
        self.assertIn("500", text)

    def test_receipt_pick_menu_starts_with_subscription(self):
        self._launch(
            category=ServiceCategory.ROAD,
            title="Укладка асфальта",
            amount_per_user=Decimal("12000"),
        )
        self._launch(
            category=ServiceCategory.SNOW,
            title="Чистка снега",
            amount_per_user=Decimal("500"),
        )
        invites = open_invites_for_user(self.user)
        self.assertEqual(len(invites), 2)
        menu = format_receipt_pick_menu(invites)
        lines = menu.splitlines()
        self.assertEqual(lines[1], "1. Подписка")
        self.assertTrue(lines[2].startswith("2. "))
        self.assertTrue(lines[3].startswith("3. "))
        self.assertIn("Подписка", menu)
        # subscription must be first numbered item
        self.assertLess(menu.index("1. Подписка"), menu.index("2. "))

    def test_launch_empty_group_fails(self):
        empty = ServiceGroup.objects.create(name="пусто")
        with self.assertRaises(ValueError):
            launch_campaign_to_group(
                category=ServiceCategory.SNOW,
                title="Снег",
                description="",
                group=empty,
                total_amount=Decimal("1000"),
                amount_per_user=Decimal("100"),
                event_at=self.event_at,
            )

    def test_launch_requires_event_at(self):
        with self.assertRaises(ValueError):
            launch_campaign_to_group(
                category=ServiceCategory.SNOW,
                title="Снег",
                description="",
                group=self.group,
                total_amount=Decimal("1000"),
                amount_per_user=Decimal("100"),
            )

    def test_group_join_message_lists_groups(self):
        other = ServiceGroup.objects.create(name="двор 2")
        other.members.add(self.user)
        text = user_groups_list_message(self.user, added_group=self.group)
        self.assertIn("ул. А", text)
        self.assertIn("двор 2", text)
        self.assertIn("Вас добавили в группу", text)

    def test_new_member_gets_active_campaigns_only(self):
        campaign, _ = self._launch(
            title="Чистка снега",
            description="",
            total_amount=Decimal("3000"),
            amount_per_user=Decimal("300"),
        )
        newbie = BotUser.objects.create(
            max_user_id="u3",
            real_name="Борис",
            profile_status=ProfileStatus.VERIFIED,
        )
        sent = []

        def capture(user, text):
            sent.append((user.id, text))

        n = invite_new_members_to_group_campaigns(
            self.group, [newbie.id], send_fn=capture
        )
        self.assertEqual(n, 1)
        self.assertEqual(sent[0][0], newbie.id)
        self.assertIn("Чистка снега", sent[0][1])
        self.assertTrue(
            campaign.invites.filter(user=newbie, status=InviteStatus.OFFERED).exists()
        )
        self.assertEqual(campaign.invites.filter(user=self.user).count(), 1)

    def test_goal_reached_closes_and_notifies(self):
        other = BotUser.objects.create(max_user_id="u4", real_name="Олег")
        self.group.members.add(other)
        campaign, _ = self._launch(
            total_amount=Decimal("500"),
            amount_per_user=Decimal("500"),
        )
        invite = campaign.invites.get(user=self.user)
        receipt = ServiceReceipt.objects.create(
            invite=invite,
            campaign=campaign,
            user=self.user,
            amount=Decimal("500"),
            status=ReceiptStatus.PENDING,
        )
        inbox = []

        def capture(user, text):
            inbox.append((user.id, text))

        approve_service_receipt(receipt, send_fn=capture)
        campaign.refresh_from_db()
        self.assertEqual(campaign.status, CampaignStatus.CLOSED)
        closed_msgs = [t for _, t in inbox if t.strip() == "Сбор закрыт."]
        self.assertEqual(len(closed_msgs), 2)  # both invitees
        self.assertTrue(
            ServiceCampaignNotice.objects.filter(
                campaign=campaign, kind=CampaignNoticeKind.CLOSED
            ).exists()
        )

    def test_surplus_goes_to_budget_notice(self):
        from services.service import group_accumulated_budget

        campaign, _ = self._launch(
            total_amount=Decimal("400"),
            amount_per_user=Decimal("500"),
        )
        invite = campaign.invites.get(user=self.user)
        receipt = ServiceReceipt.objects.create(
            invite=invite,
            campaign=campaign,
            user=self.user,
            amount=Decimal("500"),
            status=ReceiptStatus.PENDING,
        )
        inbox = []

        def capture(user, text):
            inbox.append(text)

        approve_service_receipt(receipt, send_fn=capture)
        campaign.refresh_from_db()
        self.assertEqual(campaign.status, CampaignStatus.CLOSED)
        self.assertTrue(any("общий бюджет" in t for t in inbox))
        self.assertTrue(
            ServiceCampaignNotice.objects.filter(
                campaign=campaign, kind=CampaignNoticeKind.SURPLUS
            ).exists()
        )
        self.assertEqual(group_accumulated_budget(self.group), Decimal("100"))

    def test_launch_with_offer_photos_broadcast(self):
        tiny_png = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
            b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00"
            b"\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N"
            b"\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        media = []

        def capture_media(user, text, images):
            media.append((user.id, text, len(images)))

        campaign, sent = self._launch(
            photo_uploads=[(tiny_png, "task.png")],
            send_media_fn=capture_media,
        )
        self.assertEqual(sent, 1)
        self.assertEqual(campaign.offer_photos.count(), 1)
        self.assertEqual(len(media), 1)
        self.assertEqual(media[0][2], 1)
        self.assertIn("фото того, что нужно сделать", media[0][1])

    def test_work_stage_flow_and_result_photos(self):
        campaign, _ = self._launch()
        self.assertEqual(campaign.work_stage, WorkStage.COLLECTING)
        advance_work_stage(campaign)
        campaign.refresh_from_db()
        self.assertEqual(campaign.work_stage, WorkStage.WORK_STARTED)

        inbox = []
        media = []

        def capture(user, text):
            inbox.append((user.id, text))

        def capture_media(user, text, images):
            media.append((user.id, text, len(images)))

        tiny_png = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
            b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00"
            b"\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N"
            b"\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        advance_work_stage(
            campaign,
            photo_uploads=[(tiny_png, "a.png"), (tiny_png, "b.png")],
            send_fn=capture,
            send_media_fn=capture_media,
        )
        campaign.refresh_from_db()
        self.assertEqual(campaign.work_stage, WorkStage.WORK_DONE)
        self.assertEqual(campaign.result_photos.count(), 2)
        self.assertEqual(len(media), 1)
        self.assertIn("Работа выполнена", media[0][1])
        self.assertEqual(media[0][2], 2)

        advance_work_stage(campaign)
        campaign.refresh_from_db()
        self.assertEqual(campaign.work_stage, WorkStage.WORK_CLOSED)
        self.assertEqual(campaign.status, CampaignStatus.CLOSED)
        with self.assertRaises(ValueError):
            advance_work_stage(campaign)

    def test_resend_skips_paid_and_uses_reminder_text(self):
        other = BotUser.objects.create(max_user_id="u5", real_name="Кира")
        self.group.members.add(other)
        campaign, _ = self._launch(
            total_amount=Decimal("1000"),
            amount_per_user=Decimal("500"),
        )
        paid_inv = campaign.invites.get(user=self.user)
        paid_inv.status = InviteStatus.PAID
        paid_inv.amount_paid = Decimal("500")
        paid_inv.save()
        inbox = []

        def capture(user, text):
            inbox.append((user.id, text))

        n = resend_to_unpaid(campaign, send_fn=capture)
        self.assertEqual(n, 1)
        self.assertEqual(inbox[0][0], other.id)
        self.assertIn("Напоминаю вам", inbox[0][1])
        self.assertNotIn("Начат сбор", inbox[0][1])

    def test_unpaid_reminders_before_event(self):
        campaign, _ = self._launch(event_at=timezone.now() + timedelta(hours=3))
        inbox = []

        def capture(user, text):
            inbox.append((user.id, text))

        # 2h window already open (event in 3h); 1d and 3d also open
        n = process_unpaid_reminders(send_fn=capture, now=timezone.now())
        self.assertGreaterEqual(n, 1)
        self.assertTrue(any("не оплатили сбор" in t.lower() for _, t in inbox))
        # Idempotent
        n2 = process_unpaid_reminders(send_fn=capture, now=timezone.now())
        self.assertEqual(n2, 0)
        # Paid users are skipped for remaining windows if we reset somehow —
        # after first run all 3 kinds sent; mark paid and ensure no more
        campaign.invites.update(status=InviteStatus.PAID)
        n3 = process_unpaid_reminders(send_fn=capture, now=timezone.now())
        self.assertEqual(n3, 0)

    def test_rank_labels(self):
        self.assertEqual(rank_label(85), "Образцовый гражданин")
        self.assertEqual(rank_label(60), "Хороший гражданин")
        self.assertEqual(rank_label(40), "Пассивный гражданин")
        self.assertEqual(rank_label(10), "Неактивный гражданин")

    def test_ranking_includes_users_without_offers(self):
        idle = BotUser.objects.create(max_user_id="idle", real_name="Антон", locality="Куюки")
        rows = ranking_list()
        ids = {r.user.id for r in rows}
        self.assertIn(self.user.id, ids)
        self.assertIn(idle.id, ids)
        idle_row = next(r for r in rows if r.user.id == idle.id)
        self.assertEqual(idle_row.offered, 0)
        self.assertEqual(idle_row.label, "Неактивный гражданин")
        by_loc = ranking_list(locality="Куюки")
        self.assertTrue(all("куюки" in (r.user.locality or "").lower() for r in by_loc))

    def test_citizen_stats(self):
        campaign, _ = self._launch(
            category=ServiceCategory.ROAD,
            title="Ремонт",
            description="",
            total_amount=Decimal("5000"),
            amount_per_user=Decimal("100"),
        )
        inv = campaign.invites.get()
        inv.status = InviteStatus.PAID
        inv.amount_paid = Decimal("100")
        inv.save()
        stats = citizen_stats(self.user)
        self.assertEqual(stats.offered, 1)
        self.assertEqual(stats.paid, 1)
        self.assertEqual(stats.label, "Образцовый гражданин")


class ServiceIntentTests(TestCase):
    def test_collections_command(self):
        from ai.intent import IntentAnalyzer

        r = IntentAnalyzer().analyze("сборы")
        self.assertEqual(r.intent, "service_collections")
