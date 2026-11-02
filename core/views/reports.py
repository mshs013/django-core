"""Report builder views: source -> columns -> filters -> groupings -> aggregates -> sort -> bounded preview -> save/export."""
from __future__ import annotations

import datetime as dt
import json

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View

from core.crud.access import allowed
from core.models import ReportDefinition
from core.permissions import PAGE_PERMS
from core.reports.builder import ReportError, run_preview, validate_definition
from core.reports.registry import AGGREGATES, all_report_sources, get_report_source
from core.reports.resources import export as exporter
from core.reports.resources.formats import available_formats
from core.reports import service as report_service
from core.reports.menu import MenuPlacementError, apply_placement, group_choices, group_paths, read_placement
from core.reports.scope import remember_scope, report_ctx, scope_ui
from core.search import service as saved_service
from core.search.errors import SearchError
from core.search.registry import OPERATOR_LABELS, get_search

from .base import CoreMixin, safe_next
from .crud import build_search_state, builder_fields
from .exports import _attachment


def _source_or_403(view: CoreMixin, key: str):
    source = get_report_source(key)
    if source is None:
        raise Http404
    if not all(allowed(view.request.user, p) for p in source.permission):
        raise PermissionDenied
    return source


def visible_definitions(user, company_ids, member_ids):
    return report_service.visible_definitions(user, member_ids)


def date_label(source) -> str:
    """Heading of the run's date filter: the verbose name of the source's ``date_field`` (followed through relations)."""
    model, label = source.model, "Date"
    for part in (source.date_field or "").split("__"):
        try:
            field = model._meta.get_field(part)
        except Exception:  # noqa: BLE001 - an annotation or odd path: keep the generic label
            return "Date"
        label = str(getattr(field, "verbose_name", part))
        model = field.related_model or model
    return label[:1].upper() + label[1:]


def _definition_json(raw: str) -> dict:
    if len(raw or "") > 20000:
        raise ReportError("The report definition is too large.")
    try:
        data = json.loads(raw or "{}")
    except json.JSONDecodeError:
        raise ReportError("The report definition is not valid.") from None
    if not isinstance(data, dict):
        raise ReportError("The report definition is not valid.")
    return data


class ReportList(CoreMixin, View):
    required_perm = PAGE_PERMS["reports"]

    def get(self, request):
        from core.reports.custom import all_custom_reports

        sources = [s for s in all_report_sources() if all(allowed(request.user, p) for p in s.permission)]
        custom = [r for r in all_custom_reports() if all(allowed(request.user, p) for p in r.permission)]
        defs = visible_definitions(request.user, self.ctx.company_ids, self.ctx.member_ids).select_related("owner")
        defs = [d for d in defs if get_report_source(d.source_key) is not None
                and all(allowed(request.user, p) for p in get_report_source(d.source_key).permission)]
        from core.views.query import visible_queries

        queries = visible_queries(self)
        paths = group_paths() if any(r.menu_parent_id for r in [*defs, *queries]) else {}
        for d in defs:
            d.editable = report_service.can_edit(request.user, d)
        for r in [*defs, *queries]:                   # menu icon: where the report is shown in the sidebar
            r.menu_path = paths.get(r.menu_parent_id, "a hidden menu group") if r.menu_parent_id else ""
        return render(request, "core/reports/list.html", {"sources": sources, "standard": [d for d in defs if d.is_system],
                                                          "definitions": [d for d in defs if not d.is_system], "custom_reports": custom,
                                                          "queries": queries})


class CustomReportView(CoreMixin, View):
    """A code-defined report: GET shows the parameter form; ``?run=1`` adds a bounded preview; ``?format=<ext>`` downloads."""

    def get(self, request, key):
        from django.core.paginator import Paginator

        from core import conf
        from core.reports.custom import custom_report_bytes, execute, get_custom_report

        report = get_custom_report(key)
        if report is None:
            raise Http404
        if not all(allowed(request.user, p) for p in report.permission):
            raise PermissionDenied
        submitted = "run" in request.GET or "format" in request.GET or report.form is None
        ctx = report_ctx(request)
        form = report.make_form(request.GET if submitted and report.form is not None else None, ctx=ctx)
        if form is not None:
            for form_field in form.fields.values():                   # Tabler classes; plain DateFields get the browser date picker
                widget = form_field.widget
                kind = getattr(widget, "input_type", "")
                widget.attrs.setdefault("class", "form-check-input" if kind == "checkbox" else "form-select" if kind == "select" else "form-control")
                if kind == "text" and type(form_field).__name__ == "DateField":
                    widget.input_type = "date"
        formats = [f for f in report.formats if f == "pdf" or f in available_formats()]
        context = {"report": report, "form": form, "formats": formats, "scope": scope_ui(request.user, ctx)}
        if submitted and (form is None or form.is_valid()):
            params = form.cleaned_data if form is not None else {}
            ext = request.GET.get("format")
            try:
                output = execute(report, ctx, params)
                if ext:
                    if ext not in formats:
                        raise Http404
                    data, name, ctype = custom_report_bytes(report, output, ctx, ext)
                    return _attachment(data, name, ctype)
            except SearchError as exc:
                context["error"] = str(exc)
            else:
                page = Paginator(output.rows, conf.get("CORE_PREVIEW_ROWS")).get_page(request.GET.get("page"))
                rows = page.object_list
                if report.date_formats:                                       # the report's own formats win over the site's
                    from core.formatting import format_value

                    dated = (dt.date, dt.time)
                    rows = [{k: format_value(v, report.date_formats) if isinstance(v, dated) else v for k, v in r.items()} for r in rows]
                    output.info = [(label, format_value(v, report.date_formats) if isinstance(v, dated) else v) for label, v in output.info]
                context.update(output=output, page=page, rows=rows, total=output.total_row(), ran=True)
        return render(request, "core/reports/custom.html", context)


class ReportBuilder(CoreMixin, View):
    """GET renders the builder; POST ``action=preview|save|export``. A saved definition is opened with ``?def=<id>``."""

    def _load(self, request, key):
        source = _source_or_403(self, key)
        definition, saved = {}, None
        if request.GET.get("def"):
            saved = get_object_or_404(visible_definitions(request.user, self.ctx.company_ids, self.ctx.member_ids), pk=request.GET["def"],
                                      source_key=key)
            definition = {"columns": saved.columns, "filters": saved.filters, "groupings": saved.groupings, "aggregates": saved.aggregates,
                          "sorting": saved.sorting, "calculated": saved.calculated, "parameters": saved.parameters, "page_size": saved.page_size,
                          "chart": saved.chart}
        if request.method == "GET" and request.GET.get("definition"):          # sent by the builder's own Preview / Search buttons (HTMX)
            try:
                definition = _definition_json(request.GET["definition"])
            except ReportError:
                definition = {}
        if not definition.get("columns"):                                      # show the default columns ticked, as they are what the preview uses
            definition["columns"] = list(source.default_columns)
        self.report_ctx = report_ctx(request, saved)          # the run's company scope: its own choice, the saved default, the user's
        return source, definition, saved

    def _context(self, request, source, definition, saved, extra=None):
        spec = get_search("report_source", source.key)
        _s, state, saved_searches = build_search_state(request, source.key, "report_source", self.ctx)
        share_users, share_groups = report_service.share_targets(self.request.user)
        can_edit = saved is None or report_service.can_edit(request.user, saved)
        return {"source": source, "saved": saved, "definition": definition, "definition_json": json.dumps(definition),
                "can_edit": can_edit, "back_url": safe_next(request, ""), "date_label": date_label(source),
                "menu_groups": group_choices(request.user, keep=saved.menu_parent_id if saved else None) if can_edit else None,
                "share_users": share_users.exclude(pk=request.user.pk), "share_groups": share_groups,
                "shared_user_ids": set(saved.shared_users.values_list("pk", flat=True)) if saved else set(),
                "shared_group_ids": set(saved.shared_groups.values_list("pk", flat=True)) if saved else set(),
                "source_columns": list(source.columns.values()), "aggregate_fns": sorted(AGGREGATES),
                "column_kinds": {c.name: c.kind for c in source.columns.values()}, "builder_fields": builder_fields(spec),
                "operator_labels": OPERATOR_LABELS, "state": state, "saved_list": saved_searches, "target_type": "report_source",
                "target_key": source.key, "formats": sorted(available_formats()) + ["pdf"], "spec": spec,
                "scope": scope_ui(request.user, self.report_ctx), **(extra or {})}

    def get(self, request, key):
        source, definition, saved = self._load(request, key)
        context = self._context(request, source, definition, saved)
        try:
            spec = validate_definition(source, definition)
            context.update(run_preview(source, spec, self.report_ctx, context["state"], context["state"].page))
        except SearchError as exc:
            context["error"] = str(exc)
        if request.headers.get("HX-Request") == "true" and request.headers.get("HX-Target") == "report-preview":
            return render(request, "core/reports/_preview.html", context)     # Preview / Search only refresh the results, keeping the chosen columns
        return render(request, "core/reports/builder.html", context)

    def post(self, request, key):
        source, _definition, saved = self._load(request, key)
        action = request.POST.get("action", "preview")
        try:
            definition = _definition_json(request.POST.get("definition", ""))
            spec = validate_definition(source, definition)
        except SearchError as exc:
            if request.headers.get("HX-Request"):
                return render(request, "core/reports/_preview.html", {"error": str(exc), "source": source})
            messages.error(request, str(exc))
            return redirect("core:report_builder", key)
        _s, state, _l = build_search_state(request, source.key, "report_source", self.ctx)
        if action == "preview":
            context = {"source": source, **run_preview(source, spec, self.report_ctx, state, int(request.POST.get("page") or 1))}
            return render(request, "core/reports/_preview.html", context)
        if action == "save":
            self.require_company()
            name = (request.POST.get("name") or "").strip()[:150]
            if not name:
                messages.error(request, "Give the report a name.")
                return redirect("core:report_builder", key)
            editing = saved is not None and report_service.can_edit(request.user, saved)
            try:                                                  # menu group: checked on the server, before anything is saved
                placement = read_placement(request.user, request.POST, current=saved.menu_parent_id if editing else None)
            except MenuPlacementError as exc:
                messages.error(request, str(exc))
                return redirect(f"{request.path}?def={saved.pk}" if saved else request.path)
            target = saved if editing else ReportDefinition(owner=request.user, source_key=key)
            apply_placement(target, placement)
            target.name = name                                    # someone else's / a standard report -> saved as the user's own copy
            target.columns, target.filters, target.groupings = spec.columns, spec.filters, spec.groupings
            target.aggregates, target.sorting, target.calculated = spec.aggregates, spec.sorting, spec.calculated
            target.parameters, target.page_size, target.chart = spec.parameters, spec.page_size, spec.chart
            target.slug = ""
            from django.db import IntegrityError, transaction
            from django.utils.text import slugify

            target.slug = slugify(name)[:170] or "report"
            try:
                with transaction.atomic():
                    target.save()
                    remember_scope(target, request, self.report_ctx)
                    report_service.apply_sharing(request.user, target, visibility=request.POST.get("visibility", ""),
                                                 user_ids=request.POST.getlist("shared_users"), group_ids=request.POST.getlist("shared_groups"),
                                                 is_system=bool(request.POST.get("is_system")))
            except IntegrityError:
                messages.error(request, "You already have a report with that name.")
                return redirect("core:report_builder", key)
            messages.success(request, "Report saved.")
            return redirect(f"{request.path}?def={target.pk}")
        if action == "export":
            ext = request.POST.get("format", "csv")
            if ext != "pdf" and ext not in available_formats():
                raise Http404
            _cols, qs = exporter.report_rows(source, spec, self.report_ctx, state)
            if exporter.needs_background(qs.count()):
                exporter.create_job(request.user, self.report_ctx, target_type="report", key=key, ext=ext,
                                    params={"definition": definition, "search": state.to_params()})
                messages.info(request, "This export is large, so it runs in the background. Download it from Exports when it is ready.")
                return redirect("core:export_jobs")
            data, name, ctype = exporter.export_report_bytes(source, spec, self.report_ctx, state, ext)
            return _attachment(data, name, ctype)
        raise Http404


class ReportDefinitionAction(CoreMixin, View):
    http_method_names = ["post"]

    def post(self, request, pk, action):
        definition = get_object_or_404(visible_definitions(request.user, self.ctx.company_ids, self.ctx.member_ids), pk=pk)
        _source_or_403(self, definition.source_key)
        if action == "delete":
            if not report_service.can_edit(request.user, definition):
                raise PermissionDenied
            definition.delete()
            messages.success(request, "Report deleted.")
            return redirect("core:report_list")
        if action == "clone":
            self.require_company()
            definition.pk, definition.owner = None, request.user
            from django.utils.text import slugify

            base, n = f"{definition.name} (copy)"[:140], 1
            name = base
            while ReportDefinition.objects.filter(owner=request.user, slug=slugify(name)[:170]).exists():
                n += 1
                name = f"{base} {n}"
            definition.name, definition.visibility, definition.is_system = name, "private", False
            definition.menu_parent, definition.menu_label, definition.menu_position = None, "", 100     # a clone is not in the menu
            definition.slug = slugify(name)[:170] or "report"
            definition.save()                                     # M2M sharing is not copied: a clone starts private
            messages.success(request, "Report cloned.")
            return redirect(f"{reverse('core:report_builder', args=[definition.source_key])}?def={definition.pk}")
        if action == "share":
            if not report_service.can_edit(request.user, definition) or request.POST.get("visibility") not in report_service.VISIBILITY_VALUES:
                raise PermissionDenied
            report_service.apply_sharing(request.user, definition, visibility=request.POST["visibility"],
                                         user_ids=request.POST.getlist("shared_users"), group_ids=request.POST.getlist("shared_groups"),
                                         is_system=definition.is_system if "is_system" not in request.POST else request.POST.get("is_system") == "1")
            messages.success(request, "Sharing updated.")
            return redirect("core:report_list")
        raise Http404
