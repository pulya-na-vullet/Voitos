from __future__ import annotations

import logging
import time

import django

django.setup()

from ai.factory import get_runtime_settings
from bot.client import MaxClient
from services.contractors import expire_stale_counter_offers
from services.service import process_unpaid_reminders
from services.work_request_dispatch import (
    dispatch_open_requests,
    expire_stale_work_offers,
)
from services.work_request_completion import process_due_scheduled_messages
from services.work_request_rating import backfill_rating_asks
from services.wish_ballot import process_wish_ballots
from services.manager_survey import process_manager_surveys

logger = logging.getLogger(__name__)


def _notify_via_max(user, text: str, client: MaxClient) -> None:
    if user.chat_id:
        try:
            client.send_message(text, chat_id=user.chat_id)
            return
        except Exception:
            logger.exception("chat_id send failed for %s, fallback user_id", user.max_user_id)
    client.send_message(text, user_id=user.max_user_id)


def run_service_campaign_scheduler(stop_event=None, interval_seconds: int = 60) -> None:
    """Remind unpaid invitees before campaign event_at (3d / 1d / 2h)."""
    logger.info("Service campaign scheduler started")
    while True:
        if stop_event is not None and stop_event.is_set():
            logger.info("Service campaign scheduler stopping")
            return
        try:
            cfg = get_runtime_settings()
            token = (cfg.max_bot_token or "").strip()
            if token:
                client = MaxClient(token)

                def send_fn(user, text, _client=client):
                    _notify_via_max(user, text, _client)

                n = process_unpaid_reminders(send_fn=send_fn)
                if n:
                    logger.info("Sent %s unpaid campaign reminder(s)", n)
                expired = expire_stale_counter_offers(send_fn=send_fn)
                if expired:
                    logger.info("Expired %s contractor counter-offer(s)", expired)
                wr_expired = expire_stale_work_offers(send_fn=send_fn)
                if wr_expired:
                    logger.info("Expired %s work-request offer(s)", wr_expired)
                wr_dispatched = dispatch_open_requests(send_fn=send_fn)
                if wr_dispatched:
                    logger.info("Dispatched %s open work request(s)", wr_dispatched)
                queued = process_due_scheduled_messages(send_fn=send_fn)
                if queued:
                    logger.info("Sent %s scheduled bot message(s)", queued)
                rated = backfill_rating_asks(send_fn=send_fn)
                if rated:
                    logger.info("Asked ratings for %s closed work request(s)", rated)
                wish_stats = process_wish_ballots(send_fn=send_fn)
                if wish_stats.get("tallied") or wish_stats.get("started"):
                    logger.info(
                        "Wish ballots: tallied=%s started=%s",
                        wish_stats.get("tallied"),
                        wish_stats.get("started"),
                    )
                mgr_stats = process_manager_surveys(send_fn=send_fn)
                if mgr_stats.get("closed") or mgr_stats.get("started"):
                    logger.info(
                        "Manager surveys: closed=%s started=%s",
                        mgr_stats.get("closed"),
                        mgr_stats.get("started"),
                    )
            else:
                expire_stale_counter_offers()
                expire_stale_work_offers()
                dispatch_open_requests()
                process_due_scheduled_messages()
                backfill_rating_asks()
                process_wish_ballots()
                process_manager_surveys()
        except Exception:
            logger.exception("Service campaign scheduler loop error")
        time.sleep(interval_seconds)


if __name__ == "__main__":
    run_service_campaign_scheduler()
