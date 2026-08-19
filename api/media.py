"""Декодирование base64-вложений из мобильного клиента."""

from __future__ import annotations

import base64
import re
import uuid
from pathlib import PurePosixPath


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


_SAFE_EXT = re.compile(r"^[a-z0-9]{1,8}$", re.IGNORECASE)


def unique_upload_filename(original: str | None, *, default_ext: str = "jpg") -> str:
    """Уникальное имя файла, чтобы повторные загрузки не затирали друг друга."""
    name = (original or "").strip().replace("\\", "/")
    stem = PurePosixPath(name).name if name else ""
    ext = ""
    if "." in stem:
        maybe = stem.rsplit(".", 1)[-1].lower()
        if _SAFE_EXT.match(maybe):
            ext = maybe
    if not ext:
        ext = default_ext.lstrip(".") or "jpg"
    return f"wr_{uuid.uuid4().hex[:16]}.{ext}"
