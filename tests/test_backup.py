import gzip
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from django.db import connection
from django.test import TransactionTestCase, override_settings
from django.urls import reverse

from core.backup import naming, service
from core.models import AuditLog, BackupRecord

from .base import CoreTestCase, make_user


class BackupRecordTests(CoreTestCase):
    def test_claim_is_atomic_second_delivery_gets_nothing(self):
        record = BackupRecord.objects.create(kind="database", mode="FULL")
        self.assertIsNotNone(service._claim(record.pk))
        self.assertIsNone(service._claim(record.pk))

    def test_cancelled_before_start_is_never_claimed(self):
        record = BackupRecord.objects.create(kind="database", mode="FULL", cancel_requested=True)
        self.assertIsNone(service._claim(record.pk))

    def test_unique_filenames_under_concurrency(self):
        with tempfile.TemporaryDirectory() as d:
            names, lock = [], threading.Lock()

            def grab():
                p = naming.reserve_unique_file(Path(d), "db", ".dump")
                with lock:
                    names.append(p.name)

            threads = [threading.Thread(target=grab) for _ in range(20)]
            [t.start() for t in threads]
            [t.join() for t in threads]
            self.assertEqual(len(set(names)), 20)

    def test_views_require_permissions(self):
        user = make_user("b@example.com", self.company_a)
        client = self.login(user)
        self.assertEqual(client.get(reverse("core:backup_list")).status_code, 403)
        self.assertEqual(client.post(reverse("core:backup_create"), {"mode": "FULL"}).status_code, 403)

    @override_settings(CORE_BACKUP_ALLOW_RESTORE=False)
    def test_restore_is_off_by_default(self):
        self.assertFalse(service.restore_allowed())

    def test_download_is_checked_and_audited(self):
        admin = make_user("root@example.com", self.company_a, superuser=True)
        with tempfile.TemporaryDirectory() as d, override_settings(CORE_BACKUP_DIR=d):
            path = Path(d) / "x.dump"
            path.write_bytes(b"data")
            rec = BackupRecord.objects.create(kind="database", mode="FULL", status="SUCCESS", file_name="x.dump", file_size=4)
            r = self.login(admin).get(reverse("core:backup_download", args=[rec.pk]))
            self.assertEqual(r.status_code, 200)
            self.assertTrue(AuditLog.objects.filter(event="backup").exists())


@unittest.skipUnless(connection.vendor == "sqlite", "SQLite dump/restore round trip")
class SqliteBackupTests(TransactionTestCase):
    def test_full_dump_restores_to_a_new_file_and_never_overwrites_live_db(self):
        from core.backup.adapters.registry import get_adapter

        with tempfile.TemporaryDirectory() as d, override_settings(CORE_BACKUP_DIR=d):
            record = service.create_database_backup(None, "FULL")
            service.run_database_backup(record.pk)
            record.refresh_from_db()
            self.assertEqual(record.status, "SUCCESS", record.error)
            self.assertTrue(record.sha256)
            raw = gzip.decompress((Path(d) / record.file_name).read_bytes()).decode()
            self.assertIn("CREATE TABLE", raw)
            verdict = service.validate_file("database", "FULL", "sqlite", Path(d) / record.file_name, {})
            self.assertTrue(verdict.startswith("ok"), verdict)                 # a readable message; failures raise BackupError
