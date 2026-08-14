"""Сетевые адреса панели: LAN Wi‑Fi и публичный URL стенда."""

from __future__ import annotations

import logging
import socket
from urllib.parse import urlparse

from django.conf import settings

logger = logging.getLogger(__name__)

WIFI_WORKSHOP_NOTE = "(работает из под WI-FI сети ИТ-Мастерской)"


def detect_lan_ip() -> str:
    """Локальный IP в текущей сети (для доступа с других устройств по Wi‑Fi)."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            # Не нужна реальная доступность — только выбор исходящего интерфейса.
            sock.connect(("8.8.8.8", 80))
            ip = sock.getsockname()[0]
        finally:
            sock.close()
        if ip and not ip.startswith("127."):
            return ip
    except OSError:
        pass
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            candidate = info[4][0]
            if candidate and not candidate.startswith("127."):
                return candidate
    except OSError:
        pass
    return "127.0.0.1"


def panel_bind_host() -> str:
    host = (getattr(settings, "HOST", None) or "0.0.0.0").strip()
    return host or "0.0.0.0"


def panel_port() -> int:
    return int(getattr(settings, "PORT", 18765) or 18765)


def panel_public_url() -> str:
    """Явный URL развёрнутого стенда из .env (без хвоста /)."""
    raw = (getattr(settings, "PANEL_PUBLIC_URL", None) or "").strip()
    return raw.rstrip("/")


def is_local_lan_mode() -> bool:
    """True — панель в локальной сети; False — задан публичный URL стенда."""
    return not bool(panel_public_url())


def panel_access_origin() -> str:
    """Origin для CSRF / ссылок: публичный URL или http://LAN_IP:PORT."""
    public = panel_public_url()
    if public:
        parsed = urlparse(public if "://" in public else f"https://{public}")
        scheme = parsed.scheme or "https"
        netloc = parsed.netloc or parsed.path.split("/")[0]
        return f"{scheme}://{netloc}".rstrip("/")

    bind = panel_bind_host()
    if bind in {"0.0.0.0", "::", "[::]", "*"}:
        host = detect_lan_ip()
    else:
        host = bind
    return f"http://{host}:{panel_port()}"


def panel_login_url() -> str:
    return f"{panel_access_origin()}/panel/login/"


def panel_access_hint() -> str:
    """Подпись под URL в сообщении менеджеру."""
    if is_local_lan_mode():
        return WIFI_WORKSHOP_NOTE
    return ""


def ensure_network_csrf_trusted() -> str:
    """
    Добавляет LAN/публичный origin в CSRF_TRUSTED_ORIGINS,
    чтобы логин с телефонов в той же Wi‑Fi сети не падал на CSRF.
    Возвращает итоговый origin доступа.
    """
    origin = panel_access_origin()
    trusted = list(getattr(settings, "CSRF_TRUSTED_ORIGINS", []) or [])
    extras = [origin]
    # На всякий случай localhost для админа на той же машине
    port = panel_port()
    extras.extend(
        [
            f"http://127.0.0.1:{port}",
            f"http://localhost:{port}",
        ]
    )
    changed = False
    for item in extras:
        if item and item not in trusted:
            trusted.append(item)
            changed = True
    if changed:
        settings.CSRF_TRUSTED_ORIGINS = trusted
        logger.info("CSRF_TRUSTED_ORIGINS updated for panel access: %s", origin)
    return origin
