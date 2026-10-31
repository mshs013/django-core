"""Dynamic Search Builder (Administration › Search builder, superusers): build *standard* searches for a list page or a report
source. Everyone who can open that page finds them in its "Saved searches" menu and can use them, paginated like any list, but only
a superuser can change or delete them. One standard search per page can be applied by default to everyone who has not picked a
default of their own."""
from __future__ import annotations

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from core.crud.registry import get_resource
from core.models import SavedSearch
from core.search import service
from core.search.builder.state import SearchState
from core.search.errors import SearchError
from core.search.registry import all_specs, get_search

from .base import CoreMixin
from .crud import builder_fields
from .search import _state_from_post, _target_url


def _label(spec, name: str) -> str:
    entry = spec.filterable.get(name)
    if entry is not None:
        return entry.label
    if spec.target_type == "resource":
        return get_resource(spec.key).field_label(name)
    try:
        text = str(spec.model._meta.get_field(name).verbose_name)
    except Exception:
        text = name.replace("_", " ")
    return text[:1].upper() + text[1:]


def _targets() -> list[dict]:
    out = [{"type": s.target_type, "key": s.key, "label": s.label, "kind": "List page" if s.target_type == "resource" else "Report"}
           for s in all_specs() if s.target_type in ("resource", "report_source")]
    return sorted(out, key=lambda t: (t["kind"], t["label"].lower()))


class SuperuserOnly(CoreMixin):
    def dispatch(self, request, *args, **kwargs):
        if not getattr(request.user, "is_authenticated", False) or not request.user.is_superuser:
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)


class SearchBuilderList(SuperuserOnly, View):
    def get(self, request):
        labels = {(t["type"], t["key"]): t for t in _targets()}
        searches = []
        for s in SavedSearch.objects.filter(is_system=True).order_by("target_type", "target_key", "name"):
            target = labels.get((s.target_type, s.target_key))
            if target is None:
                continue
            searches.append({"obj": s, "target": target, "open": f"{_target_url(s.target_type, s.target_key)}?saved={s.pk}",
                             "valid": service.validated_state(s) is not None})
        return render(request, "core/search/builder_list.html", {"searches": searches, "targets": _targets()})


class SearchBuilderEdit(SuperuserOnly, View):
    def _spec(self, target_type, key):
        try:
            return get_search(target_type, key)
        except Exception:
            raise Http404 from None

    def _render(self, request, spec, saved, state, status=200):
        sortable = [(name, _label(spec, name)) for name in spec.sortable_fields]
        current_sort = state.sort[0] if state.sort else ""
        return render(request, "core/search/builder_edit.html", {
            "spec": spec, "saved": saved, "state": state, "builder_fields": builder_fields(spec), "sortable": sortable,
            "sort_field": current_sort.lstrip("-"), "sort_desc": current_sort.startswith("-"),
            "target_url": _target_url(spec.target_type, spec.key)}, status=status)

    def get(self, request, target_type, key):
        spec = self._spec(target_type, key)
        saved = None
        if request.GET.get("id"):
            saved = get_object_or_404(SavedSearch, pk=request.GET["id"], is_system=True, target_type=target_type, target_key=key)
        state = (service.validated_state(saved) if saved else None) or SearchState()
        return self._render(request, spec, saved, state)

    def post(self, request, target_type, key):
        spec = self._spec(target_type, key)
        self.require_company()
        saved = None
        if request.POST.get("id"):
            saved = get_object_or_404(SavedSearch, pk=request.POST["id"], is_system=True, target_type=target_type, target_key=key)
        post = request.POST.copy()
        field, desc = post.get("sort_field", ""), post.get("sort_dir") == "desc"
        post["sort"] = f"{'-' if desc else ''}{field}" if field else ""
        try:
            state = _state_from_post(post, spec)
            if spec.target_type == "resource":
                from core.crud.registry import get_resource

                cfg = get_resource(key)
                if cfg is None:
                    raise Http404
            is_default = bool(request.POST.get("is_default"))
            if is_default:                                          # one default standard search per page
                from core.audit.service import audited_update

                audited_update(SavedSearch.objects.filter(is_system=True, target_type=target_type, target_key=key, is_default=True).exclude(
                    pk=getattr(saved, "pk", None)), message="no longer the default", is_default=False)
            obj = service.save_search(request.user, saved.company_id if saved else self.ctx.company_id, target_type=target_type, key=key,
                                      name=request.POST.get("name", ""), state=state, visibility="public", is_default=is_default,
                                      instance=saved, is_system=True)
        except SearchError as exc:
            messages.error(request, str(exc))
            return self._render(request, spec, saved, SearchState(quick=post.get("q", "")), status=400)
        messages.success(request, f"Standard search “{obj.name}” saved. Everyone who can open {spec.label} can now use it.")
        return redirect("core:search_builder")
