"""django-q2 task entry points (plain functions taking ids, so the web process, workers and commands share one code path)."""
from __future__ import annotations

from contextlib import contextmanager

_OK = ("SUCCESS", "done")                       # BackupRecord / restore_status use SUCCESS; export + archive jobs use "done"


@contextmanager
def _tell_owner(model_path: str, pk: int, label: str, url_name: str, *, user_id: int | None = None, field: str = "status",
                owner: str = "created_by", with_pk: bool = False):
    """Run a job, then notify whoever started it (``user_id``, else the row's ``owner`` field) that it finished or failed. Runs in
    ``finally`` so a crashing job still reports (``with_pk``: the link is ``url_name`` of the row itself); a notification problem never hides the job's own error."""
    try:
        yield
    finally:
        try:
            from django.apps import apps
            from django.contrib.auth import get_user_model
            from django.urls import reverse

            from core.notifications.service import notify

            row = apps.get_model(model_path).objects.filter(pk=pk).first()
            uid = user_id or getattr(row, f"{owner}_id", None)
            user = get_user_model().objects.filter(pk=uid).first() if uid else None
            if row is not None and user is not None:
                ok = getattr(row, field, "") in _OK
                notify([user], f"{label} {'completed' if ok else 'failed'}: {row}", url=reverse(f"core:{url_name}", args=[pk] if with_pk else None),
                       level="success" if ok else "danger", kind="job")
        except Exception:                       # pragma: no cover - never mask the job result
            import logging

            logging.getLogger("core.tasks").exception("job notification failed")


def run_export(job_id: int) -> None:
    from core.reports.resources.export import run_job

    with _tell_owner("core.ExportJob", job_id, "Export", "export_jobs", owner="owner"):
        run_job(job_id)


def run_database_backup(record_id: int) -> None:
    from core.backup.service import run_database_backup as run

    with _tell_owner("core.BackupRecord", record_id, "Database backup", "backup_list"):
        run(record_id)


def run_file_backup(record_id: int) -> None:
    from core.backup.service import run_file_backup as run

    with _tell_owner("core.BackupRecord", record_id, "File backup", "backup_list"):
        run(record_id)


def run_backup_restore(record_id: int, actor_id: int | None = None) -> None:
    from core.backup.service import run_restore as run

    with _tell_owner("core.BackupRecord", record_id, "Backup restore", "backup_list", user_id=actor_id, field="restore_status"):
        run(record_id, actor_id)


def run_archive(job_id: int) -> None:
    from core.archive.service import run_archive as run

    with _tell_owner("core.ArchiveJob", job_id, "Archive", "archive_list"):
        run(job_id)


def run_archive_restore(job_id: int) -> None:
    from core.archive.service import run_restore as run

    with _tell_owner("core.ArchiveJob", job_id, "Archive restore", "archive_list"):
        run(job_id)


def run_job(job_id: int) -> str:
    """A run of a job registered with ``core.jobs.register_job``."""
    from core.jobs.service import execute_job

    with _tell_owner("core.JobRecord", job_id, "Job", "job_detail", with_pk=True):
        return execute_job(job_id)


def housekeeping() -> dict:
    """Scheduled daily: expire exports, apply backup and job-file retention (files only; history rows stay)."""
    from core.backup.service import apply_retention
    from core.jobs.service import apply_retention as job_retention
    from core.reports.resources.export import expire_jobs

    return {"exports_expired": expire_jobs(), "retention": apply_retention(), "job_files": job_retention()}
