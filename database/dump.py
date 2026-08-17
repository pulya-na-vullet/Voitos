"""Дампы SQLite и автовосстановление, если рабочая БД оказалась пустой."""

from __future__ import annotations

import logging
import shutil
import sqlite3
from pathlib import Path

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

# Считаем дамп «с данными», если есть хотя бы один житель бота.
_BOTUSER_TABLE = "database_botuser"


def db_path() -> Path:
    return Path(settings.DATABASES["default"]["NAME"])


def dumps_dir() -> Path:
    path = Path(settings.DUMPS_DIR)
    path.mkdir(parents=True, exist_ok=True)
    return path


def latest_dump_path() -> Path:
    return dumps_dir() / "latest.sqlite3"


def sqlite_botuser_count(path: Path) -> int:
    """Сколько жителей в sqlite-файле (0 если таблицы нет / файл битый)."""
    if not path.exists() or path.stat().st_size < 1024:
        return 0
    try:
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    except sqlite3.Error:
        return 0
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (_BOTUSER_TABLE,),
        ).fetchone()
        if not row:
            return 0
        return int(conn.execute(f"SELECT COUNT(*) FROM {_BOTUSER_TABLE}").fetchone()[0])
    except sqlite3.Error:
        return 0
    finally:
        conn.close()


def dump_has_data(path: Path | None) -> bool:
    if not path:
        return False
    return sqlite_botuser_count(path) > 0


def is_working_db_empty() -> bool:
    """Текущая рабочая БД без жителей (после миграций / свежий wipe)."""
    # Сначала ORM — работает и для in-memory SQLite в тестах.
    try:
        from database.models import BotUser

        return BotUser.objects.count() == 0
    except Exception:
        path = db_path()
        if not path.exists():
            return True
        return sqlite_botuser_count(path) == 0


def list_dump_files() -> list[Path]:
    """Все дампы: latest + voitos_*.sqlite3, новые первые."""
    d = dumps_dir()
    dated = sorted(
        d.glob("voitos_*.sqlite3"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    latest = latest_dump_path()
    out: list[Path] = []
    if latest.exists():
        out.append(latest)
    for p in dated:
        if p.resolve() != latest.resolve():
            out.append(p)
    return out


def find_best_nonempty_dump() -> Path | None:
    """Лучший дамп с данными: сначала latest, иначе самый свежий dated."""
    latest = latest_dump_path()
    if dump_has_data(latest):
        return latest
    candidates = sorted(
        dumps_dir().glob("voitos_*.sqlite3"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for path in candidates:
        if dump_has_data(path):
            return path
    return None


def create_db_dump(*, force_empty: bool = False) -> Path | None:
    """
    Скопировать рабочую БД в data/dumps/.
    Пустую БД в latest не пишем (чтобы не затереть хороший бэкап) —
    если только force_empty=True.
    """
    source = db_path()
    if not source.exists():
        raise FileNotFoundError(f"Database file not found: {source}")

    empty = is_working_db_empty()
    if empty and not force_empty:
        logger.warning(
            "Skip DB dump: working database is empty (would overwrite good backups)"
        )
        return None

    d = dumps_dir()
    stamp = timezone.localtime().strftime("%Y%m%d_%H%M%S")
    target = d / f"voitos_{stamp}.sqlite3"
    shutil.copy2(source, target)

    if not empty:
        shutil.copy2(source, latest_dump_path())
        logger.info("Database dump created: %s (+ latest.sqlite3)", target)
    else:
        logger.info("Empty database dump created (forced): %s", target)

    _cleanup_old_dumps(d, keep=30)
    return target


def restore_db_from_dump(dump: Path, *, label: str = "") -> Path:
    """
    Восстановить рабочую БД из дампа.
    Текущий файл (даже пустой) сохраняется как voitos_before_restore_*.sqlite3.
    """
    dump = Path(dump)
    if not dump.exists():
        raise FileNotFoundError(f"Dump not found: {dump}")
    if not dump_has_data(dump):
        raise ValueError(f"Dump has no bot users, refuse restore: {dump.name}")

    target = db_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    d = dumps_dir()
    stamp = timezone.localtime().strftime("%Y%m%d_%H%M%S")
    if target.exists() and target.stat().st_size > 0:
        safety = d / f"voitos_before_restore_{stamp}.sqlite3"
        shutil.copy2(target, safety)
        logger.info("Safety copy of current DB: %s", safety)

    shutil.copy2(dump, target)
    # Обновить latest только если восстановили с данными
    shutil.copy2(dump, latest_dump_path())
    logger.info(
        "Database restored from %s%s → %s",
        dump.name,
        f" ({label})" if label else "",
        target,
    )
    return target


def maybe_restore_if_empty() -> Path | None:
    """
    Если рабочая БД пустая, а есть непустой дамп — восстановить автоматически.
    Возвращает путь дампа или None.
    """
    if not is_working_db_empty():
        return None
    best = find_best_nonempty_dump()
    if not best:
        logger.info(
            "Working DB is empty and no non-empty dump found in %s — fresh start",
            dumps_dir(),
        )
        return None
    users = sqlite_botuser_count(best)
    logger.warning(
        "Working DB is empty — restoring from dump %s (%s bot users)",
        best.name,
        users,
    )
    restore_db_from_dump(best, label="auto on empty startup")
    return best


def _cleanup_old_dumps(dumps_dir: Path, keep: int = 30) -> None:
    files = sorted(
        [p for p in dumps_dir.glob("voitos_*.sqlite3") if "before_restore" not in p.name],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    # before_restore_* тоже чистим, но мягче — оставляем 10
    safety = sorted(
        dumps_dir.glob("voitos_before_restore_*.sqlite3"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for old in files[keep:]:
        try:
            old.unlink()
        except OSError:
            logger.warning("Could not delete old dump %s", old)
    for old in safety[10:]:
        try:
            old.unlink()
        except OSError:
            logger.warning("Could not delete old safety dump %s", old)


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
                path = create_db_dump()
                if path:
                    last_day = today
                else:
                    # Пустая БД — не помечаем день, попробуем позже когда появятся данные
                    pass
            except Exception:
                logger.exception("Daily dump failed")
        time.sleep(60)


if __name__ == "__main__":
    import django

    django.setup()
    print(create_db_dump())
