"""Backup and archive screens. Every action needs its own permission, is audited, and runs through the service layer / django-q2."""
from __future__ import annotations

from django import forms
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from core.archive import service as archive
from core.backup import service as backup
from core.backup.adapters.base import UnsupportedEngineError
from core.backup.modes import DB_MODES, MODE_LABELS
from core.backup.naming import UnsafePath
from core.models import ArchiveJob, BackupRecord
from core.permissions import user_has_perm

from .base import CoreMixin


class OpsMixin(CoreMixin):
    def need(self, perm: str):
        if not user_has_perm(self.request.user, perm):
            raise PermissionDenied


class BackupList(OpsMixin, View):
    def get(self, request):
        self.need("core.view_backuprecord")
        return render(request, "core/backup/list.html", {
            "overview": backup.overview(), "records": BackupRecord.objects.all()[:50], "modes": [(m, MODE_LABELS[m]) for m in DB_MODES],
            "can_create": user_has_perm(request.user, "core.create_backup"), "can_restore": user_has_perm(request.user, "core.restore_backup"),
            "can_download": user_has_perm(request.user, "core.download_backup"), "can_validate": user_has_perm(request.user, "core.validate_backup")})


class BackupCreate(OpsMixin, View):
    http_method_names = ["post"]

    def post(self, request):
        self.need("core.create_backup")
        try:
            if request.POST.get("kind") == "file":
                record = backup.create_file_backup(request.user, request.POST.get("source"), request.POST.get("exclusions", "").split(),
                                                   bool(request.POST.get("include_hidden")))
            else:
                mode = request.POST.get("mode", "")
                if mode not in DB_MODES:
                    raise backup.BackupError("Unknown backup mode.")
                record = backup.create_database_backup(request.user, mode)
        except (backup.BackupError, UnsupportedEngineError, backup.fileselect.FileBackupError) as exc:
            messages.error(request, str(exc))
            return redirect("core:backup_list")
        backup.audit("backup_requested", record, actor=request.user, remarks=f"{record.mode}")
        backup.enqueue(record)
        messages.success(request, "Backup queued.")
        return redirect("core:backup_list")


class BackupAction(OpsMixin, View):
    http_method_names = ["post"]

    def post(self, request, pk, action):
        record = get_object_or_404(BackupRecord, pk=pk)
        if action == "cancel":
            self.need("core.create_backup")
            backup.request_cancel(record)
        elif action == "validate":
            self.need("core.validate_backup")
            backup.run_validation(record.pk, request.user.pk)
        elif action == "delete-file":
            self.need("core.delete_backuprecord")
            backup.delete_file(record, "manual")
            backup.audit("backup_file_deleted", record, actor=request.user)
        else:
            raise Http404
        return redirect("core:backup_list")


class BackupDownload(OpsMixin, View):
    def get(self, request, pk):
        self.need("core.download_backup")
        record = get_object_or_404(BackupRecord, pk=pk)
        if not record.has_file:
            raise Http404
        try:
            path = backup.record_path(record)
        except UnsafePath:
            raise Http404 from None
        if not path.is_file():
            raise Http404
        backup.audit("backup_downloaded", record, actor=request.user)
        response = FileResponse(open(path, "rb"), as_attachment=True, filename=record.file_name, content_type="application/octet-stream")
        response["X-Content-Type-Options"] = "nosniff"
        return response


class BackupRestore(OpsMixin, View):
    """Preflight checklist + typed confirmation phrase; runs in the worker. Partial extracts can never be restored."""

    def get(self, request, pk):
        self.need("core.restore_backup")
        record = get_object_or_404(BackupRecord, pk=pk)
        checks = backup.restore_preflight(record)
        return render(request, "core/backup/restore.html", {"record": record, "checks": checks, "ok": backup.preflight_ok(checks),
                                                            "phrase": backup.confirmation_phrase(record) if backup.preflight_ok(checks) else ""})

    def post(self, request, pk):
        self.need("core.restore_backup")
        record = get_object_or_404(BackupRecord, pk=pk)
        checks = backup.restore_preflight(record)
        if not backup.preflight_ok(checks) or request.POST.get("phrase", "").strip() != backup.confirmation_phrase(record):
            messages.error(request, "The confirmation phrase does not match, or a preflight check failed.")
            return redirect("core:backup_restore", pk=pk)
        from django.db import transaction

        from core.tasks.queue import enqueue

        transaction.on_commit(lambda: enqueue("core.tasks.jobs.run_backup_restore", record.pk, request.user.pk))
        messages.warning(request, "Restore queued. A safety backup is taken first.")
        return redirect("core:backup_list")


class ArchiveForm(forms.Form):
    table = forms.ChoiceField()
    older_than_days = forms.IntegerField(min_value=1, max_value=36500, initial=365)
    batch_size = forms.IntegerField(min_value=1, max_value=archive.MAX_BATCH, required=False)

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.fields["table"].choices = [(t, t) for t in archive.allowed_tables()]
        for f in self.fields.values():
            f.widget.attrs["class"] = "form-select" if isinstance(f, forms.ChoiceField) else "form-control"


class ArchiveList(OpsMixin, View):
    def get(self, request):
        self.need("core.view_archivejob")
        form = ArchiveForm(request.GET or None)
        preview = None
        if request.GET.get("table") and form.is_valid():
            preview = archive.preview(form.cleaned_data["table"], form.cleaned_data["older_than_days"])
        return render(request, "core/archive/list.html", {"form": form, "preview": preview, "jobs": ArchiveJob.objects.all()[:50],
                      "can_run": user_has_perm(request.user, "core.run_archive"), "can_restore": user_has_perm(request.user, "core.restore_archive")})

    def post(self, request):
        self.need("core.run_archive")
        form = ArchiveForm(request.POST)
        if not form.is_valid() or not request.POST.get("confirm"):
            messages.error(request, "Choose a table and age, preview it, and tick the confirmation box.")
            return redirect("core:archive_list")
        try:
            job = archive.create_archive_job(request.user, form.cleaned_data["table"], form.cleaned_data["older_than_days"],
                                             form.cleaned_data.get("batch_size"), confirm=True)
        except archive.ArchiveError as exc:
            messages.error(request, str(exc))
            return redirect("core:archive_list")
        archive.enqueue(job)
        messages.success(request, "Archive job queued.")
        return redirect("core:archive_list")


class ArchiveAction(OpsMixin, View):
    http_method_names = ["post"]

    def post(self, request, pk, action):
        job = get_object_or_404(ArchiveJob, pk=pk)
        try:
            if action == "cancel":
                self.need("core.run_archive")
                archive.request_cancel(job)
            elif action == "restore":
                self.need("core.restore_archive")
                restore = archive.create_restore_job(request.user, job)
                archive.enqueue(restore)
                messages.success(request, "Restore job queued.")
            else:
                raise Http404
        except archive.ArchiveError as exc:
            messages.error(request, str(exc))
        return redirect("core:archive_list")
