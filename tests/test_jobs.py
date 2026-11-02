"""Background jobs (``core.jobs``): only registered handlers run, every run is a JobRecord with redacted params / errors, progress,
an optional result file, retry, retention (files only), the Background jobs pages and who may see what."""
import datetime as dt
import io

from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from core.files.storage import private_storage
from core.jobs import JobError, UnknownJobType, register_job, registered_jobs, retry_job, submit_job, unregister_job
from core.jobs.service import apply_retention, execute_job
from core.models import JobRecord, Notification
from tests.dummyapp.models import Garment

from .base import CoreTestCase, make_user


class JobRunTests(CoreTestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user("jobs@example.com", self.company_a)
        Garment.objects.create(company=self.company_a, name="Polo", style_no="S1", price=10)
        Garment.objects.create(company=self.company_b, name="Tee", style_no="S2", price=5)

    def submit(self, params=None, **kwargs):
        with self.captureOnCommitCallbacks(execute=True):          # the job is queued on commit; tests run django-q inline
            job = submit_job("dummyapp.garment_price_list", params, user=self.user, company=self.company_a, **kwargs)
        job.refresh_from_db()
        return job

    def test_registered_job_runs_and_keeps_its_history(self):
        job = self.submit({"note": "monthly"})
        self.assertEqual((job.status, job.progress, job.attempts), (JobRecord.DONE, 100, 1))
        self.assertEqual(job.result, {"garments": 1})                 # only the active company's garments
        self.assertEqual(job.label, "Garment price list")
        self.assertTrue(job.task_id)
        self.assertIsNotNone(job.started_at)
        self.assertIsNotNone(job.finished_at)
        self.assertTrue(job.has_file)
        with private_storage.open(job.file_name) as handle:
            self.assertEqual(handle.read().decode(), "style_no,name,price\nS1,Polo,10.00")
        notice = Notification.objects.get(recipient=self.user, kind="job", title__contains="completed")
        self.assertEqual(notice.url, reverse("core:job_detail", args=[job.pk]))              # the bell opens this run

    def test_secrets_are_masked_in_params_and_errors(self):
        job = self.submit({"fail": True, "password": "s3cret", "api_key": "k"})
        self.assertEqual(job.status, JobRecord.FAILED)
        self.assertNotIn("s3cret", str(job.params))
        self.assertNotIn("hunter2", job.error)
        self.assertIn("ValueError: asked to fail", job.error)
        self.assertFalse(job.has_file)
        self.assertTrue(Notification.objects.filter(recipient=self.user, kind="job", title__contains="failed").exists())

    def test_only_registered_types_run(self):
        with self.assertRaises(UnknownJobType):
            submit_job("os.system", {"cmd": "x"}, user=self.user)
        stale = JobRecord.objects.create(job_type="gone.away", created_by=self.user)    # e.g. its app was removed since
        self.assertEqual(execute_job(stale.pk), JobRecord.FAILED)
        stale.refresh_from_db()
        self.assertIn("No handler is registered", stale.error)

    def test_a_run_is_never_executed_twice(self):
        job = self.submit()
        self.assertEqual(execute_job(job.pk), "skipped:done")
        job.refresh_from_db()
        self.assertEqual(job.attempts, 1)

    def test_retry_only_failed_runs_of_retryable_types(self):
        job = self.submit({"fail": True})
        with self.captureOnCommitCallbacks(execute=True):
            retry_job(job)
        job.refresh_from_db()
        self.assertEqual((job.status, job.attempts), (JobRecord.FAILED, 2))          # fails again: same params
        done = self.submit()
        with self.assertRaises(JobError):
            retry_job(done)
        register_job("tests.once", lambda job: (_ for _ in ()).throw(RuntimeError("x")), allow_retry=False)
        self.addCleanup(unregister_job, "tests.once")
        with self.captureOnCommitCallbacks(execute=True):
            once = submit_job("tests.once", user=self.user)
        once.refresh_from_db()
        with self.assertRaises(JobError):
            retry_job(once)

    def test_register_job_refuses_a_second_handler_for_a_name(self):
        register_job("tests.dup", lambda job: None)
        self.addCleanup(unregister_job, "tests.dup")
        with self.assertRaises(ValueError):
            register_job("tests.dup", lambda job: None)
        self.assertIn("tests.dup", [t.name for t in registered_jobs()])

    def test_retention_removes_old_files_and_keeps_rows(self):
        jobs = [self.submit() for _ in range(4)]
        old = timezone.now() - dt.timedelta(days=40)
        JobRecord.objects.filter(pk__in=[j.pk for j in jobs]).update(created_at=old)
        with self.settings(CORE_JOBS_KEEP_DAYS=30, CORE_JOBS_MIN_KEEP=3):
            self.assertEqual(apply_retention(dry_run=True)["candidates"], [jobs[0].pk])
            self.assertEqual(apply_retention()["deleted"], 1)
        first = JobRecord.objects.get(pk=jobs[0].pk)
        self.assertTrue(first.file_deleted)
        self.assertFalse(private_storage.exists(first.file_name))
        self.assertEqual(JobRecord.objects.count(), 4)                               # history stays

    def test_commands(self):
        self.submit()
        out = io.StringIO()
        call_command("jobs_info", stdout=out)
        self.assertIn("dummyapp.garment_price_list  Garment price list", out.getvalue())
        self.assertIn("done 1", out.getvalue())
        out = io.StringIO()
        call_command("jobs_retention", "--dry-run", stdout=out)
        self.assertIn("Would delete 0 file(s).", out.getvalue())


class JobPageTests(CoreTestCase):
    def setUp(self):
        super().setUp()
        self.owner = make_user("owner@example.com", self.company_a)
        self.other = make_user("other@example.com", self.company_a)
        self.staff = make_user("staff@example.com", self.company_a)
        self.staff.is_staff = True
        self.staff.save()
        with self.captureOnCommitCallbacks(execute=True):
            self.done = submit_job("dummyapp.garment_price_list", user=self.owner, company=self.company_a)
            self.failed = submit_job("dummyapp.garment_price_list", {"fail": True}, user=self.owner, company=self.company_a)

    def test_owner_sees_downloads_and_retries_own_jobs(self):
        client = self.login(self.owner)
        listing = client.get(reverse("core:job_list"))
        self.assertContains(listing, f"Garment price list #{self.done.pk}")
        self.assertNotContains(listing, "Started by")                                # not an "everyone" view
        download = client.get(reverse("core:job_download", args=[self.done.pk]))
        self.assertEqual(download.status_code, 200)
        self.assertIn("garment-prices.csv", download["Content-Disposition"])
        self.assertContains(client.get(reverse("core:job_detail", args=[self.failed.pk])), "Run again")
        with self.captureOnCommitCallbacks(execute=True):
            response = client.post(reverse("core:job_retry", args=[self.failed.pk]))
        self.assertRedirects(response, reverse("core:job_detail", args=[self.failed.pk]))
        self.failed.refresh_from_db()
        self.assertEqual(self.failed.attempts, 2)

    def test_other_users_cannot_see_someone_elses_jobs(self):
        client = self.login(self.other)
        self.assertNotContains(client.get(reverse("core:job_list")), "Garment price list #")
        for name in ("core:job_detail", "core:job_download"):
            self.assertEqual(client.get(reverse(name, args=[self.done.pk])).status_code, 404)
        self.assertEqual(client.post(reverse("core:job_retry", args=[self.failed.pk])).status_code, 404)

    def test_staff_see_everyones_jobs_but_retry_needs_the_permission(self):
        client = self.login(self.staff)
        listing = client.get(reverse("core:job_list"))
        self.assertContains(listing, f"Garment price list #{self.done.pk}")
        self.assertContains(listing, "Started by")
        self.assertNotContains(client.get(reverse("core:job_list"), {"mine": "1"}), "Garment price list #")
        self.assertContains(client.get(reverse("core:job_list"), {"status": "failed"}), f"#{self.failed.pk}")
        self.assertNotContains(client.get(reverse("core:job_detail", args=[self.failed.pk])), "Run again")
        self.assertEqual(client.post(reverse("core:job_retry", args=[self.failed.pk])).status_code, 403)

    def test_menu_entry_for_staff(self):
        self.assertContains(self.login(self.staff).get(reverse("core:dashboard")), reverse("core:job_list"))
        self.assertNotContains(self.login(self.other).get(reverse("core:dashboard")), reverse("core:job_list"))


class UpgradeMenuTests(CoreTestCase):
    """0002_jobrecord adds the menu entry to EXISTING installs only, once, and never re-creates what an administrator removed."""

    def run_migration_step(self):
        from importlib import import_module
        from types import SimpleNamespace

        from django.apps import apps
        from django.db import connection

        import_module("core.migrations.0002_jobrecord").add_menu_entry(apps, SimpleNamespace(connection=connection))

    def test_adds_the_entry_once_after_export_jobs(self):
        from core.models import MenuItem

        MenuItem.objects.filter(url_name="core:job_list").delete()              # an install from before this migration
        self.run_migration_step()
        self.run_migration_step()
        rows = MenuItem.objects.filter(url_name="core:job_list")
        self.assertEqual(rows.count(), 1)
        export = MenuItem.objects.get(url_name="core:export_jobs")
        self.assertEqual((rows[0].parent_id, rows[0].permission), (export.parent_id, "core.view_jobrecord"))
        self.assertGreater(rows[0].position, export.position)

    def test_leaves_a_menu_without_administration_alone(self):
        from core.models import MenuItem

        MenuItem.objects.filter(url_name="core:job_list").delete()
        MenuItem.objects.filter(title="Administration", parent=None).update(title="Admin")   # renamed by the administrator
        self.run_migration_step()
        self.assertFalse(MenuItem.objects.filter(url_name="core:job_list").exists())
