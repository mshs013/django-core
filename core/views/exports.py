"""Exports/imports for registered resources and the export-job downloads. Export = exactly what the list shows (same search state)."""
from __future__ import annotations

import os
import uuid

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.base import ContentFile
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import redirect, render
from django.views import View

from core.crud.access import scoped_queryset
from core.files.storage import private_storage
from core.files.validators import validate_upload
from core.models import ExportJob
from core.reports.resources import export as exporter
from core.reports.resources.formats import available_formats, get_format
from core.reports.resources.resources import model_import_resource

from .base import CoreMixin
from .crud import ResourceMixin, build_search_state


def _attachment(data: bytes, name: str, content_type: str) -> HttpResponse:
    response = HttpResponse(data, content_type=content_type)
    response["Content-Disposition"] = f'attachment; filename="{name}"'
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "private, no-store"
    return response


class ResourceExport(ResourceMixin, View):
    def get(self, request, ext):
        if not self.cfg.allow_export:
            raise Http404
        self.check("view")
        if ext != "pdf" and ext not in available_formats():
            raise Http404
        from core.reports.scope import report_ctx

        spec, state, _saved = build_search_state(request, self.slug, "resource", self.ctx)
        ctx = report_ctx(request)                         # the export's company scope (?company_scope=..., else the user's setting)
        count = exporter.resource_queryset(self.cfg, ctx, state).count()
        if exporter.needs_background(count):
            job = exporter.create_job(request.user, ctx, target_type="resource", key=self.slug, ext=ext,
                                      params={"search": state.to_params()})
            messages.info(request, "This export is large, so it runs in the background. Download it from Exports when it is ready.")
            return redirect("core:export_jobs")
        data, name, content_type = exporter.export_resource_bytes(self.cfg, ctx, state, ext)
        return _attachment(data, name, content_type)


class RecordExport(ResourceMixin, View):
    """One record as PDF / Excel (the detail page buttons): the same fields, tables and signatures as the page. Offered formats
    and the "only when approved" rule come from ``record.detail_export_formats``; anything else is a 404."""

    def get(self, request, pk, ext):
        from core.approvals.signatures import record_signatures
        from core.audit.service import record_event
        from core.reports.resources.record import detail_export_formats, record_bytes

        from .crud import record_content

        obj = self.obj_or_404(pk)
        self.check("view", obj)
        if ext not in detail_export_formats(self.cfg, obj):
            raise Http404
        details, children = record_content(self.cfg, obj, request)
        data, name, content_type = record_bytes(self.cfg, obj, ext, details, children, record_signatures(obj),
                                                exporter.pdf_meta(self.ctx))
        record_event("other", message=f"export {self.slug} record {ext}", obj=obj, company=self.ctx.company_id, user=request.user)
        return _attachment(data, name, content_type)


class ExportJobs(CoreMixin, View):
    def get(self, request):
        jobs = ExportJob.objects.filter(owner=request.user)[:50]
        return render(request, "core/exports/list.html", {"jobs": jobs})


class ExportJobDownload(CoreMixin, View):
    def get(self, request, pk):
        job = ExportJob.objects.filter(pk=pk, owner=request.user).first()               # only the owner's own jobs
        if job is None:
            raise Http404
        if job.status != ExportJob.DONE or job.is_expired or not job.file_name:
            raise Http404("This export is not available (it may have expired).")
        try:
            handle = private_storage.open(job.file_name, "rb")
        except (FileNotFoundError, ValueError, OSError):
            raise Http404 from None
        fmt = get_format(job.export_format) if job.export_format != "pdf" else None
        ctype = fmt.get_content_type() if fmt else "application/pdf"
        response = FileResponse(handle, as_attachment=True, filename=f"{job.target_key}.{job.export_format}", content_type=ctype)
        response["X-Content-Type-Options"] = "nosniff"
        response["Cache-Control"] = "private, no-store"
        return response


class ResourceImport(ResourceMixin, View):
    """Upload -> validated dry-run preview (nothing written) -> explicit confirm re-runs it for real, in one transaction."""

    def _guard(self):
        if not self.cfg.allow_import:
            raise Http404
        self.check("add")
        self.check("change")
        if self.cfg.company_lookup == "company":
            self.require_company()

    def get(self, request):
        self._guard()
        return self._form(request)

    def _form(self, request, errors=()):
        return render(request, "core/crud/import.html", {"cfg": self.cfg, "formats": sorted(available_formats()), "errors": list(errors),
                                                         "template_formats": TEMPLATE_FORMATS}, status=400 if errors else 200)

    def post(self, request):
        self._guard()
        resource = model_import_resource(self.cfg, self.ctx)
        if request.POST.get("token"):
            token = request.POST["token"]
            if token != request.session.get("core_import_token") or not token.isalnum():
                raise PermissionDenied
            name = request.session.get("core_import_file", "")
            ext = name.rsplit(".", 1)[-1]
            with private_storage.open(name, "rb") as handle:
                dataset = get_format(ext).create_dataset(handle.read() if get_format(ext).is_binary() else handle.read().decode("utf-8-sig"))
            result = resource.import_data(dataset, dry_run=False, raise_errors=False, use_transactions=True)
            private_storage.delete(name)
            request.session.pop("core_import_token", None)
            if result.has_errors() or result.has_validation_errors():
                messages.error(request, "The import was rejected; nothing was saved.")
                return redirect("core:resource_import", self.slug)
            messages.success(request, f"Imported {result.total_rows} rows.")
            return redirect("core:resource_list", self.slug)
        upload = request.FILES.get("file")
        ext = (request.POST.get("format") or "").lower()
        if upload is None or ext not in available_formats():
            messages.error(request, "Choose a file and a format.")
            return redirect("core:resource_import", self.slug)
        try:
            validate_upload(upload, extensions=tuple("." + e for e in available_formats()), max_bytes=20 * 1024 * 1024)
            raw = upload.read()
            fmt = get_format(ext)
            dataset = fmt.create_dataset(raw if fmt.is_binary() else raw.decode("utf-8-sig"))
        except ValidationError as exc:
            return self._form(request, exc.messages)
        except (ValueError, UnicodeDecodeError):
            return self._form(request, [f"The file could not be read as {ext.upper()}."])
        result = resource.import_data(dataset, dry_run=True, raise_errors=False, use_transactions=True)
        context = {"cfg": self.cfg, "result": result, "formats": sorted(available_formats()), "errors": _import_errors(result)}
        if not context["errors"]:
            stored = private_storage.save(f"imports/{uuid.uuid4().hex}.{ext}", ContentFile(raw))
            token = uuid.uuid4().hex
            request.session["core_import_token"], request.session["core_import_file"] = token, stored
            context["token"] = token
        return render(request, "core/crud/import.html", context, status=400 if context["errors"] else 200)


TEMPLATE_FORMATS = ("xlsx", "csv")


class ResourceImportTemplate(ResourceImport):
    """An empty file with exactly the columns the import reads (leave ``id`` blank to add rows; fill it to update a record)."""

    def get(self, request, ext):
        self._guard()
        if ext not in TEMPLATE_FORMATS or ext not in available_formats():
            raise Http404
        fmt = get_format(ext)
        dataset = model_import_resource(self.cfg, self.ctx).export(queryset=self.cfg.model._default_manager.none())
        data = fmt.export_data(dataset)
        return _attachment(data if isinstance(data, bytes) else data.encode("utf-8-sig"), f"{self.slug}-import-template.{ext}",
                           fmt.get_content_type())

    def post(self, request, ext):
        raise Http404


def _import_errors(result) -> list[str]:
    """Readable messages for a dry run: file-level errors, then per-row errors (row validation errors are listed by the template)."""
    out = [str(e.error) for e in result.base_errors]
    for number, errors in result.row_errors():
        out.extend(f"Row {number}: {e.error}" for e in errors)
    if result.has_validation_errors() and not out:
        out.append("Some rows are not valid; see below.")
    return out[:50]
