"""Tests for Windows-safe console logging."""

from __future__ import annotations

import io
import logging
import unittest

from config.logging_utf8 import SafeStreamHandler


class SafeStreamHandlerTests(unittest.TestCase):
    def test_emits_non_cp1251_chars_without_raising(self):
        buf = io.TextIOWrapper(io.BytesIO(), encoding="cp1251", errors="strict")
        handler = SafeStreamHandler(buf)
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger = logging.getLogger("test.safe_stream")
        logger.handlers.clear()
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
        # U+2011 non-breaking hyphen + U+2192 arrow — break raw cp1251.
        logger.info("LAN Wi‑Fi clients OK → dry-run")
        buf.flush()
        raw = buf.buffer.getvalue()
        self.assertTrue(raw)
        logger.handlers.clear()
