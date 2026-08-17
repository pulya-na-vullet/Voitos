"""Tests for DB dump / auto-restore when working DB is empty."""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from database.dump import (
    create_db_dump,
    find_best_nonempty_dump,
    is_working_db_empty,
    maybe_restore_if_empty,
    restore_db_from_dump,
    sqlite_botuser_count,
)
from database.models import BotUser, PanelProfile, PanelRole

User = get_user_model()


def _make_sqlite_with_users(path: Path, n: int = 2) -> None:
    """Minimal sqlite with database_botuser rows (enough for dump helpers)."""
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE database_botuser ("
        "id INTEGER PRIMARY KEY, max_user_id TEXT, real_name TEXT, phone TEXT)"
    )
    for i in range(n):
        conn.execute(
            "INSERT INTO database_botuser(max_user_id, real_name, phone) VALUES (?,?,?)",
            (f"u{i}", f"User {i}", f"8900{i}"),
        )
    conn.commit()
    conn.close()


def _make_empty_schema(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE database_botuser ("
        "id INTEGER PRIMARY KEY, max_user_id TEXT, real_name TEXT, phone TEXT)"
    )
    conn.commit()
    conn.close()


class DumpHelpersFileTests(SimpleTestCase):
    """File-level tests without Django DB (works with in-memory test runner)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="voitos_dump_"))
        self.dumps = self.tmp / "dumps"
        self.dumps.mkdir()
        self.db = self.tmp / "voitos.sqlite3"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_sqlite_botuser_count(self):
        _make_sqlite_with_users(self.db, 3)
        self.assertEqual(sqlite_botuser_count(self.db), 3)
        empty = self.tmp / "empty.sqlite3"
        _make_empty_schema(empty)
        self.assertEqual(sqlite_botuser_count(empty), 0)

    def test_skip_empty_dump(self):
        _make_empty_schema(self.db)
        latest = self.dumps / "latest.sqlite3"
        _make_sqlite_with_users(latest, 5)
        before = latest.read_bytes()
        with patch("database.dump.db_path", return_value=self.db), patch(
            "database.dump.dumps_dir", return_value=self.dumps
        ), patch("database.dump.latest_dump_path", return_value=latest), patch(
            "database.dump.is_working_db_empty", return_value=True
        ):
            result = create_db_dump()
        self.assertIsNone(result)
        self.assertEqual(latest.read_bytes(), before)

    def test_create_dump_updates_latest(self):
        _make_sqlite_with_users(self.db, 2)
        latest = self.dumps / "latest.sqlite3"
        with patch("database.dump.db_path", return_value=self.db), patch(
            "database.dump.dumps_dir", return_value=self.dumps
        ), patch("database.dump.latest_dump_path", return_value=latest), patch(
            "database.dump.is_working_db_empty", return_value=False
        ):
            path = create_db_dump()
        self.assertIsNotNone(path)
        self.assertTrue(path.exists())
        self.assertEqual(sqlite_botuser_count(latest), 2)

    def test_find_best_and_restore(self):
        _make_empty_schema(self.db)
        good = self.dumps / "voitos_20990101_120000.sqlite3"
        _make_sqlite_with_users(good, 4)
        latest = self.dumps / "latest.sqlite3"
        latest.write_bytes(b"SQLite format 3\x00" + b"\x00" * 200)
        with patch("database.dump.dumps_dir", return_value=self.dumps), patch(
            "database.dump.latest_dump_path", return_value=latest
        ):
            best = find_best_nonempty_dump()
        self.assertEqual(best, good)

        with patch("database.dump.db_path", return_value=self.db), patch(
            "database.dump.dumps_dir", return_value=self.dumps
        ), patch("database.dump.latest_dump_path", return_value=latest):
            restore_db_from_dump(good)
        self.assertEqual(sqlite_botuser_count(self.db), 4)

    def test_maybe_restore_if_empty(self):
        _make_empty_schema(self.db)
        good = self.dumps / "voitos_20990101_120000.sqlite3"
        _make_sqlite_with_users(good, 2)
        latest = self.dumps / "latest.sqlite3"
        with patch("database.dump.db_path", return_value=self.db), patch(
            "database.dump.dumps_dir", return_value=self.dumps
        ), patch("database.dump.latest_dump_path", return_value=latest), patch(
            "database.dump.is_working_db_empty", return_value=True
        ):
            restored = maybe_restore_if_empty()
        self.assertIsNotNone(restored)
        self.assertEqual(sqlite_botuser_count(self.db), 2)


class DumpPanelTests(TestCase):
    def test_panel_restore_endpoint(self):
        admin = User.objects.create_superuser("dumpadm", "d@t.com", "pass")
        PanelProfile.objects.create(user=admin, role=PanelRole.ADMIN)
        BotUser.objects.create(max_user_id="pr1", real_name="A", chat_id="1")

        tmp = Path(tempfile.mkdtemp(prefix="voitos_panel_dump_"))
        try:
            dump = tmp / "voitos_panel_restore.sqlite3"
            _make_sqlite_with_users(dump, 3)
            with patch("database.dump.list_dump_files", return_value=[dump]), patch(
                "database.dump.find_best_nonempty_dump", return_value=dump
            ), patch(
                "database.dump.restore_db_from_dump", return_value=dump
            ) as restore_mock, patch(
                "database.dump.sqlite_botuser_count", return_value=3
            ):
                c = Client()
                self.assertTrue(c.login(username="dumpadm", password="pass"))
                resp = c.post(reverse("panel:restore_dump"), {"dump_name": dump.name})
                self.assertEqual(resp.status_code, 302)
                restore_mock.assert_called_once()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_is_working_db_empty_orm(self):
        BotUser.objects.all().delete()
        self.assertTrue(is_working_db_empty())
        BotUser.objects.create(max_user_id="x", real_name="Y", chat_id="z")
        self.assertFalse(is_working_db_empty())
