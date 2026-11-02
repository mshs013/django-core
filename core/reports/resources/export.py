"""Export orchestration: scoped + filtered queryset -> django-import-export Resource -> file. Small exports are returned
immediately; large ones become ``ExportJob`` rows processed by django-q2 (progress, status, expiring download)."""
from __future__ import annotations

import os
import uuid
from datetime import timedelta

from django.core.files.base import ContentFile
from django.core.exceptions import PermissionDenied
from django.utils import timezone

from core import conf
from core.audit.service import record_event
from core.crud.access import Ctx, scoped_queryset
from core.files.storage import private_storage
from core.models import ExportJob
from core.permissions import user_has_perm
from core.search.builder.state import SearchState, apply_state, parse_state
from core.search.registry import get_search

from .formats import get_format
from .pdf import render_pdf
from .resources import as_row, model_export_resource, rows_resource

CHUNK = 5000


def pdf_meta(ctx: Ctx, **extra) -> dict:
    """Letterhead details for a PDF printed by ``ctx.user`` in the active company."""
    from core.reports.letterhead import user_display_name

    return {"generated_by": user_display_name(ctx.user), "company_id": ctx.company_id, **extra}


def ctx_for(user, company_id, params: dict | None = None) -> Ctx:
    """The job owner's report ``Ctx``: the company scope stored with the job, re-resolved against the owner's memberships and
    setting now (a job without one covers the active company, as before)."""
    from core.reports.scope import ctx_from_params

    return ctx_from_params(user, company_id, params or {})


def _text_chunks(resource, queryset, fmt):
    """CSV/TSV are streamed in chunks so RAM stays bounded; other formats are built in memory under CORE_EXPORT_MAX_ROWS."""
    first = True
    total = queryset.count() if hasattr(queryset, "count") else len(queryset)
    for offset in range(0, total, CHUNK):
        data = fmt.export_data(resource.export(queryset=queryset[offset: offset + CHUNK]))
        text = data.decode("utf-8") if isinstance(data, bytes) else data
        if not first:
            text = text.split("\n", 1)[1] if "\n" in text else ""
        first = False
        yield text


def render(resource, queryset, ext: str) -> tuple[bytes, str]:
    fmt = get_format(ext)
    total = queryset.count() if hasattr(queryset, "count") else len(queryset)
    if total > conf.get("CORE_EXPORT_MAX_ROWS"):
        raise PermissionDenied("This export is too large; narrow the filter.")
    if ext in ("csv", "tsv"):
        body = "".join(_text_chunks(resource, queryset, fmt))
        return ("﻿" + body).encode("utf-8") if ext == "csv" else body.encode("utf-8"), fmt.get_content_type()
    data = fmt.export_data(resource.export(queryset=queryset))
    return (data.encode("utf-8") if isinstance(data, str) else data), fmt.get_content_type()


# ------------------------------------------------------------------------------------------------------------- CRUD resources
def resource_queryset(cfg, ctx: Ctx, state: SearchState):
    return apply_state(get_search("resource", cfg.slug), scoped_queryset(cfg, ctx, action="view"), state)


def export_resource_bytes(cfg, ctx: Ctx, state: SearchState, ext: str) -> tuple[bytes, str, str]:
    if not cfg.allow_export or not user_has_perm(ctx.user, cfg.perm("view")):
        raise PermissionDenied
    from core.reports import scope

    qs = resource_queryset(cfg, ctx, state)
    companies = None                                  # {pk: "A, B"} when more than one company is in scope
    if scope.wants_company_column(ctx, cfg.company_lookup) and qs.count() <= conf.get("CORE_EXPORT_MAX_ROWS"):
        companies = scope.row_companies(cfg.model, cfg.company_lookup, qs.order_by().values("pk"), ctx)
    if ext == "pdf":
        names = [n for n in cfg.list_fields if n != "__str__"] or ["pk"]
        from core.crud.service import display_value

        objects = list(qs[:5000])
        rows = [[display_value(o, n, cfg.date_formats) for n in names] for o in objects]
        headings = [cfg.field_label(n) for n in names]
        if companies is not None:
            rows = [[companies.get(o.pk, "")] + row for o, row in zip(objects, rows)]
            headings = [scope.COMPANY_LABEL] + headings
        data = render_pdf(cfg.label_plural, headings, rows, pdf_meta(ctx, subtitle=f"{qs.count()} rows"))
        content_type = "application/pdf"
    else:
        data, content_type = render(model_export_resource(cfg, companies=companies), qs, ext)
    record_event("other", message=f"export {cfg.slug}.{ext} · {scope.audit_note(ctx)}", model_label=cfg.model._meta.label,
                 company=ctx.company_id, user=ctx.user)
    return data, f"{cfg.slug}.{ext}", content_type


# ------------------------------------------------------------------------------------------------------------- report sources
def report_rows(source, spec, ctx: Ctx, state: SearchState | None):
    from core.reports.builder.run import build_queryset

    result = build_queryset(source, spec, ctx, state)
    return result.columns, result.queryset


def _filled(result, rows):
    """``rows`` in chunks with the Company column filled in (many-company sources); a plain pass-through otherwise."""
    if result.fill is None:
        yield from rows
        return
    batch = []
    for row in rows:
        batch.append(row)
        if len(batch) >= CHUNK:
            yield from result.fill_companies(batch)
            batch = []
    yield from result.fill_companies(batch)


def export_report_bytes(source, spec, ctx: Ctx, state: SearchState | None, ext: str) -> tuple[bytes, str, str]:
    from core.reports.builder.run import build_queryset
    from core.reports.scope import audit_note

    result = build_queryset(source, spec, ctx, state)
    columns, qs = result.columns, result.queryset
    total = qs.count()
    if total > conf.get("CORE_EXPORT_MAX_ROWS"):
        raise PermissionDenied("This export is too large; narrow the filter.")
    from core.reports.builder.run import image_aliases

    images = set(image_aliases(source, columns))
    if ext == "pdf":
        from .pdf import ImageCell

        rows = [[(ImageCell(row.get(a)) if row.get(a) else "") if a in images else row.get(a) for a, _ in columns]
                for row in result.fill_companies(list(qs[:5000]))]
        data = render_pdf(source.label, [label for _, label in columns], rows, pdf_meta(ctx, subtitle=f"{total} rows"), source.pdf_template)
        content_type = "application/pdf"
    else:
        resource = rows_resource(columns)
        rows = (as_row({k: ("Yes" if k in images and v else ("" if k in images else v)) for k, v in r.items()})        # never export storage paths
                for r in _filled(result, qs.iterator(chunk_size=CHUNK)))
        fmt = get_format(ext)
        if ext in ("csv", "tsv"):
            parts, first = [], True
            batch = []
            for row in rows:
                batch.append(row)
                if len(batch) >= CHUNK:
                    parts.append(_one_chunk(resource, batch, fmt, first)); first = False; batch = []
            if batch or first:
                parts.append(_one_chunk(resource, batch, fmt, first))
            body = "".join(parts)
            data = ("﻿" + body).encode("utf-8") if ext == "csv" else body.encode("utf-8")
        else:
            out = fmt.export_data(resource.export(queryset=list(rows)))
            data = out.encode("utf-8") if isinstance(out, str) else out
        content_type = fmt.get_content_type()
    record_event("report_run", message=f"export {source.key}.{ext} · {audit_note(ctx)}", model_label=source.model._meta.label,
                 company=ctx.company_id, user=ctx.user)
    return data, f"{source.key}.{ext}", content_type


def _one_chunk(resource, rows, fmt, first: bool) -> str:
    data = fmt.export_data(resource.export(queryset=rows))
    text = data.decode("utf-8") if isinstance(data, bytes) else data
    return text if first else (text.split("\n", 1)[1] if "\n" in text else "")


# ------------------------------------------------------------------------------------------------------------- background jobs
def needs_background(count: int) -> bool:
    return count > conf.get("CORE_EXPORT_SYNC_MAX_ROWS")


def create_job(user, ctx: Ctx, *, target_type: str, key: str, ext: str, params: dict) -> ExportJob:
    from core.tasks.queue import enqueue

    from core.reports.scope import scope_params

    job = ExportJob.objects.create(owner=user, company_id=ctx.company_id, target_type=target_type, target_key=key, export_format=ext,
                                   params={**params, **scope_params(ctx)})
    from django.db import transaction

    transaction.on_commit(lambda: enqueue("core.tasks.jobs.run_export", job.pk))
    return job


def run_job(job_id: int) -> None:
    """Executed by the worker. Re-derives scope from the owner (never trusts stored ids beyond the owner's memberships)."""
    from core.crud.registry import get_resource
    from core.reports.builder.definition import validate_definition
    from core.reports.registry import get_report_source

    job = ExportJob.objects.select_related("owner").get(pk=job_id)
    if job.status not in (ExportJob.QUEUED,):
        return
    job.status, job.progress = ExportJob.RUNNING, 5
    job.save(update_fields=["status", "progress"])
    try:
        ctx = ctx_for(job.owner, job.company_id, job.params)
        if job.target_type == "resource":
            cfg = get_resource(job.target_key)
            if cfg is None:
                raise ValueError("Unknown resource")
            state = parse_state(get_search("resource", cfg.slug), job.params.get("search", {}), strict=True)
            data, name, _ = export_resource_bytes(cfg, ctx, state, job.export_format)
        else:
            source = get_report_source(job.target_key)
            if source is None or not all(user_has_perm(job.owner, p) for p in source.permission):
                raise PermissionDenied
            spec = validate_definition(source, job.params.get("definition", {}))
            state = parse_state(get_search("report_source", source.key), job.params.get("search", {}), strict=True)
            data, name, _ = export_report_bytes(source, spec, ctx, state, job.export_format)
        stored = private_storage.save(f"exports/{uuid.uuid4().hex}.{job.export_format}", ContentFile(data))
        job.file_name = stored
        job.row_count = 0
        job.status, job.progress = ExportJob.DONE, 100
        job.finished_at = timezone.now()
        job.expires_at = job.finished_at + timedelta(hours=conf.get("CORE_EXPORT_EXPIRY_HOURS"))
        job.error = ""
    except Exception as exc:  # noqa: BLE001 - recorded without payload; details go to the log
        import logging

        logging.getLogger("core.export").exception("export job %s failed", job_id)
        job.status, job.error = ExportJob.FAILED, f"{exc.__class__.__name__}"[:500]
        job.finished_at = timezone.now()
    job.save()


def expire_jobs() -> int:
    """Delete files of expired jobs and mark them expired (rows stay as history)."""
    n = 0
    for job in ExportJob.objects.filter(status=ExportJob.DONE, expires_at__lte=timezone.now()):
        if job.file_name and private_storage.exists(job.file_name):
            private_storage.delete(job.file_name)
        job.status, job.file_name = ExportJob.EXPIRED, ""
        job.save(update_fields=["status", "file_name"])
        n += 1
    return n
