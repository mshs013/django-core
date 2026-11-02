"""Pure-Python backup tests (no Django, no database, no native tools besides the Python interpreter).
Run with:  python -m unittest tests.test_backup_pure"""
import datetime as dt
import sys
import tempfile
from pathlib import Path
from unittest import TestCase

from core.backup import commands, recent
from core.backup.modes import DATA_ONLY, FULL, LAST_24_HOURS, SCHEMA_ONLY
from core.backup.retention import RetentionRow, select_expired
from core.backup.runner import ToolFailed, run_tool
from core.backup.adapters.base import ConnectionSettings

CONN = ConnectionSettings("postgresql", "db.local", "5432", "app", "erp", "p@ss word\"x")


class CommandTests(TestCase):
    def test_password_never_in_argv(self):
        for mode in (FULL, SCHEMA_ONLY, DATA_ONLY):
            argv = commands.pg_dump_argv("/usr/bin/pg_dump", CONN, mode, "/tmp/x.dump", 6)
            self.assertIsInstance(argv, list)
            self.assertFalse(any("p@ss" in a for a in argv))
        self.assertEqual(commands.pg_env(CONN)["PGPASSWORD"], CONN.password)

    def test_mysql_password_only_in_option_file(self):
        conn = ConnectionSettings("mysql", "db.local", "3306", "app", "erp", 'p@ss word"x')
        for mode in (FULL, SCHEMA_ONLY, DATA_ONLY):
            argv = commands.mysql_dump_argv("/usr/bin/mysqldump", "/tmp/c.cnf", conn, mode)
            self.assertEqual(argv[1], "--defaults-extra-file=/tmp/c.cnf")              # must be the first option
            self.assertFalse(any("p@ss" in a for a in argv))
            self.assertEqual(argv[-1], "erp")
        self.assertFalse(any("p@ss" in a for a in commands.mysql_restore_argv("mysql", "/tmp/c.cnf", conn)))
        self.assertIn('password="p@ss word\\"x"', commands.mysql_options(conn))           # quotes escaped
        self.assertIn("--no-data", commands.mysql_dump_argv("t", "c", conn, SCHEMA_ONLY))
        self.assertIn("--no-create-info", commands.mysql_dump_argv("t", "c", conn, DATA_ONLY))
        with self.assertRaises(commands.CommandError):
            commands.mysql_dump_argv("t", "c", conn, LAST_24_HOURS)
        self.assertEqual(commands.db_extension("mysql", FULL), ".sql.gz")

    def test_modes_map_to_flags(self):
        self.assertIn("--schema-only", commands.pg_dump_argv("t", CONN, SCHEMA_ONLY, "d", 6))
        self.assertIn("--data-only", commands.pg_dump_argv("t", CONN, DATA_ONLY, "d", 6))
        with self.assertRaises(commands.CommandError):
            commands.pg_dump_argv("t", CONN, LAST_24_HOURS, "d", 6)  # not a native mode


class ChildEnvTests(TestCase):
    def test_passes_only_system_variables(self):
        from unittest import mock

        from core.backup.runner import child_env

        with mock.patch.dict("os.environ", {"PATH": "/bin", "SYSTEMROOT": "C:\Windows", "DJANGO_SECRET_KEY": "s", "DB_PASSWORD": "p"},
                             clear=True):
            env = child_env({"PGPASSWORD": "x"})
        self.assertEqual(env, {"PATH": "/bin", "SYSTEMROOT": "C:\Windows", "PGPASSWORD": "x"})


class RetentionTests(TestCase):
    def test_keeps_newest_and_only_expires_old_success(self):
        now = dt.datetime(2026, 10, 1, tzinfo=dt.timezone.utc)
        rows = [RetentionRow(i, "database", "FULL", "SUCCESS", now - dt.timedelta(days=d)) for i, d in enumerate([1, 40, 50, 60], start=1)]
        rows.append(RetentionRow(9, "database", "FULL", "FAILED", now - dt.timedelta(days=90)))
        self.assertEqual(select_expired(rows, now, keep_days=30, min_keep=2), [3, 4])
        self.assertEqual(select_expired(rows, now, keep_days=0, min_keep=0), [])


class RunnerTests(TestCase):
    def test_runs_without_shell_and_redacts_failure(self):
        out = run_tool([sys.executable, "-c", "print('hi')"], timeout=20)
        self.assertEqual(out.strip(), "hi")
        with self.assertRaises(ToolFailed) as cm:
            run_tool([sys.executable, "-c", "import sys; sys.stderr.write('bad s3cret'); sys.exit(3)"], secrets=["s3cret"], timeout=20)
        self.assertNotIn("s3cret", str(cm.exception))

    def test_timeout_kills_child(self):
        with self.assertRaises(ToolFailed) as cm:
            run_tool([sys.executable, "-c", "import time; time.sleep(30)"], timeout=2)
        self.assertIn("timed out", str(cm.exception))


class Last24HoursBundleTests(TestCase):
    def test_bundle_has_manifest_counts_and_skips(self):
        now = dt.datetime(2026, 10, 1, 12, tzinfo=dt.timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "b.jsonl.tar.gz"
            rows = iter([(1, now), (2, now)])
            manifest = recent.write_bundle(dest, engine="postgresql", since=now - dt.timedelta(hours=24), until=now,
                                           exports=[("orders", "created_at", ["id", "created_at"], rows)],
                                           skipped={"lookup": "no creation timestamp column"})
            self.assertTrue(manifest["partial"])
            self.assertEqual(manifest["tables"]["orders"]["rows"], 2)
            self.assertIn("lookup", manifest["skipped"])
            self.assertIn("PARTIAL", manifest["notice"])
            self.assertTrue(recent.validate_bundle(dest).startswith("ok"))
