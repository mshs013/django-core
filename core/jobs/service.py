"""Start, run, retry and clean up registered jobs. Queueing goes through ``core.tasks.queue`` (django-q2; ``Q_SYNC=1`` / tests run
inline), and the starter is notified when a run ends (``core.tasks.jobs.run_job``)."""
from __future__ import annotations

import datetime as dt
import logging

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from core import conf
from core.audit.redaction import redact_mapping
from core.backup.adapters.redact import redact
from core.backup.retention import RetentionRow, select_expired
from core.models import JobRecord

from .registry import JobError, get_job_type

log = logging.getLogger("core.jobs")


def submit_job(job_type: str, params: dict | None = None, *, user=None, label: str = "", company=None) -> JobRecord:
    """Create a queued run of a registered job type and queue it once the surrounding transaction commits. ``params`` must be
    JSON-safe; keys that look like secrets are masked before saving."""
    definition = get_job_type(job_type)                                      # UnknownJobType for anything not registered
    job = JobRecord.objects.create(job_type=job_type, label=(label or definition.label)[:200], params=redact_mapping(params or {}),
                                   company=company, created_by=user if getattr(user, "pk", None) else None)
    _enqueue(job, definition.timeout)
    return job


def _enqueue(job: JobRecord, timeout: int) -> None:
    from core.tasks.queue import enqueue

    def send():
        task_id = enqueue("core.tasks.jobs.run_job", job.pk, task_name=f"job:{job.job_type}:{job.pk}", timeout=timeout)
        JobRecord.objects.filter(pk=job.pk).update(task_id=str(task_id or "")[:64])

    transaction.on_commit(send)


def execute_job(job_id: int) -> str:
    """Run one queued job (called by the worker). A job that is no longer QUEUED is skipped, so a duplicate delivery never runs it
    twice. Handler exceptions mark the run FAILED with a redacted message; they never reach the worker."""
    claimed = JobRecord.objects.filter(pk=job_id, status=JobRecord.QUEUED).update(
        status=JobRecord.RUNNING, started_at=timezone.now(), finished_at=None, attempts=F("attempts") + 1, error="")
    job = JobRecord.objects.filter(pk=job_id).first()
    if job is None:
        return "missing"
    if not claimed:
        return f"skipped:{job.status}"
    try:
        result = get_job_type(job.job_type).handler(job)
    except Exception as exc:  # noqa: BLE001 - recorded on the row
        text = str(exc) if isinstance(exc, JobError) else f"{type(exc).__name__}: {exc}"
        if not isinstance(exc, JobError):
            log.error("job %s (%s) failed: %s", job.pk, job.job_type, redact(text, limit=500))
        JobRecord.objects.filter(pk=job.pk).update(status=JobRecord.FAILED, error=redact(text), finished_at=timezone.now())
        return JobRecord.FAILED
    payload = result if isinstance(result, dict) else {} if result is None else {"value": result}
    JobRecord.objects.filter(pk=job.pk).update(status=JobRecord.DONE, progress=100, result=redact_mapping(payload),
                                               finished_at=timezone.now())
    return JobRecord.DONE


def can_retry(job: JobRecord) -> bool:
    try:
        return job.status == JobRecord.FAILED and get_job_type(job.job_type).allow_retry
    except JobError:
        return False


def retry_job(job: JobRecord) -> JobRecord:
    """Queue a FAILED run again (same row; ``attempts`` counts the runs)."""
    if not can_retry(job):
        raise JobError("Only a failed job whose type allows retries can be started again.")
    JobRecord.objects.filter(pk=job.pk, status=JobRecord.FAILED).update(status=JobRecord.QUEUED, error="", progress=0,
                                                                       progress_message="", finished_at=None)
    job.refresh_from_db()
    _enqueue(job, get_job_type(job.job_type).timeout)
    return job


def delete_file(job: JobRecord) -> None:
    """Remove the result file but keep the history row."""
    from core.files.storage import private_storage

    if job.file_name and private_storage.exists(job.file_name):
        private_storage.delete(job.file_name)
    JobRecord.objects.filter(pk=job.pk).update(file_deleted=True, file_deleted_at=timezone.now())


def apply_retention(now: dt.datetime | None = None, dry_run: bool = False) -> dict:
    """Delete result FILES older than CORE_JOBS_KEEP_DAYS, always keeping the newest CORE_JOBS_MIN_KEEP per job type. Rows stay."""
    rows = [RetentionRow(j.pk, j.job_type, "", "SUCCESS", j.created_at)
            for j in JobRecord.objects.filter(status=JobRecord.DONE, file_deleted=False).exclude(file_name="")
            .only("pk", "job_type", "created_at")]
    ids = select_expired(rows, now or timezone.now(), int(conf.get("CORE_JOBS_KEEP_DAYS")), int(conf.get("CORE_JOBS_MIN_KEEP")))
    if not dry_run:
        for job in JobRecord.objects.filter(pk__in=ids):
            delete_file(job)
    return {"candidates": ids, "deleted": 0 if dry_run else len(ids), "dry_run": dry_run}
