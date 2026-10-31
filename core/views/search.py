"""Saved-search endpoints (save / default / pin / clone / share / delete) and the live builder preview."""
from __future__ import annotations

import json

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View

from core.crud.access import allowed, scoped_queryset
from core.crud.registry import get_resource
from core.crud.service import display_value
from core.models import SavedSearch
from core.reports.registry import get_report_source
from core.search import service
from core.search.builder.state import SearchState, apply_state, clamp_page_size, clean_sort, parse_state
from core.search.errors import SearchError
from core.search.registry import get_search

from .base import CoreMixin, safe_next


def _target_url(target_type: str, key: str) -> str:
    return reverse("core:resource_list", args=[key]) if target_type == "resource" else reverse("core:report_builder", args=[key])


def _may_use_target(view: CoreMixin, target_type: str, key: str) -> None:
    """The user must be allowed to view the target itself; searches never grant access."""
    if target_type == "resource":
        cfg = get_resource(key)
        if cfg is None or not allowed(view.request.user, cfg.perm("view")):
            raise PermissionDenied
    elif target_type == "report_source":
        source = get_report_source(key)
        if source is None or not all(allowed(view.request.user, p) for p in source.permission):
            raise PermissionDenied
    else:
        raise Http404


def _state_from_post(post, spec) -> SearchState:
    state = parse_state(spec, post, strict=True)
    state.page_size = clamp_page_size(post.get("page_size") or None)
    return state


class SaveSearch(CoreMixin, View):
    http_method_names = ["post"]

    def post(self, request):
        target_type, key = request.POST.get("target_type", ""), request.POST.get("target_key", "")
        _may_use_target(self, target_type, key)
        if not service.can_build(request.user):
            raise PermissionDenied
        self.require_company()
        try:
            spec = get_search(target_type, key)
            state = _state_from_post(request.POST, spec)
            instance = None
            if request.POST.get("id"):
                mine = Q(owner=request.user) | (Q(is_system=True) if request.user.is_superuser else Q(pk__in=[]))
                instance = get_object_or_404(SavedSearch.objects.filter(mine), pk=request.POST["id"], target_type=target_type, target_key=key)
            saved = service.save_search(request.user, self.ctx.company_id, target_type=target_type, key=key, name=request.POST.get("name", ""),
                                        state=state, visibility=request.POST.get("visibility", "private"),
                                        pinned=bool(request.POST.get("pinned")), is_default=bool(request.POST.get("is_default")),
                                        instance=instance, is_system=bool(request.POST.get("is_system")))
        except SearchError as exc:
            messages.error(request, str(exc))
            return redirect(safe_next(request, _target_url(target_type, key)))
        if saved.pinned_to_menu:
            from core.menu.service import invalidate_menus

            invalidate_menus()
        messages.success(request, f"Saved search “{saved.name}” saved.")
        return redirect(f"{_target_url(target_type, key)}?saved={saved.pk}")


class SavedSearchAction(CoreMixin, View):
    http_method_names = ["post"]

    def post(self, request, pk, action):
        saved = get_object_or_404(SavedSearch.objects.filter(Q(company_id__in=self.ctx.member_ids) | Q(is_system=True)), pk=pk)
        _may_use_target(self, saved.target_type, saved.target_key)
        back = safe_next(request, _target_url(saved.target_type, saved.target_key))
        try:
            if action == "delete":
                service.delete_search(request.user, saved)
                messages.success(request, "Saved search deleted.")
                return redirect(back)
            if action == "default":
                service.set_default(request.user, saved, request.POST.get("value", "1") == "1")
            elif action == "pin":
                service.pin(request.user, saved, request.POST.get("value", "1") == "1")
            elif action == "clone":
                saved = service.clone(request.user, saved)
            elif action == "share":
                service.share(request.user, saved, request.POST.get("visibility", "private"))
            else:
                raise Http404
        except SearchError as exc:
            messages.error(request, str(exc))
            return redirect(back)
        messages.success(request, "Saved search updated.")
        return redirect(f"{_target_url(saved.target_type, saved.target_key)}?saved={saved.pk}")


class SearchPreview(CoreMixin, View):
    """Live (HTMX) preview of the builder: validated, scoped, permission-checked, bounded to a handful of rows."""

    def post(self, request):
        target_type, key = request.POST.get("target_type", ""), request.POST.get("target_key", "")
        _may_use_target(self, target_type, key)
        if not service.can_build(request.user):
            raise PermissionDenied
        try:
            spec = get_search(target_type, key)
            state = _state_from_post(request.POST, spec)
            if target_type == "resource":
                cfg = get_resource(key)
                qs = apply_state(spec, scoped_queryset(cfg, self.ctx, action="view"), state)
                total = qs.count()
                names = list(cfg.list_fields)[:5]
                rows = [[display_value(o, n) for n in names] for o in qs[:10]]
                headers = [cfg.field_label(n) for n in names]
            else:
                from core.reports.builder import build_queryset, validate_definition

                source = get_report_source(key)
                names = list(source.default_columns)[:5]
                result = build_queryset(source, validate_definition(source, {"columns": names}), self.ctx, state)
                total = result.queryset.count()
                rows = [[row.get(n, "") for n in names] for row in result.queryset[:10]]
                headers = [source.columns[n].label for n in names]
        except SearchError as exc:
            return render(request, "core/search/_preview.html", {"error": str(exc)})
        return render(request, "core/search/_preview.html", {"total": total, "rows": rows, "headers": headers})
