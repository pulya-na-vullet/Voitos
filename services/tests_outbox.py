"""MAX outbox: bulk mailings enqueue and a worker flushes them."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from database.models import (
    BotUser,
    CampaignStatus,
    InviteStatus,
    ScheduledBotMessage,
    ServiceCategory,
    ServiceGroup,
)
from services.outbox import KIND_CAMPAIGN_NOTICE, deliver, pending_count, process_outbox
from services.service import (
    broadcast_campaign_message,
    launch_campaign_to_group,
    process_unpaid_reminders,
    resend_to_unpaid,
)


class OutboxQueueTests(TestCase):
    def setUp(self) -> None:
        self.user = BotUser.objects.create(
            max_user_id="ou1",
            real_name="Олег",
            phone="89000000001",
            locality="Куюки",
        )
        self.other = BotUser.objects.create(
            max_user_id="ou2",
            real_name="Кира",
            phone="89000000002",
            locality="Куюки",
        )
        self.group = ServiceGroup.objects.create(name="Очередь")
        self.group.members.add(self.user, self.other)
        self.event_at = timezone.now() + timedelta(days=4)

    def _launch(self, **kwargs):
        defaults = dict(
            category=ServiceCategory.SNOW,
            title="Чистка снега",
            description="двор",
            group=self.group,
            total_amount=Decimal("1000"),
            amount_per_user=Decimal("500"),
            event_at=self.event_at,
        )
        defaults.update(kwargs)
        return launch_campaign_to_group(**defaults)

    def test_deliver_without_send_fn_stays_queued(self):
        ok = deliver(self.user, "привет", kind=KIND_CAMPAIGN_NOTICE)
        self.assertTrue(ok)
        msg = ScheduledBotMessage.objects.get(user=self.user)
        self.assertIsNone(msg.sent_at)
        self.assertEqual(msg.text, "привет")
        self.assertEqual(pending_count(), 1)

    def test_deliver_with_send_fn_flushes_immediately(self):
        inbox = []

        def capture(user, text):
            inbox.append((user.id, text))

        deliver(self.user, "сейчас", kind=KIND_CAMPAIGN_NOTICE, send_fn=capture)
        self.assertEqual(inbox, [(self.user.id, "сейчас")])
        msg = ScheduledBotMessage.objects.get(user=self.user)
        self.assertIsNotNone(msg.sent_at)
        self.assertEqual(process_outbox(send_fn=capture), 0)
        self.assertEqual(len(inbox), 1)

    def test_process_outbox_sends_and_marks(self):
        inbox = []

        def capture(user, text):
            inbox.append((user.id, text))

        deliver(self.user, "раз", kind=KIND_CAMPAIGN_NOTICE)
        deliver(self.other, "два", kind=KIND_CAMPAIGN_NOTICE)
        n = process_outbox(send_fn=capture)
        self.assertEqual(n, 2)
        self.assertEqual({t for _, t in inbox}, {"раз", "два"})
        self.assertEqual(pending_count(), 0)
        self.assertEqual(
            ScheduledBotMessage.objects.filter(sent_at__isnull=False).count(), 2
        )

    def test_failed_send_retries_without_double_mark(self):
        def boom(user, text):
            raise RuntimeError("max down")

        deliver(self.user, "упс", kind=KIND_CAMPAIGN_NOTICE)
        n = process_outbox(send_fn=boom)
        self.assertEqual(n, 0)
        msg = ScheduledBotMessage.objects.get(user=self.user)
        self.assertIsNone(msg.sent_at)
        self.assertIsNone(msg.cancelled_at)
        self.assertEqual(msg.meta.get("attempts"), 1)
        self.assertGreater(msg.send_at, timezone.now())

        inbox = []

        def capture(user, text):
            inbox.append(text)

        msg.send_at = timezone.now()
        msg.save(update_fields=["send_at"])
        self.assertEqual(process_outbox(send_fn=capture), 1)
        self.assertEqual(inbox, ["упс"])

    def test_launch_enqueues_without_inline_send(self):
        campaign, sent = self._launch()
        self.assertEqual(sent, 2)
        pending = ScheduledBotMessage.objects.filter(sent_at__isnull=True)
        self.assertEqual(pending.count(), 2)
        self.assertTrue(all(m.kind == "campaign.offer" for m in pending))

        inbox = []

        def capture(user, text):
            inbox.append(user.id)

        self.assertEqual(process_outbox(send_fn=capture), 2)
        self.assertEqual(set(inbox), {self.user.id, self.other.id})
        campaign.refresh_from_db()
        self.assertEqual(campaign.status, CampaignStatus.ACTIVE)

    def test_resend_and_unpaid_reminders_enqueue(self):
        campaign, _ = self._launch()
        ScheduledBotMessage.objects.all().delete()
        n = resend_to_unpaid(campaign)
        self.assertEqual(n, 2)
        self.assertEqual(pending_count(), 2)
        self.assertTrue(
            ScheduledBotMessage.objects.filter(kind="campaign.resend").exists()
        )

        ScheduledBotMessage.objects.all().delete()
        campaign.invites.filter(user=self.user).update(status=InviteStatus.PAID)
        n_rem = process_unpaid_reminders(
            now=self.event_at - timedelta(days=3, minutes=-1)
        )
        self.assertEqual(n_rem, 1)
        msg = ScheduledBotMessage.objects.get()
        self.assertEqual(msg.user_id, self.other.id)
        self.assertEqual(msg.kind, "campaign.unpaid_remind")
        self.assertIsNone(msg.sent_at)

    def test_broadcast_enqueues_closed_copy(self):
        campaign, _ = self._launch()
        ScheduledBotMessage.objects.all().delete()
        n = broadcast_campaign_message(campaign, "Сбор закрыт.")
        self.assertEqual(n, 2)
        self.assertEqual(pending_count(), 2)
        texts = set(
            ScheduledBotMessage.objects.values_list("text", flat=True)
        )
        self.assertEqual(texts, {"Сбор закрыт."})
