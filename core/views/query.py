"""Query builder pages: pick a root model -> columns / joins / functions -> filters -> group & aggregate -> result filters -> sort
-> chart -> preview -> save / share / export. Saved queries are ``QueryReport`` rows (JSON re-validated on every load)."""
from __future__ import annotations

import json

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.text import slugify
from django.views import View

from core.models import QueryReport
from core.reports import service as report_service
from core.reports.query import (AGGREGATES, CHART_TYPES, FUNCTIONS, HAVING_OPERATORS, OPERATOR_LABELS, OPERATORS, QueryError,
                                all_query_models, allowed_for, catalogue, export_bytes, get_query_model, preview, to_json, validate)
from core.reports.query.catalogue import tables
from core.reports.query.engine import FILTER_FUNCTIONS
from core.reports.menu import MenuPlacementError, apply_placement, group_choices, read_placement
from core.reports.resources.formats import available_formats
from core.reports.scope import remember_scope, report_ctx, scope_ui

from .base import CoreMixin, safe_next
from .exports import _attachment

MAX_DEFINITION = 50000


def _definition(raw: str) -> dict:
    if len(raw or "") > MAX_DEFINITION:
        raise QueryError("The query is too large.")
    try:
        data = json.loads(raw or "{}")
    except json.JSONDecodeError:
        raise QueryError("The query is not valid.") from None
    if not isinstance(data, dict):
        raise QueryError("The query is not valid.")
    return data


def _visible(view: CoreMixin):
    return report_service.visible_definitions(view.request.user, view.ctx.member_ids, model=QueryReport)


def _root_or_403(view: CoreMixin, key: str):
    entry = get_query_model(key)
    if entry is None or not entry.root:
        raise Http404
    if not allowed_for(view.request.user, entry):
        raise PermissionDenied
    return entry


class QueryStart(CoreMixin, View):
    """Choose the root model; lists the user's own, shared and standard saved queries."""

    def get(self, request):
        models = [m for m in all_query_models() if m.root and allowed_for(request.user, m)]
        return render(request, "core/reports/query_start.html", {"models": models, "saved": visible_queries(self, models)})


def visible_queries(view: CoreMixin, models=None) -> list:
    """Saved queries the user may open (own, shared, standard) whose root table they can still use; also shown on Reports."""
    if models is None:
        models = [m for m in all_query_models() if m.root and allowed_for(view.request.user, m)]
    keys = {m.key for m in models}
    saved = [q for q in _visible(view).select_related("owner") if q.root in keys]
    for q in saved:
        q.editable = report_service.can_edit(view.request.user, q)
        q.root_label = get_query_model(q.root).label
    return saved


class QueryBuilder(CoreMixin, View):
    """GET renders the builder (``?q=<id>`` opens a saved query); POST ``action=preview|save|export``."""

    def _saved(self, request, entry):
        if not request.GET.get("q"):
            return None
        return get_object_or_404(_visible(self), pk=request.GET["q"], root=entry.key)

    def get(self, request, key):
        entry = _root_or_403(self, key)
        saved = self._saved(request, entry)
        self.report_ctx = report_ctx(request, saved)
        definition = dict(saved.definition) if saved else {}
        context = self._context(request, entry, saved, definition)
        if saved and definition.get("columns"):
            try:
                spec = validate(entry, request.user, definition)
                context.update(preview(spec, self.report_ctx, request.GET.get("page") or 1), ran=True)
            except QueryError as exc:
                context["error"] = str(exc)
        return render(request, "core/reports/query_builder.html", context)

    def _context(self, request, entry, saved, definition):
        share_users, share_groups = report_service.share_targets(self.request.user)
        fields = catalogue(entry, request.user)
        groups: dict[str, list] = {}
        for f in fields:
            groups.setdefault(f["g"], []).append(f)
        options = {
            "functions": {k: {"label": v[0], "kinds": v[1], "result": v[2], "arg": v[3]} for k, v in FUNCTIONS.items()},
            "filterFunctions": list(FILTER_FUNCTIONS), "operators": OPERATORS, "operatorLabels": OPERATOR_LABELS,
            "havingOperators": list(HAVING_OPERATORS), "aggregates": AGGREGATES, "chartTypes": list(CHART_TYPES),
            "tables": tables(fields),
        }
        can_edit = saved is None or report_service.can_edit(request.user, saved)
        return {"entry": entry, "saved": saved, "definition": definition, "fields": fields, "field_groups": groups,
                "options": options, "formats": sorted(available_formats()) + ["pdf"],
                "can_edit": can_edit, "back_url": safe_next(request, ""),
                "menu_groups": group_choices(request.user, keep=saved.menu_parent_id if saved else None) if can_edit else None,
                "share_users": share_users.exclude(pk=request.user.pk), "share_groups": share_groups,
                "shared_user_ids": set(saved.shared_users.values_list("pk", flat=True)) if saved else set(),
                "shared_group_ids": set(saved.shared_groups.values_list("pk", flat=True)) if saved else set(),
                "scope": scope_ui(request.user, self.report_ctx)}

    def post(self, request, key):
        entry = _root_or_403(self, key)
        saved = self._saved(request, entry)
        self.report_ctx = report_ctx(request, saved)
        action = request.POST.get("action", "preview")
        try:
            spec = validate(entry, request.user, _definition(request.POST.get("definition", "")))
        except QueryError as exc:
            if action == "preview" or request.headers.get("HX-Request"):
                return render(request, "core/reports/_query_preview.html", {"error": str(exc)})
            messages.error(request, str(exc))
            return redirect(request.get_full_path())
        if action == "preview":
            try:
                context = preview(spec, self.report_ctx, request.POST.get("page") or 1)
            except QueryError as exc:
                context = {"error": str(exc)}
            return render(request, "core/reports/_query_preview.html", {**context, "ran": True})
        if action == "export":
            try:
                data, name, ctype = export_bytes(spec, self.report_ctx, request.POST.get("format", "csv"),
                                                 title=(request.POST.get("name") or (saved.name if saved else "")).strip()[:150])
            except QueryError as exc:
                messages.error(request, str(exc))
                return redirect(request.get_full_path())
            return _attachment(data, name, ctype)
        if action == "save":
            return self._save(request, entry, saved, spec)
        raise Http404

    def _save(self, request, entry, saved, spec):
        self.require_company()
        name = (request.POST.get("name") or "").strip()[:150]
        if not name:
            messages.error(request, "Give the report a name.")
            return redirect(request.get_full_path())
        editing = saved is not None and report_service.can_edit(request.user, saved)
        try:                                                      # menu group: checked on the server, before anything is saved
            placement = read_placement(request.user, request.POST, current=saved.menu_parent_id if editing else None)
        except MenuPlacementError as exc:
            messages.error(request, str(exc))
            return redirect(request.get_full_path())
        target = saved if editing else QueryReport(owner=request.user, root=entry.key)
        apply_placement(target, placement)
        target.name, target.slug = name, slugify(name)[:170] or "report"
        target.description = (request.POST.get("description") or "").strip()[:300]
        target.definition = to_json(spec)
        try:
            with transaction.atomic():
                target.save()
                remember_scope(target, request, self.report_ctx)
                report_service.apply_sharing(request.user, target, visibility=request.POST.get("visibility", ""),
                                             user_ids=request.POST.getlist("shared_users"), group_ids=request.POST.getlist("shared_groups"),
                                             is_system=bool(request.POST.get("is_system")))
        except IntegrityError:
            messages.error(request, "You already have a report with that name.")
            return redirect(request.get_full_path())
        messages.success(request, "Report saved.")
        return redirect(f"{reverse('core:query_builder', args=[entry.key])}?q={target.pk}")


class QueryReportAction(CoreMixin, View):
    http_method_names = ["post"]

    def post(self, request, pk, action):
        report = get_object_or_404(_visible(self), pk=pk)
        _root_or_403(self, report.root)
        if action == "delete":
            if not report_service.can_edit(request.user, report):
                raise PermissionDenied
            report.delete()
            messages.success(request, "Report deleted.")
            return redirect("core:query_start")
        if action == "clone":
            self.require_company()
            base, n = f"{report.name} (copy)"[:140], 1
            name = base
            while QueryReport.objects.filter(owner=request.user, slug=slugify(name)[:170]).exists():
                n += 1
                name = f"{base} {n}"
            copy = QueryReport.objects.create(name=name, slug=slugify(name)[:170] or "report", root=report.root, owner=request.user,
                                              definition=report.definition,
                                              description=report.description)
            messages.success(request, "Report cloned.")
            return redirect(f"{reverse('core:query_builder', args=[copy.root])}?q={copy.pk}")
        raise Http404
