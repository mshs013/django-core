import datetime as dt

from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from core.archive import service
from core.models import ArchiveJob

from tests.dummyapp.models import OldRecord

from .base import make_user


@override_settings(CORE_ARCHIVE_MIN_AGE_DAYS=1)
class ArchiveTests(TransactionTestCase):
    def setUp(self):
        import tempfile

        self.tmp = tempfile.mkdtemp()
        self._ctx = override_settings(CORE_ARCHIVE_DIR=self.tmp)
        self._ctx.enable()
        now = timezone.now()
        for i in range(25):
            OldRecord.objects.create(created_at=now - dt.timedelta(days=400), payload=f"old{i}")
        for i in range(5):
            OldRecord.objects.create(created_at=now, payload=f"new{i}")

    def tearDown(self):
        self._ctx.disable()

    def test_preview_changes_nothing(self):
        p = service.preview("dummyapp_oldrecord", 365)
        self.assertTrue(p.ok, p.reason)
        self.assertEqual((p.matched, p.blocked), (25, 0))
        self.assertEqual(OldRecord.objects.count(), 30)

    def test_creating_a_job_requires_explicit_confirmation(self):
        with self.assertRaises(service.ArchiveError):
            service.create_archive_job(None, "dummyapp_oldrecord", 365)

    def test_archive_moves_rows_verified_and_restore_brings_them_back(self):
        job = service.create_archive_job(None, "dummyapp_oldrecord", 365, batch_size=10, confirm=True)
        job = service.run_archive(job.pk)
        self.assertEqual(job.status, "done", job.error)
        self.assertEqual(OldRecord.objects.count(), 5)                     # moved, not copied
        self.assertEqual(job.processed_rows, 25)
        self.assertEqual(job.batches.count(), 3)
        restore = service.run_restore(service.create_restore_job(None, job).pk)
        self.assertEqual(restore.status, "done", restore.error)
        self.assertEqual(OldRecord.objects.count(), 30)

    def test_disallowed_and_internal_tables_are_refused(self):
        for table in ("auth_user", "core_user", "django_session", "core_auditlog", "sqlite_master", "nope"):
            p = service.preview(table, 365)
            self.assertFalse(p.ok, table)
            with self.assertRaises(service.ArchiveError):
                service.create_archive_job(None, table, 365, confirm=True)

    def test_too_young_cutoff_is_refused(self):
        with override_settings(CORE_ARCHIVE_MIN_AGE_DAYS=30):
            self.assertFalse(service.preview("dummyapp_oldrecord", 5).ok)

    def test_tampered_batch_aborts_before_any_delete(self):
        from core.archive import codec

        job = service.create_archive_job(None, "dummyapp_oldrecord", 365, batch_size=10, confirm=True)
        real = codec.write_batch

        def corrupting(path, *args, **kwargs):
            info = real(path, *args, **kwargs)
            with open(path, "ab") as handle:               # damage the file after its checksum was taken
                handle.write(b"corrupt")
            return info

        codec.write_batch = corrupting
        try:
            job = service.run_archive(job.pk)
        finally:
            codec.write_batch = real
        self.assertNotEqual(job.status, "done")
        self.assertEqual(OldRecord.objects.count(), 30)                    # nothing was deleted
