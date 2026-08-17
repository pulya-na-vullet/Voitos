from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from unittest.mock import MagicMock

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.utils import timezone

from database.models import (
    AdminTask,
    AdminTaskKind,
    AdminTaskStatus,
    BotUser,
    PanelProfile,
    PanelRole,
    PendingAction,
    ServiceCategory,
    ServiceGroup,
    WishBallotStatus,
    WishPeriod,
    WishPeriodStatus,
    WishTopic,
    WishVote,
)
from services.service import launch_campaign_to_group
from services.wish_ballot import (
    close_period_after_campaign,
    handle_wish_ballot_vote,
    process_wish_ballots,
    start_ballot,
    tally_ballot,
)
from services.wishes import capture_wish


class WishBallotFlowTests(TestCase):
    def setUp(self) -> None:
        self.anna = BotUser.objects.create(
            max_user_id="wb-anna",
            real_name="Анна",
            chat_id="c-anna",
        )
        self.boris = BotUser.objects.create(
            max_user_id="wb-boris",
            real_name="Борис",
            chat_id="c-boris",
        )
        self.group = ServiceGroup.objects.create(name="Лесная")
        self.group.members.add(self.anna, self.boris)
        self.admin = User.objects.create_superuser("wbadm", "w@t.com", "pass")
        PanelProfile.objects.create(user=self.admin, role=PanelRole.ADMIN)
        self.client = Client()
        self.client.login(username="wbadm", password="pass")

    def _seed_wishes(self) -> None:
        capture_wish(self.anna, "Хочу чтобы починили дорогу и ямы", group=self.group)
        capture_wish(self.boris, "Ещё одна яма на дороге", group=self.group)
        capture_wish(self.anna, "Поставьте фонари у подъезда", group=self.group)
        capture_wish(self.boris, "Нужна детская площадка", group=self.group)

    def test_capture_attaches_open_period(self):
        wish = capture_wish(self.anna, "Хочу чтобы починили дорогу", group=self.group)
        self.assertIsNotNone(wish.period_id)
        period = WishPeriod.objects.get(pk=wish.period_id)
        self.assertEqual(period.status, WishPeriodStatus.OPEN)
        self.assertEqual(period.group_id, self.group.id)

    def test_start_ballot_after_interval_sends_and_sets_pending(self):
        self._seed_wishes()
        period = WishPeriod.objects.get(group=self.group, status=WishPeriodStatus.OPEN)
        period.opened_at = timezone.now() - timedelta(days=15)
        period.save(update_fields=["opened_at"])
        sent: list[tuple[str, str]] = []

        def send_fn(user, text):
            sent.append((user.max_user_id, text))

        ballot = start_ballot(self.group, send_fn=send_fn)
        self.assertIsNotNone(ballot)
        assert ballot is not None
        self.assertEqual(ballot.status, WishBallotStatus.VOTING)
        self.assertEqual(len(ballot.options), 3)
        self.assertEqual(len(sent), 2)
        self.assertIn("Голосование", sent[0][1])
        pending = PendingAction.objects.get(user=self.anna)
        self.assertEqual(pending.pending_kind, "wish_ballot_vote")
        self.assertEqual(pending.pending_payload.get("ballot_id"), ballot.id)

    def test_vote_tally_creates_admin_task(self):
        self._seed_wishes()
        period = WishPeriod.objects.get(group=self.group)
        period.opened_at = timezone.now() - timedelta(days=15)
        period.save(update_fields=["opened_at"])
        ballot = start_ballot(self.group, send_fn=lambda *_: None)
        assert ballot is not None

        for user, choice in ((self.anna, "1"), (self.boris, "1")):
            pending, _ = PendingAction.objects.get_or_create(user=user)
            pending.pending_kind = "wish_ballot_vote"
            pending.pending_payload = {"ballot_id": ballot.id}
            pending.save()
            reply = handle_wish_ballot_vote(user, choice, pending)
            self.assertIn("Ваш голос", reply or "")

        ballot.voting_ends_at = timezone.now() - timedelta(minutes=1)
        ballot.save(update_fields=["voting_ends_at"])
        tally_ballot(ballot)
        ballot.refresh_from_db()
        self.assertEqual(ballot.status, WishBallotStatus.WON)
        self.assertEqual(ballot.winner_topic, WishTopic.ROAD)
        task = AdminTask.objects.get(
            kind=AdminTaskKind.WISH_BALLOT,
            source_model="WishBallot",
            source_id=ballot.id,
        )
        self.assertEqual(task.status, AdminTaskStatus.OPEN)
        self.assertIn("wish_ballot=", task.action_url)

    def test_process_tallies_due_ballots(self):
        self._seed_wishes()
        period = WishPeriod.objects.get(group=self.group)
        period.opened_at = timezone.now() - timedelta(days=15)
        period.save(update_fields=["opened_at"])
        ballot = start_ballot(self.group, send_fn=lambda *_: None)
        assert ballot is not None
        WishVote.objects.create(
            ballot=ballot,
            user=self.anna,
            choice_topic=WishTopic.ROAD,
            will_do=True,
        )
        ballot.voting_ends_at = timezone.now() - timedelta(hours=1)
        ballot.save(update_fields=["voting_ends_at"])
        stats = process_wish_ballots(send_fn=lambda *_: None)
        self.assertEqual(stats["tallied"], 1)
        ballot.refresh_from_db()
        self.assertEqual(ballot.status, WishBallotStatus.WON)

    def test_campaign_closes_period_and_opens_new(self):
        self._seed_wishes()
        period = WishPeriod.objects.get(group=self.group)
        period.opened_at = timezone.now() - timedelta(days=15)
        period.save(update_fields=["opened_at"])
        ballot = start_ballot(self.group, send_fn=lambda *_: None)
        assert ballot is not None
        WishVote.objects.create(
            ballot=ballot, user=self.anna, choice_topic=WishTopic.ROAD, will_do=True
        )
        tally_ballot(ballot)
        ballot.refresh_from_db()

        campaign, _ = launch_campaign_to_group(
            category=ServiceCategory.ROAD,
            title="Ремонт дороги",
            description="По итогам голосования",
            group=self.group,
            total_amount=Decimal("1000"),
            amount_per_user=Decimal("500"),
            event_at=timezone.now() + timedelta(days=7),
            send_fn=lambda *_: None,
        )
        new_period = close_period_after_campaign(ballot, campaign)
        period.refresh_from_db()
        ballot.refresh_from_db()
        self.assertEqual(period.status, WishPeriodStatus.CLOSED)
        self.assertEqual(ballot.status, WishBallotStatus.COMPLETED)
        self.assertEqual(ballot.campaign_id, campaign.id)
        self.assertEqual(new_period.status, WishPeriodStatus.OPEN)
        self.assertNotEqual(new_period.id, period.id)
        task = AdminTask.objects.get(source_model="WishBallot", source_id=ballot.id)
        self.assertEqual(task.status, AdminTaskStatus.DONE)

    def test_services_prefill_from_ballot(self):
        self._seed_wishes()
        period = WishPeriod.objects.get(group=self.group)
        period.opened_at = timezone.now() - timedelta(days=15)
        period.save(update_fields=["opened_at"])
        ballot = start_ballot(self.group, send_fn=lambda *_: None)
        assert ballot is not None
        WishVote.objects.create(
            ballot=ballot, user=self.anna, choice_topic=WishTopic.ROAD, will_do=True
        )
        tally_ballot(ballot)
        resp = self.client.get(f"/panel/services/?wish_ballot={ballot.id}&group_id={self.group.id}")
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        self.assertIn(f'name="wish_ballot_id" value="{ballot.id}"', body)
        self.assertIn("Дороги", body)

    def test_no_votes_cancels_without_task(self):
        self._seed_wishes()
        period = WishPeriod.objects.get(group=self.group)
        period.opened_at = timezone.now() - timedelta(days=15)
        period.save(update_fields=["opened_at"])
        ballot = start_ballot(self.group, send_fn=lambda *_: None)
        assert ballot is not None
        WishVote.objects.create(
            ballot=ballot, user=self.anna, choice_topic="", will_do=False
        )
        tally_ballot(ballot)
        ballot.refresh_from_db()
        self.assertEqual(ballot.status, WishBallotStatus.CANCELLED)
        self.assertFalse(
            AdminTask.objects.filter(
                kind=AdminTaskKind.WISH_BALLOT, source_id=ballot.id
            ).exists()
        )
