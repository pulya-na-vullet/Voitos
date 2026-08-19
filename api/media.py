"""Декодирование base64-вложений из мобильного клиента."""

from __future__ import annotations

import base64


def decode_base64_payload(raw: str, *, max_bytes: int = 12 * 1024 * 1024) -> bytes | None:
    """Вернуть байты или None при ошибке. Поддерживает data:…;base64,…"""
    raw = (raw or "").strip()
    if not raw:
        return None
    if "," in raw and raw.lower().startswith("data:"):
        raw = raw.split(",", 1)[1]
    try:
        data = base64.b64decode(raw, validate=False)
    except Exception:
        return None
    if not data or len(data) > max_bytes:
        return None
    return data
