"""Archive engine: preview, batched move (write file -> verify -> delete in one transaction), and restore.

Safety rules, in the order they protect data:
  1. Only tables listed in ``CORE_ARCHIVE_ALLOWED_TABLES`` (and not on the built-in deny list) can ever be touched.
  2. The table needs a single-column primary key and an unambiguous creation-timestamp column (``backup.adapters`` rules).
  3. Rows still referenced by another table are never moved (``NOT EXISTS`` in the SELECT *and* again in the DELETE).
  4. Each batch is written to a gzip file, read back and checked (checksum, row count, key digest) BEFORE any row is deleted.
  5. Rows are deleted in a transaction; if the number deleted differs from the number archived the transaction rolls back.
  6. Restore verifies the file again, refuses to overwrite existing rows, and inserts inside a transaction.
  7. Creating a job that deletes data needs ``confirm=True`` (the UI/command ask the operator); ``preview`` never changes anything.
Archive files are the only copy of the moved rows: they are never removed automatically. This tooling is separate from the
full-database restore in ``core.backup``.
"""
from __future__ import annotations

import datetime as dt
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from django.db import transaction
from django.utils import timezone

from core import conf
from core.archive import codec, sql
from core.models import ArchiveBatch, ArchiveJob
from core.models.jobs import (A_CANCELLED as CANCELLED, A_DONE as DONE, A_FAILED as FAILED, A_QUEUED as QUEUED, A_RUNNING as RUNNING,
                              B_ABORTED, B_MOVED, B_RESTORE_FAILED, B_RESTORED, B_WRITTEN, KIND_ARCHIVE, KIND_RESTORE)

log = logging.getLogger(__name__)

DENY_PREFIXES = ("django_", "auth_", "guardian_", "core_", "sqlite_", "pg_")
MAX_BATCH = 10000


class ArchiveError(Exception):
    """A refusal or failure with a message that is safe to show to the user."""


@dataclass
class Reference:
    child_table: str
    child_column: str
    parent_column: str


@dataclass
class Preview:
    table: str
    ok: bool = False
    reason: str = ""
    column: str = ""
    pk: str = ""
    cutoff: dt.datetime | None = None
    matched: int = 0
    blocked: int = 0
    references: list[str] = field(default_factory=list)

    @property
    def movable(self) -> int:
        return max(0, self.matched - self.blocked)


# ---- configuration ---------------------------------------------------------------------------------------------------
def allowed_tables() -> list[str]:
    configured = [t for t in conf.get("CORE_ARCHIVE_ALLOWED_TABLES") if t]
    return sorted(t for t in configured if not t.startswith(DENY_PREFIXES))


def storage_dir() -> Path:
    path = conf.archive_dir()
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def min_age_days() -> int:
    return int(conf.get("CORE_ARCHIVE_MIN_AGE_DAYS"))


def _adapter():
    from core.backup.adapters.registry import get_adapter

    return get_adapter("default")


def _batch_path(job: ArchiveJob, number: int) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_]+", job.table):
        raise ArchiveError("Invalid table name.")
    return storage_dir() / f"job{job.pk}" / f"{job.table}-{number:06d}.jsonl.gz"


def _resolve_inside_storage(path: Path) -> Path:
    base = storage_dir().resolve()
    resolved = path.resolve()
    if base not in resolved.parents:
        raise ArchiveError("Archive file is outside the archive directory.")
    return resolved


# ---- table inspection --------------------------------------------------------------------------------------------------
def inspect(table: str):
    """(adapter, columns, pk, creation column, references) for an allowed table, or ArchiveError."""
    if table not in allowed_tables():
        raise ArchiveError("This table is not enabled for archiving (CORE_ARCHIVE_ALLOWED_TABLES).")
    adapter = _adapter()
    if table not in adapter.tables():
        raise ArchiveError("Table does not exist.")
    pk = adapter.primary_key_columns(table)
    if len(pk) != 1:
        raise ArchiveError("Only tables with a single-column primary key can be archived.")
    choice = adapter.creation_timestamp_choice(table)
    if not choice.column:
        raise ArchiveError(f"No usable creation timestamp: {choice.reason}.")
    refs = []
    for fk in adapter.foreign_keys():
        if fk.ref_table != table:
            continue
        if fk.table == table:
            raise ArchiveError("Self-referencing tables are not supported.")
        if len(fk.columns) != 1:
            raise ArchiveError(f"Composite foreign key from {fk.table} is not supported.")
        refs.append(Reference(fk.table, fk.columns[0], fk.ref_columns[0]))
    columns = [c.name for c in adapter.columns(table)]
    return adapter, columns, pk[0], choice.column, refs


def _cutoff_param(adapter, cutoff: dt.datetime):
    return adapter.window_param(cutoff)


def preview(table: str, older_than_days: int) -> Preview:
    """Dry run: counts only, nothing is written or deleted."""
    result = Preview(table=table)
    if older_than_days < min_age_days():
        result.reason = f"Rows must be at least {min_age_days()} days old (CORE_ARCHIVE_MIN_AGE_DAYS)."
        return result
    try:
        adapter, _cols, pk, column, refs = inspect(table)
    except ArchiveError as exc:
        result.reason = str(exc)
        return result
    cutoff = timezone.now() - dt.timedelta(days=older_than_days)
    param = _cutoff_param(adapter, cutoff)
    result.column, result.pk, result.cutoff = column, pk, cutoff
    result.matched = int(adapter.fetch(sql.count_old(adapter, table, column), [param])[0][0])
    movable = int(adapter.fetch(sql.count_movable(adapter, table, column, refs), [param])[0][0])
    result.blocked = result.matched - movable
    result.references = [f"{r.child_table}.{r.child_column}" for r in refs]
    result.ok = True
    return result


# ---- jobs ----------------------------------------------------------------------------------------------------------------
def create_archive_job(user, table: str, older_than_days: int, batch_size: int | None = None, *, confirm: bool = False) -> ArchiveJob:
    if not confirm:
        raise ArchiveError("Archiving moves rows out of the database; explicit confirmation is required (run a preview first).")
    prev = preview(table, older_than_days)
    if not prev.ok:
        raise ArchiveError(prev.reason)
    if prev.movable == 0:
        raise ArchiveError("Nothing to archive.")
    size = max(1, min(int(batch_size or conf.get("CORE_ARCHIVE_BATCH_SIZE")), MAX_BATCH))
    with transaction.atomic():
        if ArchiveJob.objects.select_for_update().filter(table=table, status__in=(QUEUED, RUNNING)).exists():
            raise ArchiveError("Another job for this table is already queued or running.")
        return ArchiveJob.objects.create(kind=KIND_ARCHIVE, table=table, column=prev.column, older_than_days=older_than_days,
                                         cutoff=prev.cutoff, batch_size=size, matched_rows=prev.matched, blocked_rows=prev.blocked,
                                         created_by=user)


def create_restore_job(user, source: ArchiveJob) -> ArchiveJob:
    if source.kind != KIND_ARCHIVE:
        raise ArchiveError("Only an archive job can be restored.")
    if source.status in (QUEUED, RUNNING):
        raise ArchiveError("The archive job is still active.")
    pending = source.batches.filter(status=B_MOVED)
    total = sum(b.rows for b in pending)
    if not total:
        raise ArchiveError("No moved batches are left to restore.")
    with transaction.atomic():
        if ArchiveJob.objects.select_for_update().filter(table=source.table, status__in=(QUEUED, RUNNING)).exists():
            raise ArchiveError("Another job for this table is already queued or running.")
        return ArchiveJob.objects.create(kind=KIND_RESTORE, table=source.table, column=source.column, parent=source,
                                         batch_size=source.batch_size, matched_rows=total, created_by=user)


def _claim(job_id: int, expected_kind: str) -> ArchiveJob | None:
    """Atomically move queued -> running; None when someone else already did (a duplicate task delivery runs nothing)."""
    taken = ArchiveJob.objects.filter(pk=job_id, status=QUEUED, kind=expected_kind).update(status=RUNNING, started_at=timezone.now())
    return ArchiveJob.objects.get(pk=job_id) if taken else None


def _finish(job: ArchiveJob, status: str, error: str = ""):
    job.status, job.error, job.finished_at = status, error[:4000], timezone.now()
    job.save(update_fields=["status", "error", "finished_at", "processed_rows", "blocked_rows"])


def request_cancel(job: ArchiveJob) -> None:
    if job.status == QUEUED:
        ArchiveJob.objects.filter(pk=job.pk, status=QUEUED).update(status=CANCELLED, finished_at=timezone.now())
    elif job.status == RUNNING:
        ArchiveJob.objects.filter(pk=job.pk).update(cancel_requested=True)  # honoured between batches


def _audit(job: ArchiveJob, action: str, remarks: str = ""):
    from core.audit.service import record_event

    try:
        record_event("archive", obj=job, user=job.created_by, message=f"{action}: {job.table} rows={job.processed_rows} {remarks}"[:480])
    except Exception:
        log.exception("could not audit %s", action)


def reconcile(job: ArchiveJob, adapter, pk: str) -> None:
    """After a crash a batch can be left 'written': if its rows are still in the table the delete never committed (drop the file);
    if none are left the delete did commit (mark moved). Anything in between cannot happen (one transaction) and aborts the job."""
    for batch in job.batches.filter(status=B_WRITTEN):
        path = _resolve_inside_storage(_batch_path(job, batch.number))
        header, rows = codec.read_batch(path) if path.is_file() else (None, iter(()))
        pks = [r[header["columns"].index(pk)] for r in rows] if header else []
        present = 0
        for i in range(0, len(pks), 500):
            chunk = pks[i:i + 500]
            present += len(adapter.fetch(sql.existing_keys(adapter, job.table, pk, len(chunk)), chunk))
        if pks and present == 0:
            batch.status = B_MOVED
        elif present == len(pks):
            batch.status = B_ABORTED
            path.unlink(missing_ok=True)
        else:
            raise ArchiveError(f"Batch {batch.number} is half-applied; manual check needed.")
        batch.save(update_fields=["status"])


def run_archive(job_id: int) -> ArchiveJob:
    job = _claim(job_id, KIND_ARCHIVE)
    if job is None:
        return ArchiveJob.objects.get(pk=job_id)
    try:
        adapter, columns, pk, column, refs = inspect(job.table)
        if column != job.column:
            raise ArchiveError("The table's creation column changed since the job was created.")
        reconcile(job, adapter, pk)
        param = _cutoff_param(adapter, job.cutoff)
        pk_index = columns.index(pk)
        number = job.batches.order_by("-number").values_list("number", flat=True).first() or 0
        last = None
        while True:
            job.refresh_from_db(fields=["cancel_requested"])
            if job.cancel_requested:
                _finish(job, CANCELLED, "Cancelled between batches; rows moved so far remain restorable.")
                _audit(job, "archive_cancelled")
                return job
            query = sql.select_batch(adapter, job.table, columns, column, pk, refs, last is not None, job.batch_size)
            rows = adapter.fetch(query, [param] if last is None else [param, last])
            if not rows:
                break
            number += 1
            last = rows[-1][pk_index]
            _move_batch(job, adapter, columns, pk, refs, rows, number)
        _finish(job, DONE)
        _audit(job, "archive_completed", f"{job.processed_rows} rows from {job.table}")
    except Exception as exc:  # noqa: BLE001 - persisted so the UI shows it
        log.exception("archive job %s failed", job_id)
        _finish(job, FAILED, f"{type(exc).__name__}: {exc}")
        _audit(job, "archive_failed", str(exc))
    return job


def _move_batch(job, adapter, columns, pk, refs, rows, number):
    path = _batch_path(job, number)
    path.parent.mkdir(parents=True, exist_ok=True)
    batch = ArchiveBatch.objects.create(job=job, number=number, file_name=str(path.relative_to(storage_dir())), rows=len(rows))
    try:
        info = codec.write_batch(path, job.table, columns, pk, rows)  # encode errors surface here, before any delete
        problem = codec.verify_batch(path, info["rows"], info["sha256"], info["pk_sha256"])
        if problem:
            raise ArchiveError(f"Archive file failed verification: {problem}")
    except Exception as exc:
        batch.status, batch.error = B_ABORTED, str(exc)[:2000]
        batch.save(update_fields=["status", "error"])
        path.unlink(missing_ok=True)
        raise
    batch.sha256, batch.pk_sha256, batch.pk_min, batch.pk_max = info["sha256"], info["pk_sha256"], info["pk_min"], info["pk_max"]
    batch.save()
    keys = [r[columns.index(pk)] for r in rows]
    try:
        with transaction.atomic(using=adapter.alias):
            deleted = adapter.execute(sql.delete_batch(adapter, job.table, pk, refs, len(keys)), keys)
            if deleted != len(keys):  # a referencing row appeared, or rows vanished: undo and keep everything
                raise ArchiveError(f"Deleted {deleted} of {len(keys)} rows; batch rolled back.")
            batch.status = B_MOVED
            batch.save(update_fields=["status"])
    except Exception as exc:
        batch.status, batch.error = B_ABORTED, str(exc)[:2000]
        batch.save(update_fields=["status", "error"])
        path.unlink(missing_ok=True)
        raise
    job.processed_rows += len(keys)
    job.save(update_fields=["processed_rows"])


def run_restore(job_id: int) -> ArchiveJob:
    job = _claim(job_id, KIND_RESTORE)
    if job is None:
        return ArchiveJob.objects.get(pk=job_id)
    try:
        adapter = _adapter()
        if job.table not in allowed_tables() or job.table not in adapter.tables():
            raise ArchiveError("Table is not available for restore.")
        pk = adapter.primary_key_columns(job.table)
        current = {c.name for c in adapter.columns(job.table)}
        for batch in job.parent.batches.filter(status__in=(B_MOVED, B_RESTORE_FAILED)).order_by("-number"):
            job.refresh_from_db(fields=["cancel_requested"])
            if job.cancel_requested:
                _finish(job, CANCELLED, "Cancelled between batches.")
                return job
            _restore_batch(job, adapter, pk, current, batch)
        _finish(job, DONE)
        _audit(job, "archive_restored", f"{job.processed_rows} rows into {job.table}")
    except Exception as exc:  # noqa: BLE001
        log.exception("restore job %s failed", job_id)
        _finish(job, FAILED, f"{type(exc).__name__}: {exc}")
        _audit(job, "archive_restore_failed", str(exc))
    return job


def _restore_batch(job, adapter, pk, current_columns, batch):
    path = _resolve_inside_storage(storage_dir() / batch.file_name)
    problem = codec.verify_batch(path, batch.rows, batch.sha256, batch.pk_sha256)
    if problem:
        _mark_restore_failed(batch, f"verification failed: {problem}")
        raise ArchiveError(f"Batch {batch.number}: {problem}")
    header, rows_iter = codec.read_batch(path)
    columns = header["columns"]
    if len(pk) != 1 or header["pk"] != pk[0] or not set(columns) <= current_columns:
        _mark_restore_failed(batch, "table structure changed since archiving")
        raise ArchiveError(f"Batch {batch.number}: table structure changed since archiving.")
    rows = list(rows_iter)
    pk_index = columns.index(pk[0])
    keys = [r[pk_index] for r in rows]
    for i in range(0, len(keys), 500):
        chunk = keys[i:i + 500]
        if adapter.fetch(sql.existing_keys(adapter, job.table, pk[0], len(chunk)), chunk):
            _mark_restore_failed(batch, "rows with the same primary key already exist")
            raise ArchiveError(f"Batch {batch.number}: rows with the same primary key already exist; nothing was overwritten.")
    insert = sql.insert_row(adapter, job.table, columns)
    try:
        with transaction.atomic(using=adapter.alias):
            for row in rows:
                adapter.execute(insert, row)
            batch.status = B_RESTORED
            batch.save(update_fields=["status"])
    except Exception as exc:
        _mark_restore_failed(batch, str(exc))
        raise
    job.processed_rows += len(rows)
    job.save(update_fields=["processed_rows"])


def _mark_restore_failed(batch, message):
    batch.status, batch.error = B_RESTORE_FAILED, message[:2000]
    batch.save(update_fields=["status", "error"])


def enqueue(job: ArchiveJob) -> None:
    from core.tasks.queue import enqueue as queue

    func = "core.tasks.jobs.run_archive" if job.kind == KIND_ARCHIVE else "core.tasks.jobs.run_archive_restore"
    transaction.on_commit(lambda: queue(func, job.pk))
