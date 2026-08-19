from __future__ import annotations

import logging
import time

import django

django.setup()

from ai.factory import get_runtime_settings
from bot.client import MaxClient
from reminders.service import ReminderService

logger = logging.getLogger(__name__)


def run_reminder_scheduler(stop_event=None, interval_seconds: int = 20) -> None:
    service = ReminderService()
    logger.info("Reminder scheduler started")
    while True:
        if stop_event is not None and stop_event.is_set():
            logger.info("Reminder scheduler stopping")
            return
        try:
            cfg = get_runtime_settings()
            token = (cfg.max_bot_token or "").strip()
            if not token:
                time.sleep(interval_seconds)
                continue
            client = MaxClient(token)
            due = service.list_due()
            for reminder in due:
                user = reminder.user
                from subscriptions.renewal_reminders import strip_renewal_marker

                system_text = strip_renewal_marker(reminder.text)
                text = system_text if system_text is not None else f"Напоминание: {reminder.text}"
                max_ok = False
                try:
                    if user.chat_id:
                        client.send_message(text, chat_id=user.chat_id)
                    else:
                        client.send_message(text, user_id=user.max_user_id)
                    max_ok = True
                    logger.info("Sent reminder #%s to user %s", reminder.id, user.max_user_id)
                except Exception:
                    logger.exception("Failed to send reminder #%s", reminder.id)
                # Продление: inbox/FCM даже если MAX недоступен (мобильный клиент).
                if max_ok or system_text is not None:
                    try:
                        service.mark_sent(reminder)
                    except Exception:
                        logger.exception("mark_sent failed reminder #%s", reminder.id)
        except Exception:
            logger.exception("Reminder scheduler loop error")
        time.sleep(interval_seconds)


if __name__ == "__main__":
    run_reminder_scheduler()
