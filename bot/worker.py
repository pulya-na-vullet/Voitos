from __future__ import annotations

import logging
import time

import django

django.setup()

from ai.factory import get_runtime_settings
from bot.client import MaxApiError, MaxClient
from bot.handler import UpdateHandler
from bot.status import set_bot_error, set_bot_status

logger = logging.getLogger(__name__)


def run_bot_worker(stop_event=None) -> None:
    """Long-polling loop for MAX updates. Restarts internally on transient errors."""
    from django.db import close_old_connections

    marker = None
    client: MaxClient | None = None
    handler: UpdateHandler | None = None
    last_token = ""
    empty_polls = 0

    logger.info("Bot worker started")
    set_bot_status(state="starting", detail="Бот запускается...")

    while True:
        close_old_connections()
        if stop_event is not None and stop_event.is_set():
            logger.info("Bot worker stopping")
            set_bot_status(state="stopped", detail="Бот остановлен")
            return

        cfg = get_runtime_settings()
        token = (cfg.max_bot_token or "").strip()
        if not token:
            set_bot_status(state="waiting_token", detail="Токен MAX не задан. Укажите его в настройках.")
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
                bot_name = str(me.get("name") or me.get("first_name") or "")
                bot_username = str(me.get("username") or "")
                removed = client.clear_webhooks()
                detail = f"Подключён: {bot_name or bot_username or 'бот MAX'}"
                if removed:
                    detail += f". Снято webhook-подписок: {removed} (нужно для long polling)"
                set_bot_status(
                    state="connected",
                    detail=detail,
                    bot_name=bot_name,
                    bot_username=bot_username,
                    clear_error=True,
                )
                logger.info("Connected to MAX bot: %s | webhooks cleared: %s", me, removed)
            except Exception as exc:
                set_bot_error(f"Не удалось подключиться к MAX (GET /me): {exc}")
                logger.exception("Failed to call GET /me — check token")
                client = None
                time.sleep(5)
                continue

        try:
            assert client is not None and handler is not None
            # First call without marker only returns the latest event.
            # After that we always pass marker so new messages are not lost.
            data = client.get_updates(
                marker=marker,
                limit=100,
                timeout=30,
                types=["message_created", "bot_started"],
            )
            updates = data.get("updates") or []
            if "marker" in data and data["marker"] is not None:
                try:
                    marker = int(data["marker"])
                except (TypeError, ValueError):
                    marker = data["marker"]

            set_bot_status(
                state="polling",
                detail=f"Long polling активен. Последний опрос: {len(updates)} событий",
                last_marker=marker if isinstance(marker, int) else None,
                touch_poll=True,
                clear_error=True,
            )

            if updates:
                empty_polls = 0
                logger.info("Received %s MAX updates (marker=%s)", len(updates), marker)
                for update in updates:
                    try:
                        handler.handle_update(update)
                        set_bot_status(
                            state="polling",
                            detail="Обработано входящее событие",
                            touch_update=True,
                        )
                    except Exception:
                        logger.exception("Failed to handle update: %s", update)
            else:
                empty_polls += 1
                if empty_polls % 10 == 0:
                    logger.info("No updates yet (empty polls=%s, marker=%s)", empty_polls, marker)

        except MaxApiError as exc:
            body = (exc.body or "").lower()
            # If webhook blocks polling, force clear and retry
            if exc.status_code in {409, 400} or "webhook" in body or "subscription" in body:
                logger.warning("Polling blocked — clearing webhooks and retrying")
                try:
                    assert client is not None
                    client.clear_webhooks()
                except Exception:
                    logger.exception("Webhook cleanup failed")
            set_bot_error(f"Ошибка MAX API: {exc}")
            time.sleep(3)
        except Exception as exc:
            set_bot_error(f"Сбой воркера бота: {exc}")
            logger.exception("Unexpected bot worker error")
            time.sleep(3)


if __name__ == "__main__":
    run_bot_worker()
