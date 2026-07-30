from __future__ import annotations

import logging
import time

import django

django.setup()

from ai.factory import get_runtime_settings
from bot.client import MaxApiError, MaxClient
from bot.handler import UpdateHandler

logger = logging.getLogger(__name__)


def run_bot_worker(stop_event=None) -> None:
    """Long-polling loop for MAX updates. Restarts internally on transient errors."""
    marker = None
    client: MaxClient | None = None
    handler: UpdateHandler | None = None
    last_token = ""

    logger.info("Bot worker started")
    while True:
        if stop_event is not None and stop_event.is_set():
            logger.info("Bot worker stopping")
            return

        cfg = get_runtime_settings()
        token = (cfg.max_bot_token or "").strip()
        if not token:
            logger.warning("MAX bot token not configured. Waiting...")
            time.sleep(5)
            continue

        if token != last_token or client is None:
            client = MaxClient(token)
            handler = UpdateHandler(client)
            last_token = token
            marker = None
            try:
                me = client.get_me()
                logger.info("Connected to MAX bot: %s", me.get("name") or me.get("username") or me)
            except Exception:
                logger.exception("Failed to call GET /me — check token")
                time.sleep(5)
                continue

        try:
            data = client.get_updates(
                marker=marker,
                limit=100,
                timeout=30,
                types=["message_created", "bot_started"],
            )
            updates = data.get("updates") or []
            if "marker" in data and data["marker"] is not None:
                marker = data["marker"]
            for update in updates:
                try:
                    assert handler is not None
                    handler.handle_update(update)
                except Exception:
                    logger.exception("Failed to handle update")
        except MaxApiError:
            logger.exception("MAX API error in polling loop")
            time.sleep(3)
        except Exception:
            logger.exception("Unexpected bot worker error")
            time.sleep(3)


if __name__ == "__main__":
    run_bot_worker()
