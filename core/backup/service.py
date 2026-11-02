"""Backup orchestration (Django layer): creates and runs jobs, validates, restores, applies retention.

Every function that runs a job takes a record id (what django-q2 passes) and reloads the row, so the web process, a worker and the
management commands all share one code path. Anything stored on the row or written to a log passes through ``redact`` first.
"""
from __future__ import annotations

import datetime as dt
import gzip
import logging
import os
import time
from pathlib import Path

from django.conf import settings

from core import conf
from django.db import transaction
from django.utils import timezone

from core.backup.adapters.base import UnsupportedEngineError
from core.backup.adapters.identifiers import InvalidIdentifier
from core.backup.adapters.redact import redact
from core.backup.adapters.registry import get_adapter
from core.backup.adapters.tools import ToolNotFound

from . import commands, fileselect, recent, sqlite as sqlite_backup
from core.models import BackupRecord

from .modes import DATA_ONLY, FILES_MODE, FULL, KIND_DATABASE, KIND_FILE, LAST_24_HOURS, SCHEMA_ONLY
from .naming import UnsafePath, reserve_unique_dir, reserve_unique_file, safe_join, sha256_file, write_sidecar
from .retention import RetentionRow, select_expired
from .runner import SecretFile, ToolFailed, run_tool

log = logging.getLogger(__name__)

RECENT_WINDOW_HOURS = 24
RESTORES_SUBDIR = "restores"


class BackupError(Exception):
    """A job step failed; the message is safe to store and show."""


# ---------------------------------------------------------------- settings helpers
def backup_dir() -> Path:
    path = conf.backup_dir()
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path.resolve()


def restore_allowed() -> bool:
    return bool(bool(conf.get("CORE_BACKUP_ALLOW_RESTORE")))


def storage_dirs() -> list[Path]:
    dirs = [conf.backup_dir(), conf.archive_dir()]
    return [d.resolve(strict=False) for d in dirs]


def _default_file_roots() -> list[str]:
    """Used when CORE_BACKUP_FILE_ROOTS is empty: the project folder, collected static files, media and private media."""
    roots = [("Project", getattr(settings, "BASE_DIR", None)), ("Static", getattr(settings, "STATIC_ROOT", None)),
             ("Media", getattr(settings, "MEDIA_ROOT", None)), ("Private media", conf.private_media_root())]
    return [f"{label}={path}" for label, path in roots if path]


def file_sources() -> list[fileselect.Source]:
    return fileselect.parse_sources(list(conf.get("CORE_BACKUP_FILE_ROOTS")) or _default_file_roots())


def audit(action: str, record: BackupRecord | None = None, actor=None, remarks: str = "", status: str = "SUCCESS", data: dict | None = None, request=None) -> None:
    """Best effort audit event: a failing audit subsystem must never break (or hide) a backup operation."""
    from core.audit.service import record_event

    try:
        record_event("backup", obj=record, user=actor, message=f"{action}: {remarks}"[:480] + ("" if status == "SUCCESS" else " [FAILED]"))
    except Exception:  # noqa: BLE001
        log.exception("audit event %s could not be recorded", action)


# ---------------------------------------------------------------- record helpers
def _update(record: BackupRecord, **fields) -> None:
    """Write fields to the row without clobbering columns other code paths changed (cancel flag, validation...)."""
    BackupRecord.objects.filter(pk=record.pk).update(**fields)
    for name, value in fields.items():
        setattr(record, name, value)


def _progress(record: BackupRecord, percent: int, message: str = "") -> None:
    percent = max(0, min(99, int(percent)))
    now = time.monotonic()
    last = getattr(record, "_last_progress_write", 0.0)
    if percent == record.progress and not message:
        return
    if now - last < 1.0 and percent < 99:           # at most one write per second
        return
    record._last_progress_write = now
    _update(record, progress=percent, message=message[:255] or record.message)


def cancel_requested(record: BackupRecord) -> bool:
    return BackupRecord.objects.filter(pk=record.pk, cancel_requested=True).exists()


def _claim(record_id: int) -> BackupRecord | None:
    """QUEUED -> RUNNING atomically; a duplicate delivery of the same task finds nothing to claim."""
    claimed = BackupRecord.objects.filter(pk=record_id, status=BackupRecord.QUEUED, cancel_requested=False).update(
        status=BackupRecord.RUNNING, started_at=timezone.now(), message="Starting")
    return BackupRecord.objects.filter(pk=record_id).first() if claimed else None


def _remove_files(path: Path | None) -> None:
    if path is None:
        return
    for candidate in (path, path.with_name(path.name + ".json")):
        try:
            candidate.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            log.warning("could not remove %s", candidate.name)


def record_path(record: BackupRecord) -> Path:
    """Absolute path of the backup file; raises UnsafePath unless it is strictly inside BACKUP_DIR."""
    return safe_join(backup_dir(), record.file_name)


# ---------------------------------------------------------------- creating jobs
def create_database_backup(user, mode: str, alias: str = "default") -> BackupRecord:
    if mode not in (SCHEMA_ONLY, DATA_ONLY, FULL, LAST_24_HOURS):
        raise BackupError("Unknown backup mode.")
    adapter = get_adapter(alias)          # UnsupportedEngineError for Oracle/SQLite
    return BackupRecord.objects.create(kind=KIND_DATABASE, mode=mode, engine=adapter.engine, db_alias=alias,
                                       created_by=user if getattr(user, "pk", None) else None)


def create_file_backup(user, source_index: int, extra_exclusions: list[str] | None = None, include_hidden: bool = True) -> BackupRecord:
    sources = file_sources()
    source = fileselect.pick_source(sources, source_index)
    return BackupRecord.objects.create(
        kind=KIND_FILE, mode=FILES_MODE, source_label=source.label[:255],
        options={"source_index": source.index, "extra_exclusions": list(extra_exclusions or []), "include_hidden": bool(include_hidden)},
        created_by=user if getattr(user, "pk", None) else None)


def enqueue(record: BackupRecord) -> None:
    """Queue the job with django-q2 after the surrounding transaction commits (so the worker can see the row)."""
    from core.tasks.queue import enqueue as queue

    func = "core.tasks.jobs.run_database_backup" if record.kind == KIND_DATABASE else "core.tasks.jobs.run_file_backup"
    transaction.on_commit(lambda: queue(func, record.pk))


def request_cancel(record: BackupRecord) -> bool:
    """QUEUED jobs are cancelled at once; RUNNING jobs are asked to stop (the worker checks regularly)."""
    if BackupRecord.objects.filter(pk=record.pk, status=BackupRecord.QUEUED).update(
            status=BackupRecord.CANCELLED, finished_at=timezone.now(), message="Cancelled before it started", cancel_requested=True):
        return True
    return bool(BackupRecord.objects.filter(pk=record.pk, status=BackupRecord.RUNNING).update(cancel_requested=True, message="Cancelling..."))


# ---------------------------------------------------------------- running: database
def _tool_version(tool: str) -> str:
    try:
        return run_tool([tool, "--version"], timeout=30).strip()[:200]
    except ToolFailed:
        return ""


def _native_dump(adapter, mode: str, dest: Path, record: BackupRecord) -> dict:
    level = int(conf.get("CORE_BACKUP_COMPRESSION"))
    cancel = lambda: cancel_requested(record)  # noqa: E731
    if adapter.engine == "sqlite":
        info = sqlite_backup.dump(Path(adapter.connection.settings_dict["NAME"]), dest, mode, level)
        return {"format": "SQL script (gzip, sqlite3 iterdump)", "tool": "python-sqlite3", "tool_version": sqlite_backup.sqlite3.sqlite_version, **info}
    conn = adapter.connection_settings()
    timeout = int(conf.get("CORE_BACKUP_TIMEOUT_SECONDS"))
    tool = adapter.tool_name(adapter.dump_tool_kind())
    if adapter.engine == "mysql":
        with SecretFile(commands.mysql_options(conn)) as cnf:
            run_tool(commands.mysql_dump_argv(tool, cnf, conn, mode), secrets=conn.secrets, timeout=timeout, stdout_to=dest,
                     gzip_level=max(0, min(9, level)), should_cancel=cancel)
        return {"format": "mysqldump / mariadb-dump SQL (gzip)", "tool": Path(tool).name, "tool_version": _tool_version(tool)}
    run_tool(commands.pg_dump_argv(tool, conn, mode, str(dest), level), env=commands.pg_env(conn), secrets=conn.secrets,
             timeout=timeout, should_cancel=cancel)
    return {"format": "pg_dump custom (compressed)", "tool": Path(tool).name, "tool_version": _tool_version(tool)}


def _recent_exports(adapter, since: dt.datetime, until: dt.datetime, skipped: dict, record: BackupRecord):
    """Yield (table, column, column names, row iterator) for tables that have a usable creation timestamp; fill ``skipped`` for the rest.

    A table without one is NEVER exported whole: that would turn a "last 24 hours" extract into a full dump.
    """
    tables = adapter.tables()
    for number, table in enumerate(tables, start=1):
        if cancel_requested(record):
            raise BackupError("Cancelled")
        _progress(record, number * 95 // max(1, len(tables)), f"Scanning {table}")
        choice = adapter.creation_timestamp_choice(table)
        if choice.column is None:
            skipped[table] = choice.reason
            continue
        try:
            names = [c.name for c in adapter.columns(table)]
            select = ", ".join(adapter.quote(n) for n in names)
            column = adapter.quote(choice.column)
            order = ", ".join(adapter.quote(c) for c in adapter.primary_key_columns(table)) or column
            sql = f"SELECT {select} FROM {adapter.quote(table)} WHERE {column} >= %s AND {column} <= %s ORDER BY {order}"
        except InvalidIdentifier:
            skipped[table] = "table or column name is not a plain identifier; cannot be queried safely"
            continue
        params = [adapter.window_param(since), adapter.window_param(until)]
        yield table, choice.column, names, _stream_rows(adapter, sql, params)


def _stream_rows(adapter, sql: str, params: list):
    with adapter.connection.chunked_cursor() as cursor:
        cursor.execute(sql, params)
        while rows := cursor.fetchmany(2000):
            yield from rows


def _recent_dump(adapter, dest: Path, record: BackupRecord) -> dict:
    until = timezone.now()
    since = until - dt.timedelta(hours=RECENT_WINDOW_HOURS)
    skipped: dict[str, str] = {}
    manifest = recent.write_bundle(
        dest, engine=adapter.engine, since=since, until=until, exports=_recent_exports(adapter, since, until, skipped, record),
        skipped=skipped, compression_level=int(conf.get("CORE_BACKUP_COMPRESSION")))
    return {"format": "JSON lines in tar.gz (partial extract)", **manifest}


def execute_database_backup(record: BackupRecord) -> None:
    """Run an already-claimed (RUNNING) database backup to completion; never raises, always leaves a final status."""
    dest: Path | None = None
    secrets: list[str] = []
    try:
        adapter = get_adapter(record.db_alias or "default")
        secrets = adapter.connection_settings().secrets
        dest = reserve_unique_file(backup_dir(), f"db-{adapter.engine}-{record.mode}", commands.db_extension(adapter.engine, record.mode))
        _update(record, file_name=dest.name, message="Dumping the database")
        details = _recent_dump(adapter, dest, record) if record.mode == LAST_24_HOURS else _native_dump(adapter, record.mode, dest, record)
        if cancel_requested(record):
            raise BackupError("Cancelled")
        _finish_success(record, dest, details, adapter)
    except Exception as exc:  # noqa: BLE001
        _finish_failure(record, dest, exc, secrets)


def _finish_success(record: BackupRecord, dest: Path, details: dict, adapter=None) -> None:
    _update(record, message="Checking the result", progress=99)
    size = dest.stat().st_size
    digest = sha256_file(dest)
    try:
        verdict = validate_file(record.kind, record.mode, record.engine, dest, details)
    except BackupError as exc:
        raise BackupError(f"The backup was written but failed validation: {exc}") from None
    manifest = _manifest_for_record(details)
    metadata = {"id": record.pk, "kind": record.kind, "mode": record.mode, "engine": record.engine, "file": dest.name, "size": size, "sha256": digest,
                "created_at": record.created_at.isoformat(), "finished_at": timezone.now().isoformat(),
                "server_version": adapter.server_version() if adapter is not None else "", "details": manifest}
    write_sidecar(dest, metadata)
    now = timezone.now()
    _update(record, status=BackupRecord.SUCCESS, progress=100, message="Backup complete", error="", file_size=size, sha256=digest, manifest=manifest,
            finished_at=now, validation_status="OK", validation_message=verdict, validated_at=now)
    audit("backup_finished", record, actor=record.created_by, remarks=f"{record.mode} backup finished", data={"file": dest.name, "size": size})


def _manifest_for_record(details: dict) -> dict:
    """What is stored on the row: JSON-safe, no secrets (details never contain connection data)."""
    import json

    return json.loads(json.dumps(details, default=str))


KNOWN_ERRORS = (BackupError, ToolFailed, ToolNotFound, fileselect.FileBackupError, recent.BundleError, UnsupportedEngineError,
                sqlite_backup.SqliteBackupError)


def _finish_failure(record: BackupRecord, dest: Path | None, exc: Exception, secrets: list[str]) -> None:
    """Remove the partial file, store a redacted reason. Unexpected exceptions are logged (redacted) with their type name."""
    cancelled = isinstance(exc, KNOWN_ERRORS) and "cancelled" in str(exc).lower()
    _remove_files(dest)
    if isinstance(exc, KNOWN_ERRORS):
        text = str(exc)
    else:
        text = f"{type(exc).__name__}: {exc}"
        log.error("backup %s failed: %s", record.pk, redact(text, secrets, limit=500))
    safe = redact(text, secrets, limit=2000)
    _update(record, status=BackupRecord.CANCELLED if cancelled else BackupRecord.FAILED, finished_at=timezone.now(), file_name="", file_size=0,
            message="Cancelled" if cancelled else "Backup failed", error="" if cancelled else safe)
    audit("backup_failed", record, actor=record.created_by, remarks="Backup cancelled" if cancelled else safe[:500], status="FAILED")


def run_database_backup(record_id: int) -> None:
    record = _claim(record_id)
    if record is None:
        return
    audit("backup_started", record, actor=record.created_by, remarks=f"{record.mode} database backup")
    execute_database_backup(record)


# ---------------------------------------------------------------- running: files
def execute_file_backup(record: BackupRecord) -> None:
    dest: Path | None = None
    try:
        sources = file_sources()
        source = fileselect.pick_source(sources, record.options.get("source_index"))
        fileselect.refuse_overlap_with_storage(source.path, storage_dirs())
        rules = fileselect.ExclusionRules.build(
            source.path, config_paths=[], config_patterns=list(conf.get("CORE_BACKUP_FILE_EXCLUDES")),
            extra=record.options.get("extra_exclusions", []), storage_dirs=storage_dirs(), include_hidden=bool(record.options.get("include_hidden", True)))
        dest = reserve_unique_file(backup_dir(), f"files-{source.label}", commands.FILES_EXTENSION)
        _update(record, file_name=dest.name, message="Archiving files")
        stats = fileselect.build_archive(dest, source.path, rules, compression_level=int(conf.get("CORE_BACKUP_COMPRESSION")),
                                         progress=lambda pct, msg: _progress(record, pct, msg), should_cancel=lambda: cancel_requested(record))
        stats["source"] = source.label
        stats.pop("root", None)          # the server path stays out of stored manifests; the label identifies the source
        stats["include_hidden"] = bool(record.options.get("include_hidden", True))
        _finish_success(record, dest, stats)
    except Exception as exc:  # noqa: BLE001
        _finish_failure(record, dest, exc, [])


def run_file_backup(record_id: int) -> None:
    record = _claim(record_id)
    if record is None:
        return
    audit("backup_started", record, actor=record.created_by, remarks=f"file backup of {record.source_label}")
    execute_file_backup(record)


# ---------------------------------------------------------------- validation
def validate_file(kind: str, mode: str, engine: str, path: Path, details: dict) -> str:
    """Engine-appropriate integrity check of a backup file; returns a human message, raises BackupError."""
    try:
        if kind == KIND_FILE:
            return fileselect.validate_archive(path, details.get("files"))
        if mode == LAST_24_HOURS:
            return recent.validate_bundle(path)
        if engine == "postgresql":
            return _validate_pg_dump(path, mode)
        if engine == "mysql":
            return _validate_mysql_dump(path)
        return sqlite_backup.validate(path, mode)
    except (fileselect.FileBackupError, recent.BundleError, ToolFailed, ToolNotFound, sqlite_backup.SqliteBackupError) as exc:
        raise BackupError(str(exc)) from None


def _validate_pg_dump(path: Path, mode: str) -> str:
    from core.backup.adapters.tools import resolve_tool

    tool = resolve_tool(("pg_restore",), conf.get("CORE_BACKUP_TOOLS_DIR") or None, prefer_postgres_lib=True)
    listing = run_tool(commands.pg_list_argv(tool, str(path)), timeout=600)
    entries = [line for line in listing.splitlines() if line and not line.startswith(";")]
    if not entries and mode != DATA_ONLY:
        raise BackupError("pg_restore found no objects in the dump")
    return f"ok: pg_restore can read the dump ({len(entries)} entries)"


def _validate_mysql_dump(path: Path) -> str:
    """Readable gzip whose last line is the footer both MySQL/MariaDB dump tools write after a complete dump."""
    last, statements = "", 0
    try:
        with gzip.open(path, "rt", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if line.strip():
                    last = line.strip()
                if line.startswith(("CREATE TABLE", "INSERT INTO")):
                    statements += 1
    except (OSError, EOFError) as exc:
        raise BackupError(f"Not a readable gzip file: {exc}") from None
    if not last.startswith(commands.MYSQL_DUMP_COMPLETED_FOOTER):
        raise BackupError("The dump is incomplete: no '-- Dump completed' footer")
    return f"ok: complete dump ({statements} CREATE TABLE / INSERT statements)"


def run_validation(record_id: int, actor_id: int | None = None) -> None:
    """Re-check an existing backup: file present inside BACKUP_DIR, checksum matches, format readable. Stores the verdict on the row."""
    record = BackupRecord.objects.filter(pk=record_id).first()
    if record is None:
        return
    _update(record, validation_status="RUNNING", validation_message="Validating...")
    try:
        if not record.has_file:
            raise BackupError("This backup has no file to validate.")
        path = record_path(record)
        if not path.is_file():
            raise BackupError("The backup file is missing from the backup directory.")
        if sha256_file(path) != record.sha256:
            raise BackupError("Checksum mismatch: the file changed after the backup was made.")
        verdict = validate_file(record.kind, record.mode, record.engine, path, record.manifest or {})
        _update(record, validation_status="OK", validation_message="Checksum matches; " + verdict, validated_at=timezone.now())
        audit("backup_validated", record, remarks="Validation passed", data={"id": record.pk})
    except (BackupError, UnsafePath, OSError) as exc:
        _update(record, validation_status="FAILED", validation_message=redact(str(exc), limit=1000), validated_at=timezone.now())
        audit("backup_validated", record, remarks="Validation failed", status="FAILED", data={"id": record.pk})


# ---------------------------------------------------------------- retention
def apply_retention(now: dt.datetime | None = None, dry_run: bool = False) -> dict:
    """Delete backup FILES older than BACKUP_RETENTION_DAYS, always keeping the newest BACKUP_MIN_KEEP successful per (kind, mode)."""
    now = now or timezone.now()
    rows = [RetentionRow(r.id, r.kind, r.mode, r.status, r.created_at, r.file_deleted)
            for r in BackupRecord.objects.filter(status=BackupRecord.SUCCESS, file_deleted=False).only("id", "kind", "mode", "status", "created_at", "file_deleted")]
    ids = select_expired(rows, now, int(conf.get("CORE_BACKUP_KEEP_DAYS")), int(conf.get("CORE_BACKUP_MIN_KEEP")))
    deleted = 0
    if not dry_run:
        for record in BackupRecord.objects.filter(pk__in=ids):
            delete_file(record, reason="retention")
            deleted += 1
        if deleted:
            audit("backup_retention", remarks=f"Retention removed {deleted} backup file(s)", data={"ids": ids})
    return {"candidates": ids, "deleted": deleted, "dry_run": dry_run}


def delete_file(record: BackupRecord, reason: str = "manual") -> None:
    """Remove the backup file (and sidecar) but keep the history row."""
    try:
        _remove_files(record_path(record) if record.file_name else None)
    except UnsafePath:
        log.warning("refusing to delete outside BACKUP_DIR (record %s)", record.pk)
    _update(record, file_deleted=True, file_deleted_at=timezone.now(), message=f"File deleted ({reason})"[:255])


# ---------------------------------------------------------------- restore
def confirmation_phrase(record: BackupRecord) -> str:
    """What the operator must type: the target database name for a database restore, 'RESTORE FILES' for a file restore."""
    if record.kind == KIND_FILE:
        return f"RESTORE FILES {record.pk}"
    adapter = get_adapter(record.db_alias or "default")
    return f"RESTORE {adapter.connection_settings().name}"


def restore_preflight(record: BackupRecord) -> list[tuple[str, bool, str]]:
    """Checklist of (check, passed, detail). A restore may start only when every check passes."""
    checks: list[tuple[str, bool, str]] = []

    def add(name: str, ok: bool, detail: str = "") -> None:
        checks.append((name, ok, detail))

    add("Restore is enabled (CORE_BACKUP_ALLOW_RESTORE)", restore_allowed(), "" if restore_allowed() else "Set CORE_BACKUP_ALLOW_RESTORE=1 to enable restores.")
    add("The backup finished successfully", record.status == BackupRecord.SUCCESS and not record.file_deleted)
    add("This is not a partial extract", not record.is_partial, "LAST_24_HOURS extracts cannot be restored over a database." if record.is_partial else "")
    path = None
    try:
        path = record_path(record) if record.has_file else None
    except UnsafePath:
        path = None
    exists = bool(path and path.is_file())
    add("The backup file is present in the backup directory", exists)
    add("The checksum still matches", exists and sha256_file(path) == record.sha256)
    if record.kind == KIND_DATABASE:
        try:
            adapter = get_adapter(record.db_alias or "default")
            add("The server uses the same database engine", adapter.engine == record.engine, f"backup: {record.engine}, server: {adapter.engine}")
            try:
                if adapter.engine != "sqlite":
                    adapter.tool_name("pg_restore" if adapter.engine == "postgresql" else adapter.client_tool_kind())
                add("The restore tool is installed", True)
            except ToolNotFound as exc:
                add("The restore tool is installed", False, str(exc))
        except UnsupportedEngineError as exc:
            add("The database engine is supported", False, str(exc))
    else:
        add("A source for this backup is still configured", any(s.label == record.source_label for s in file_sources()))
    return checks


def preflight_ok(checks) -> bool:
    return all(ok for _name, ok, _detail in checks)


def run_restore(record_id: int, actor_id: int | None = None) -> None:
    """Restore ``record``. Database: a FULL safety backup is taken first (the restore is aborted if it fails). Files: extract into a NEW directory."""
    from django.contrib.auth import get_user_model

    record = BackupRecord.objects.filter(pk=record_id).first()
    if record is None:
        return
    actor = get_user_model().objects.filter(pk=actor_id).first() if actor_id else None
    checks = restore_preflight(record)
    if not preflight_ok(checks):
        failed = "; ".join(name for name, ok, _d in checks if not ok)
        _update(record, restore_status="FAILED", restore_message=f"Preflight failed: {failed}"[:1000])
        audit("restore_failed", record, actor=actor, remarks=f"Preflight failed: {failed}", status="FAILED")
        return
    _update(record, restore_status="RUNNING", restore_message="Restore started", restore_target="")
    audit("restore_started", record, actor=actor, remarks=f"Restore of backup #{record.pk}")
    secrets: list[str] = []
    try:
        path = record_path(record)
        if record.kind == KIND_FILE:
            target = reserve_unique_dir(backup_dir() / RESTORES_SUBDIR, f"restore-{record.pk}")
            count = fileselect.safe_extract(path, target)
            outcome = {"restore_status": "SUCCESS", "restore_message": f"Extracted {count} files into a new folder", "restore_target": str(target.relative_to(backup_dir()))}
        else:
            adapter = get_adapter(record.db_alias or "default")
            secrets = adapter.connection_settings().secrets
            message = _database_restore(adapter, record, path, actor)
            outcome = {"restore_status": "SUCCESS", "restore_message": message}
        # A database restore replaces the table holding this row with the backup's copy, so the row may be gone or older: best effort.
        BackupRecord.objects.filter(pk=record.pk).update(restored_at=timezone.now(), **outcome)
        audit("restore_finished", record, actor=actor, remarks=outcome["restore_message"])
    except Exception as exc:  # noqa: BLE001
        message = redact(str(exc) if isinstance(exc, KNOWN_ERRORS + (OSError,)) else type(exc).__name__, secrets, limit=1000)
        BackupRecord.objects.filter(pk=record.pk).update(restore_status="FAILED", restore_message=message)
        audit("restore_failed", record, actor=actor, remarks=message, status="FAILED")


def _database_restore(adapter, record: BackupRecord, path: Path, actor) -> str:
    safety = BackupRecord.objects.create(kind=KIND_DATABASE, mode=FULL, engine=adapter.engine, db_alias=record.db_alias or "default",
                                         created_by=actor, status=BackupRecord.RUNNING, started_at=timezone.now(),
                                         message="Safety backup before restore", options={"safety_for_restore_of": record.pk})
    execute_database_backup(safety)
    safety.refresh_from_db()
    if safety.status != BackupRecord.SUCCESS:
        raise BackupError("The safety backup taken before the restore failed, so the restore was not started.")
    if adapter.engine == "sqlite":
        target = reserve_unique_file(backup_dir() / RESTORES_SUBDIR, f"restore-{record.pk}", ".sqlite3")
        target.unlink()                                  # the unique NAME is reserved; restore_to_new_file creates the file itself
        counts = sqlite_backup.restore_to_new_file(path, target)
        return (f"Restored a COPY ({sum(counts.values())} rows in {len(counts)} tables) to restores/{target.name}. "
                "SQLite restores never overwrite the live file: stop the app and swap it in.")
    conn = adapter.connection_settings()
    timeout = int(conf.get("CORE_BACKUP_TIMEOUT_SECONDS"))
    if adapter.engine == "mysql":
        tool = adapter.tool_name(adapter.client_tool_kind())
        if record.mode == DATA_ONLY:                     # a data-only dump has only INSERTs (django_migrations too): empty every table first;
            adapter.delete_all_rows()                    # FULL drops and recreates them. MySQL cannot do this in one transaction: if the
            adapter.execute(f"DELETE FROM {adapter.quote('django_migrations')}")   # import fails, restore the safety backup taken above
        with SecretFile(commands.mysql_options(conn)) as cnf:
            run_tool(commands.mysql_restore_argv(tool, cnf, conn), secrets=conn.secrets, timeout=timeout, stdin_from=path, gunzip_stdin=True)
        return "Database restored"
    tool = adapter.tool_name("pg_restore")
    run_tool(commands.pg_restore_argv(tool, conn, record.mode, str(path)), env=commands.pg_env(conn), secrets=conn.secrets, timeout=timeout)
    return "Database restored"


# ---------------------------------------------------------------- overview
def overview() -> dict:
    """Configuration and engine status for the overview page. Never includes passwords."""
    engines = []
    for alias in settings.DATABASES:
        entry = {"alias": alias, "supported": True, "error": "", "tools": {}}
        try:
            adapter = get_adapter(alias)
            conn = adapter.connection_settings()
            entry.update(engine=adapter.engine, host=conn.host or "(local socket)", port=conn.port, user=conn.user, name=conn.name)
            wanted = {"postgresql": ("pg_dump", "pg_restore"), "mysql": ("mysqldump", "mysql")}.get(adapter.engine, ())
            for kind in wanted:
                try:
                    entry["tools"][kind] = Path(adapter.tool_name(kind)).name
                except ToolNotFound:
                    entry["tools"][kind] = ""
        except UnsupportedEngineError as exc:
            entry.update(supported=False, error=str(exc))
        except Exception as exc:  # noqa: BLE001 - e.g. server unreachable while detecting MariaDB
            entry.update(supported=False, error=f"Could not inspect this connection ({type(exc).__name__})")
        engines.append(entry)
    directory = conf.backup_dir()
    return {
        "engines": engines,
        "backup_dir": str(directory), "backup_dir_exists": directory.is_dir(), "backup_dir_writable": directory.is_dir() and os.access(directory, os.W_OK),
        "retention_days": conf.get("CORE_BACKUP_KEEP_DAYS"), "min_keep": conf.get("CORE_BACKUP_MIN_KEEP"),
        "compression_level": conf.get("CORE_BACKUP_COMPRESSION"), "timeout": conf.get("CORE_BACKUP_TIMEOUT_SECONDS"),
        "sources": [{"index": s.index, "label": s.label, "path": str(s.path), "exists": s.path.is_dir()} for s in file_sources()],
        "exclude_paths": [],
        "exclude_patterns": list(conf.get("CORE_BACKUP_FILE_EXCLUDES")),
        "restore_allowed": restore_allowed(),
    }
