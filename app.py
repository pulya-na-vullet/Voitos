#!/usr/bin/env python3
"""
Voitos entrypoint.

Starts Django web panel + MAX bot worker + reminder scheduler + daily DB dumps.
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


def sync_env_into_settings() -> None:
    """Seed DB settings from environment on first boot if empty."""
    from django.conf import settings

    from database.models import AppSettings

    cfg = AppSettings.load()
    dirty = False
    if not cfg.max_bot_token and settings.MAX_BOT_TOKEN:
        cfg.max_bot_token = settings.MAX_BOT_TOKEN
        dirty = True
    if not cfg.allowed_max_user_id and settings.ALLOWED_MAX_USER_ID:
        cfg.allowed_max_user_id = settings.ALLOWED_MAX_USER_ID
        dirty = True
    if not cfg.yandex_api_key and settings.YANDEX_API_KEY:
        cfg.yandex_api_key = settings.YANDEX_API_KEY
        dirty = True
    if not cfg.yandex_folder_id and settings.YANDEX_FOLDER_ID:
        cfg.yandex_folder_id = settings.YANDEX_FOLDER_ID
        dirty = True
    if settings.YANDEX_MODEL and (not cfg.yandex_model or cfg.yandex_model == "yandexgpt-lite"):
        if settings.YANDEX_MODEL != cfg.yandex_model:
            cfg.yandex_model = settings.YANDEX_MODEL
            dirty = True
    if dirty:
        cfg.save()
        logger.info("Seeded AppSettings from environment variables")


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

    # Waitress blocks; poll stop_event in a side thread to close later.
    # For MVP we rely on process kill / daemon thread exit.
    serve(application, host=host, port=port, threads=8, channel_timeout=120)


def run_bot(stop_event: threading.Event) -> None:
    from bot.worker import run_bot_worker

    run_bot_worker(stop_event)


def run_reminders(stop_event: threading.Event) -> None:
    from reminders.scheduler import run_reminder_scheduler

    run_reminder_scheduler(stop_event)


def run_dumps(stop_event: threading.Event) -> None:
    from database.dump import run_daily_dump_loop

    run_daily_dump_loop(stop_event)


def _handle_signal(signum, frame) -> None:  # noqa: ARG001
    logger.info("Received signal %s, shutting down...", signum)
    STOP.set()


def main() -> int:
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
            # Watchdog: restart dead threads
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
