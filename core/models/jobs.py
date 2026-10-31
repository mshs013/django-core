from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils import timezone

from .base import CreatedInfoModel

from core.backup.modes import DATA_ONLY, FILES_MODE, FULL, KIND_DATABASE, KIND_FILE, LAST_24_HOURS, MODE_LABELS, SCHEMA_ONLY


class BackupRecord(CreatedInfoModel):
    """One backup job (database or files), from QUEUED to a final state. Rows are history: retention deletes files, never rows."""

    KIND_CHOICES = [(KIND_DATABASE, "Database"), (KIND_FILE, "Files")]
    MODE_CHOICES = [(SCHEMA_ONLY, "Schema only"), (DATA_ONLY, "Data only"), (FULL, "Full"),
                    (LAST_24_HOURS, "Last 24 hours (partial extract)"), (FILES_MODE, "Files")]
    QUEUED, RUNNING, SUCCESS, FAILED, CANCELLED = "QUEUED", "RUNNING", "SUCCESS", "FAILED", "CANCELLED"
    STATUS_CHOICES = [(QUEUED, "Queued"), (RUNNING, "Running"), (SUCCESS, "Success"), (FAILED, "Failed"), (CANCELLED, "Cancelled")]
    FINAL_STATUSES = (SUCCESS, FAILED, CANCELLED)
    VALIDATION_CHOICES = [("", "Not validated"), ("RUNNING", "Validating"), ("OK", "Valid"), ("FAILED", "Invalid")]
    RESTORE_CHOICES = [("", "Never restored"), ("RUNNING", "Restoring"), ("SUCCESS", "Restored"), ("FAILED", "Restore failed")]

    kind = models.CharField(max_length=16, choices=KIND_CHOICES, db_index=True)
    mode = models.CharField(max_length=24, choices=MODE_CHOICES)
    engine = models.CharField(max_length=16, blank=True)
    db_alias = models.CharField(max_length=64, blank=True)
    source_label = models.CharField(max_length=255, blank=True)
    options = models.JSONField(default=dict, blank=True)

    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=QUEUED, db_index=True)
    progress = models.PositiveSmallIntegerField(default=0)
    message = models.CharField(max_length=255, blank=True)
    error = models.TextField(blank=True)
    cancel_requested = models.BooleanField(default=False)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    file_name = models.CharField(max_length=255, blank=True)
    file_size = models.PositiveBigIntegerField(default=0)
    sha256 = models.CharField(max_length=64, blank=True)
    manifest = models.JSONField(default=dict, blank=True)
    file_deleted = models.BooleanField(default=False)
    file_deleted_at = models.DateTimeField(null=True, blank=True)

    validation_status = models.CharField(max_length=16, choices=VALIDATION_CHOICES, blank=True)
    validation_message = models.TextField(blank=True)
    validated_at = models.DateTimeField(null=True, blank=True)

    restore_status = models.CharField(max_length=16, choices=RESTORE_CHOICES, blank=True)
    restore_message = models.TextField(blank=True)
    restore_target = models.CharField(max_length=255, blank=True)
    restored_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        default_permissions = ("view", "delete")
        permissions = [
            ("create_backup", "Can start database and file backups"),
            ("download_backup", "Can download backup files"),
            ("validate_backup", "Can validate backup files"),
            ("restore_backup", "Can restore a backup (database or files)"),
        ]
        indexes = [models.Index(fields=["kind", "mode", "status", "created_at"], name="core_backup_group_idx")]

    def __str__(self) -> str:
        return f"{self.get_kind_display()} backup #{self.pk} ({self.get_mode_display().lower()})"

    @property
    def mode_label(self) -> str:
        return MODE_LABELS.get(self.mode, self.mode)

    @property
    def is_partial(self) -> bool:
        return self.mode == LAST_24_HOURS

    @property
    def is_active(self) -> bool:
        return self.status in (self.QUEUED, self.RUNNING)

    @property
    def has_file(self) -> bool:
        return self.status == self.SUCCESS and bool(self.file_name) and not self.file_deleted



KIND_ARCHIVE, KIND_RESTORE = "archive", "restore"
KINDS = [(KIND_ARCHIVE, "Archive"), (KIND_RESTORE, "Restore")]
A_QUEUED, A_RUNNING, A_DONE, A_FAILED, A_CANCELLED = "queued", "running", "done", "failed", "cancelled"
JOB_STATUSES = [(A_QUEUED, "Queued"), (A_RUNNING, "Running"), (A_DONE, "Done"), (A_FAILED, "Failed"), (A_CANCELLED, "Cancelled")]
B_WRITTEN, B_MOVED, B_ABORTED, B_RESTORED, B_RESTORE_FAILED = "written", "moved", "aborted", "restored", "restore_failed"
BATCH_STATUSES = [(B_WRITTEN, "Written (not yet removed)"), (B_MOVED, "Moved"), (B_ABORTED, "Aborted"), (B_RESTORED, "Restored"),
                  (B_RESTORE_FAILED, "Restore failed")]


class ArchiveJob(models.Model):
    """A persisted archive (move old rows to files) or restore (put them back) run. Progress lives here, so a worker restart or a
    page reload never loses it."""

    kind = models.CharField(max_length=10, choices=KINDS, default=KIND_ARCHIVE)
    table = models.CharField(max_length=64)
    column = models.CharField(max_length=64, blank=True, help_text="Creation-timestamp column used to select rows.")
    older_than_days = models.PositiveIntegerField(default=0)
    cutoff = models.DateTimeField(null=True, blank=True)
    batch_size = models.PositiveIntegerField(default=1000)
    parent = models.ForeignKey("self", on_delete=models.PROTECT, null=True, blank=True, related_name="restores")
    status = models.CharField(max_length=10, choices=JOB_STATUSES, default=A_QUEUED)
    cancel_requested = models.BooleanField(default=False)
    matched_rows = models.PositiveBigIntegerField(default=0, help_text="Rows older than the cutoff when the job started.")
    blocked_rows = models.PositiveBigIntegerField(default=0, help_text="Rows kept because other tables still reference them.")
    processed_rows = models.PositiveBigIntegerField(default=0, help_text="Rows moved (archive) or re-inserted (restore).")
    error = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    created_at = models.DateTimeField(default=timezone.now)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-id"]
        verbose_name = "archive job"
        verbose_name_plural = "archive jobs"
        permissions = [("run_archive", "Can run a data archive"), ("restore_archive", "Can restore archived data")]
        indexes = [models.Index(fields=["table", "status"], name="core_archjob_table_idx")]

    def __str__(self):
        return f"{self.get_kind_display()} {self.table} #{self.pk}"

    @property
    def progress_percent(self) -> int:
        total = self.matched_rows - self.blocked_rows if self.kind == KIND_ARCHIVE else self.matched_rows
        return 100 if self.status == A_DONE else (min(99, int(self.processed_rows * 100 / total)) if total > 0 else 0)

    @property
    def is_active(self) -> bool:
        return self.status in (A_QUEUED, A_RUNNING)


class ArchiveBatch(models.Model):
    job = models.ForeignKey(ArchiveJob, on_delete=models.CASCADE, related_name="batches")
    number = models.PositiveIntegerField()
    file_name = models.CharField(max_length=100, blank=True)
    rows = models.PositiveIntegerField(default=0)
    sha256 = models.CharField(max_length=64, blank=True)
    pk_sha256 = models.CharField(max_length=64, blank=True)
    pk_min = models.CharField(max_length=100, blank=True)
    pk_max = models.CharField(max_length=100, blank=True)
    status = models.CharField(max_length=15, choices=BATCH_STATUSES, default=B_WRITTEN)
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["job_id", "number"]
        verbose_name = "Archive batch"
        verbose_name_plural = "Archive batches"
        indexes = [models.Index(fields=["job", "number"], name="core_archbatch_job_idx")]

    def __str__(self):
        return f"{self.job_id}/{self.number}"


class ExportJob(CreatedInfoModel):
    """A (possibly background) export of a resource or report. The file lives in private storage and expires."""

    QUEUED, RUNNING, DONE, FAILED, EXPIRED = "queued", "running", "done", "failed", "expired"
    STATUSES = [(QUEUED, "Queued"), (RUNNING, "Running"), (DONE, "Done"), (FAILED, "Failed"), (EXPIRED, "Expired")]

    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    company = models.ForeignKey("core.Company", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    target_type = models.CharField(max_length=20)            # resource | report
    target_key = models.CharField(max_length=100)
    export_format = models.CharField(max_length=10)
    params = models.JSONField(default=dict, blank=True)      # active search state (validated again at run time)
    status = models.CharField(max_length=10, choices=STATUSES, default=QUEUED, db_index=True)
    progress = models.PositiveSmallIntegerField(default=0)
    row_count = models.PositiveIntegerField(default=0)
    file_name = models.CharField(max_length=120, blank=True)
    error = models.CharField(max_length=500, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at", "-pk")
        indexes = [models.Index(fields=["owner", "-created_at"], name="core_exportjob_owner_idx")]

    def __str__(self) -> str:
        return f"Export {self.target_key} #{self.pk}"

    @property
    def is_expired(self) -> bool:
        return self.expires_at is not None and self.expires_at <= timezone.now()


class JobRecord(CreatedInfoModel):
    """One run of a job registered with ``core.jobs.register_job`` (a business app's background work). The row is the history: it
    keeps status, progress, the (redacted) parameters and result, a redacted error, timing and an optional result file. Retention
    deletes old FILES only, never rows (``core.jobs.service.apply_retention``)."""

    QUEUED, RUNNING, DONE, FAILED = "queued", "running", "done", "failed"
    STATUSES = [(QUEUED, "Queued"), (RUNNING, "Running"), (DONE, "Done"), (FAILED, "Failed")]

    job_type = models.CharField(max_length=150, db_index=True)           # the register_job name; only registered types ever run
    label = models.CharField(max_length=200, blank=True)                 # what the list shows (default: the job type's label)
    company = models.ForeignKey("core.Company", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    params = models.JSONField(default=dict, blank=True)                 # redacted before saving: never put credentials here
    status = models.CharField(max_length=10, choices=STATUSES, default=QUEUED, db_index=True)
    progress = models.PositiveSmallIntegerField(default=0)
    progress_message = models.CharField(max_length=255, blank=True)
    result = models.JSONField(default=dict, blank=True)                 # the handler's return value (redacted)
    error = models.TextField(blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    task_id = models.CharField(max_length=64, blank=True)               # django-q2 task id (to find the worker's log)
    file_name = models.CharField(max_length=255, blank=True)            # result file in private storage (jobs/...)
    file_deleted = models.BooleanField(default=False)
    file_deleted_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at", "-pk")
        indexes = [models.Index(fields=["job_type", "created_at"], name="core_jobrecord_type_idx")]
        permissions = [("retry_jobrecord", "Can retry a failed job of another user")]

    def __str__(self) -> str:
        return f"{self.label or self.job_type} #{self.pk}"

    @property
    def is_active(self) -> bool:
        return self.status in (self.QUEUED, self.RUNNING)

    @property
    def has_file(self) -> bool:
        return self.status == self.DONE and bool(self.file_name) and not self.file_deleted

    @property
    def download_name(self) -> str:
        return self.file_name.rsplit("/", 1)[-1]

    @property
    def duration_seconds(self) -> int | None:
        if not self.started_at:
            return None
        return round(((self.finished_at or timezone.now()) - self.started_at).total_seconds())

    def set_progress(self, percent, message: str = "") -> None:
        """For handlers: store progress (clamped to 0..100) and an optional message. Written at once (visible on the jobs page)."""
        try:
            self.progress = max(0, min(100, int(percent)))
        except (TypeError, ValueError):
            pass
        fields = ["progress"]
        if message:
            self.progress_message = str(message)[:255]
            fields.append("progress_message")
        JobRecord.objects.filter(pk=self.pk).update(**{f: getattr(self, f) for f in fields})

    def save_file(self, name: str, data: bytes) -> str:
        """For handlers: store the job's result file (private storage, downloaded from the jobs page). ``name`` is the download name."""
        import os
        import uuid

        from django.core.files.base import ContentFile

        from core.files.storage import private_storage

        base = os.path.basename(str(name or "result")).replace("\\", "_")[:120] or "result"
        self.file_name = private_storage.save(f"jobs/{uuid.uuid4().hex}/{base}", ContentFile(data))
        JobRecord.objects.filter(pk=self.pk).update(file_name=self.file_name)
        return self.file_name
