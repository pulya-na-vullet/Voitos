"""Безопасный вывод логов в консоль Windows (cp1251 / UTF-8).

На Windows консоль часто в cp1251: символы вроде «Wi‑Fi» (U+2011) и «→»
ломают logging.emit. Переключаем stdout/stderr на UTF-8 с заменой,
а StreamHandler дополнительно переживает UnicodeEncodeError.
"""

from __future__ import annotations

import logging
import sys


def configure_stdio_utf8() -> None:
    """По возможности перевести stdout/stderr на UTF-8 (errors=replace)."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if not callable(reconfigure):
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            try:
                reconfigure(errors="replace")
            except Exception:
                pass


class SafeStreamHandler(logging.StreamHandler):
    """StreamHandler, который не падает на UnicodeEncodeError."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            stream = self.stream
            try:
                stream.write(msg + self.terminator)
            except UnicodeEncodeError:
                encoding = getattr(stream, "encoding", None) or "ascii"
                safe = (msg + self.terminator).encode(encoding, errors="replace").decode(
                    encoding, errors="replace"
                )
                stream.write(safe)
            self.flush()
        except Exception:
            self.handleError(record)
