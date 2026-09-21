from __future__ import annotations

import logging
import shutil
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)


def create_db_dump() -> Path:
    """Copy SQLite database into daily dump folder."""
    db_path = Path(settings.DATABASES["default"]["NAME"])
    dumps_dir = Path(settings.DUMPS_DIR)
    dumps_dir.mkdir(parents=True, exist_ok=True)
    stamp = timezone.localtime().strftime("%Y%m%d_%H%M%S")
    target = dumps_dir / f"voitos_{stamp}.sqlite3"
    if not db_path.exists():
        raise FileNotFoundError(f"Database file not found: {db_path}")
    shutil.copy2(db_path, target)
    # Also keep a rolling "latest" pointer
    latest = dumps_dir / "latest.sqlite3"
    shutil.copy2(db_path, latest)
    logger.info("Database dump created: %s", target)
    _cleanup_old_dumps(dumps_dir, keep=30)
    return target


def _cleanup_old_dumps(dumps_dir: Path, keep: int = 30) -> None:
    files = sorted(
        [p for p in dumps_dir.glob("voitos_*.sqlite3")],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for old in files[keep:]:
        try:
            old.unlink()
        except OSError:
            logger.warning("Could not delete old dump %s", old)


def run_daily_dump_loop(stop_event=None) -> None:
    """Create a dump once per calendar day (local time)."""
    import time

    last_day: str | None = None
    logger.info("Daily dump worker started")
    while True:
        if stop_event is not None and stop_event.is_set():
            return
        today = timezone.localtime().strftime("%Y-%m-%d")
        if today != last_day:
            try:
                create_db_dump()
                last_day = today
            except Exception:
                logger.exception("Daily dump failed")
        time.sleep(60)


if __name__ == "__main__":
    import django

    django.setup()
    print(create_db_dump())
