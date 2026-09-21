#!/usr/bin/env python3
"""
Voitos entrypoint.

Starts Django web panel + MAX bot worker + reminder/service schedulers + daily DB dumps.
Runs migrations on every launch and restarts child processes if they crash.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

logging.basicConfig(
    level=logging.INFO,
    format="[{asctime}] {levelname} {name}: {message}",
    style="{",
)
logger = logging.getLogger("voitos.app")

STOP = threading.Event()


def _ensure_dependencies() -> None:
    try:
        import django  # noqa: F401
        import requests  # noqa: F401
        import waitress  # noqa: F401
    except ImportError:
        logger.info("Installing Python dependencies...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", str(BASE_DIR / "requirements.txt")])


def run_migrations() -> None:
    import django
    from django.core.management import call_command

    django.setup()
    # Prepare TLS bundle for MAX (certifi + Минцифры)
    try:
        from bot.ssl_utils import ensure_ca_bundle

        ensure_ca_bundle()
    except Exception:
        logger.exception("Could not prepare SSL CA bundle")
    logger.info("Running migrations...")
    call_command("migrate", interactive=False, verbosity=1)
    # Ensure dirs exist
    from django.conf import settings

    Path(settings.DATA_DIR).mkdir(parents=True, exist_ok=True)
    Path(settings.DUMPS_DIR).mkdir(parents=True, exist_ok=True)
    Path(settings.LOGS_DIR).mkdir(parents=True, exist_ok=True)
    Path(settings.MEDIA_ROOT).mkdir(parents=True, exist_ok=True)


def ensure_admin_user() -> None:
    from django.conf import settings
    from django.contrib.auth import get_user_model

    User = get_user_model()
    username = settings.ADMIN_USERNAME
    password = settings.ADMIN_PASSWORD
    email = settings.ADMIN_EMAIL
    user, created = User.objects.get_or_create(
        username=username,
        defaults={"email": email, "is_staff": True, "is_superuser": True},
    )
    if created:
        user.set_password(password)
        user.save()
        logger.info("Created admin user '%s'", username)
    else:
        # Keep staff/superuser flags
        changed = False
        if not user.is_staff or not user.is_superuser:
            user.is_staff = True
            user.is_superuser = True
            changed = True
        if changed:
            user.save()
        logger.info("Admin user '%s' ready", username)

    from database.models import AppSettings

    AppSettings.load()


def _ensure_env_file() -> None:
    """Create .env from .env.example when missing (common on Windows unzip)."""
    env_path = BASE_DIR / ".env"
    example = BASE_DIR / ".env.example"
    if env_path.exists():
        return
    if example.exists():
        env_path.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
        logger.info("Created .env from .env.example — заполните ключи или настройте в панели")
    else:
        env_path.write_text(
            "SECRET_KEY=voitos-local-dev-secret-change-me\n"
            "DEBUG=True\nALLOWED_HOSTS=*\n"
            "ADMIN_USERNAME=admin\nADMIN_PASSWORD=admin\n"
            "HOST=0.0.0.0\nPORT=18765\n"
            "YANDEX_MODEL=yandexgpt-lite\n"
            "MAX_SSL_VERIFY=true\n",
            encoding="utf-8",
        )
        logger.info("Created default .env")


def sync_env_into_settings() -> None:
    """Seed DB settings from environment and repair bad model names (e.g. deepseek)."""
    from ai.factory import repair_ai_settings
    from database.models import AppSettings

    cfg = repair_ai_settings()
    # Multi-user mode: do not keep single-user lock
    if cfg.allowed_max_user_id:
        cfg.allowed_max_user_id = ""
        cfg.save(update_fields=["allowed_max_user_id", "updated_at"])
    if not cfg.payment_phone:
        cfg.payment_phone = "89625507832"
        cfg.save(update_fields=["payment_phone", "updated_at"])
    # Safety net: never leave unpaid users on a gifted full subscription
    try:
        from subscriptions.service import (
            recalculate_approved_receipt_periods,
            revoke_unpaid_subscriptions,
        )

        revoked = revoke_unpaid_subscriptions()
        if revoked:
            logger.info("Startup: revoked unpaid free access for %s user(s)", revoked)
        fixed = recalculate_approved_receipt_periods()
        if fixed:
            logger.info("Startup: recalculated %s approved receipt period(s)", fixed)
    except Exception:
        logger.exception("Startup: failed subscription maintenance")
    logger.info(
        "AI settings: model=%s folder_set=%s key_set=%s multi_user=on",
        cfg.yandex_model,
        bool(cfg.yandex_folder_id),
        bool(cfg.yandex_api_key),
    )


@dataclass
class ManagedProcess:
    name: str
    target: callable
    thread: threading.Thread | None = None
    stop_event: threading.Event = field(default_factory=threading.Event)
    restart_delay: float = 2.0

    def start(self) -> None:
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._runner, name=self.name, daemon=True)
        self.thread.start()
        logger.info("Started process: %s", self.name)

    def _runner(self) -> None:
        while not STOP.is_set() and not self.stop_event.is_set():
            try:
                self.target(self.stop_event)
            except Exception:
                logger.exception("Process %s crashed", self.name)
            if STOP.is_set() or self.stop_event.is_set():
                break
            logger.warning("Restarting process %s in %.1fs...", self.name, self.restart_delay)
            time.sleep(self.restart_delay)

    def stop(self) -> None:
        self.stop_event.set()


def run_web_server(stop_event: threading.Event) -> None:
    from django.conf import settings
    from waitress import serve

    from config.wsgi import application

    host = settings.HOST
    port = int(settings.PORT)
    logger.info("Web panel listening on http://%s:%s/panel/", host, port)
    serve(application, host=host, port=port, threads=8, channel_timeout=120)


def run_bot(stop_event: threading.Event) -> None:
    from bot.worker import run_bot_worker

    run_bot_worker(stop_event)


def run_reminders(stop_event: threading.Event) -> None:
    from reminders.scheduler import run_reminder_scheduler

    run_reminder_scheduler(stop_event)


def run_service_notices(stop_event: threading.Event) -> None:
    from services.scheduler import run_service_campaign_scheduler

    run_service_campaign_scheduler(stop_event)


def run_dumps(stop_event: threading.Event) -> None:
    from database.dump import run_daily_dump_loop

    run_daily_dump_loop(stop_event)


def _handle_signal(signum, frame) -> None:  # noqa: ARG001
    logger.info("Received signal %s, shutting down...", signum)
    STOP.set()


def main() -> int:
    _ensure_env_file()
    _ensure_dependencies()
    run_migrations()
    ensure_admin_user()
    sync_env_into_settings()

    # Initial dump on startup so there is always at least one backup
    try:
        from database.dump import create_db_dump

        create_db_dump()
    except Exception:
        logger.exception("Initial DB dump failed (non-fatal)")

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    processes = [
        ManagedProcess("web", run_web_server),
        ManagedProcess("bot", run_bot),
        ManagedProcess("reminders", run_reminders),
        ManagedProcess("service_notices", run_service_notices),
        ManagedProcess("dumps", run_dumps),
    ]
    for proc in processes:
        proc.start()

    from django.conf import settings

    logger.info("=" * 60)
    logger.info("Voitos is running")
    logger.info("Admin panel: http://%s:%s/panel/", settings.HOST if settings.HOST != "0.0.0.0" else "127.0.0.1", settings.PORT)
    logger.info("Login: %s / %s", settings.ADMIN_USERNAME, settings.ADMIN_PASSWORD)
    logger.info("Configure Yandex AI + MAX token in the panel, then chat in MAX")
    logger.info("=" * 60)

    try:
        while not STOP.is_set():
            for proc in processes:
                if proc.thread is not None and not proc.thread.is_alive() and not STOP.is_set():
                    logger.error("Process thread dead: %s — restarting", proc.name)
                    proc.start()
            time.sleep(2)
    finally:
        for proc in processes:
            proc.stop()
        logger.info("Voitos stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
