from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

import requests
from django.test import SimpleTestCase

from bot.client import MaxApiError
from bot.worker import run_bot_worker


class BotWorkerResilienceTests(SimpleTestCase):
    @patch("bot.worker.set_bot_status")
    @patch("bot.worker.set_bot_error")
    @patch("bot.worker.UpdateHandler")
    @patch("bot.worker.MaxClient")
    @patch("bot.worker.get_runtime_settings")
    def test_read_timeout_does_not_set_hard_error(
        self, mock_cfg, mock_client_cls, mock_handler_cls, mock_error, mock_status
    ):
        mock_cfg.return_value = MagicMock(max_bot_token="tok")
        client = MagicMock()
        client.get_me.return_value = {"name": "Bot", "username": "b"}
        client.clear_webhooks.return_value = 0
        client.get_updates.side_effect = [
            requests.exceptions.ReadTimeout("timed out"),
            {"updates": [], "marker": 1},
            KeyboardInterrupt(),  # stop loop for test
        ]
        mock_client_cls.return_value = client
        mock_handler_cls.return_value = MagicMock()

        stop = threading.Event()
        with patch("bot.worker.time.sleep", return_value=None):
            try:
                run_bot_worker(stop)
            except KeyboardInterrupt:
                pass

        mock_error.assert_not_called()
        # polling status after soft timeout
        states = [c.kwargs.get("state") for c in mock_status.call_args_list if c.kwargs]
        self.assertIn("polling", states)

    @patch("bot.worker.set_bot_status")
    @patch("bot.worker.set_bot_error")
    @patch("bot.worker.UpdateHandler")
    @patch("bot.worker.MaxClient")
    @patch("bot.worker.get_runtime_settings")
    def test_429_uses_backoff_without_hard_error_spam(
        self, mock_cfg, mock_client_cls, mock_handler_cls, mock_error, mock_status
    ):
        mock_cfg.return_value = MagicMock(max_bot_token="tok")
        client = MagicMock()
        client.get_me.return_value = {"name": "Bot", "username": "b"}
        client.clear_webhooks.return_value = 0
        client.get_updates.side_effect = [
            MaxApiError("too many", status_code=429, body='{"code":"too.many.requests"}'),
            {"updates": [], "marker": 2},
            KeyboardInterrupt(),
        ]
        mock_client_cls.return_value = client
        mock_handler_cls.return_value = MagicMock()

        sleeps: list[float] = []

        def capture_sleep(sec):
            sleeps.append(sec)

        with patch("bot.worker.time.sleep", side_effect=capture_sleep):
            try:
                run_bot_worker(None)
            except KeyboardInterrupt:
                pass

        self.assertTrue(any(s >= 5 for s in sleeps))
        mock_error.assert_not_called()
