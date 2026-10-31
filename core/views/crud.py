"""Generic CRUD views driven by the resource registry. The URL slug is looked up in the allowlist; nothing else from the request
selects a model, field, lookup or action."""
from __future__ import annotations

import mimetypes
import re

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import models
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.html import format_html
from django.views import View

from core import approvals, conf
from core.reports.resources.formats import available_formats
from core.crud import formsets as fs
from core.crud.access import Ctx, allowed, get_object_or_404, scoped_queryset
from core.crud.forms import PrivateFileInput, build_form_class, build_tabs, editable_field_names
from core.crud.registry import ResourceConfig, get_resource
from core.crud.service import CrudError, delete_object, display_value, field_label, restore_object, run_bulk_action
from core.approvals.registry import model_lock_statuses
from core.models.approvals import CancelableModel
from core.models.base import SoftDeleteModel
from core.search import service as saved_service
from core.search.builder.state import SearchState, apply_state, paginate, parse_state
from core.search.errors import SearchError
from core.search.registry import OPERATOR_LABELS, get_search

from .base import CoreMixin, htmx_redirect, safe_next
from .modal import is_modal, modal_done

PREFIX_RE = re.compile(r"^[A-Za-z0-9_\-]{1,120}$")


class ResourceMixin(CoreMixin):
    def dispatch(self, request, *args, slug=None, **kwargs):
        cfg = get_resource(slug or "")
        if cfg is None:
            raise Http404("Unknown resource")
        self.cfg: ResourceConfig = cfg
        self.slug = slug
        return super().dispatch(request, *args, **kwargs)

    # -- helpers
    def check(self, action: str, obj=None):
        if not self.cfg.allows(action) and action != "view":
            raise PermissionDenied
        if not allowed(self.request.user, self.cfg.perm(action), obj):
            raise PermissionDenied

    def obj_or_404(self, pk, *, mode="alive", action="view"):
        return get_object_or_404(self.cfg, self.ctx, pk, mode=mode, action=action)

    def base_context(self, **extra):
        return {"cfg": self.cfg, "can_add": self.cfg.allows("add") and allowed(self.request.user, self.cfg.perm("add")),
                "has_trash": self.cfg.is_soft, **extra}

    def report_menu(self) -> dict:
        """Reports offered on the list page: standard ones, the user's own and shared ones, and "new report" per source."""
        from core.reports.service import definitions_for_model

        standard, mine, sources = definitions_for_model(self.request.user, self.ctx.member_ids, self.cfg.model)
        return {"standard_reports": standard, "my_reports": mine, "report_sources": sources}

    def locked(self, obj) -> bool:
        return approvals.is_locked(obj)


def build_search_state(request, cfg_key: str, target_type: str, ctx: Ctx):
    """State from the URL, merged with a saved/default search. Returns (spec, state, saved_list, warnings)."""
    spec = get_search(target_type, cfg_key)
    state = parse_state(spec, request.GET)
    if state.tree.get("items") and not saved_service.can_build(request.user):
        state.tree = {"op": "AND", "items": []}          # users without builder rights filter only through offered searches
        state.warnings.append("Advanced filters are available through the saved searches offered to you.")
    saved_list = saved_service.usable_searches(request.user, target_type, cfg_key, ctx.company_id) if ctx.company_id else []
    explicit = any(k in request.GET for k in ("q", "adv", "sort", "page_size", "saved", "nosaved"))
    chosen = None
    if request.GET.get("saved"):
        chosen = next((s for s in saved_list if str(s.pk) == request.GET["saved"]), None)
        if chosen is None:
            state.warnings.append("That saved search is not available.")
    elif not explicit:
        chosen = (next((s for s in saved_list if s.is_default and s.owner_id == request.user.pk), None)
                  or next((s for s in saved_list if s.is_default and s.is_system), None))     # a standard search applied to everyone
    if chosen is not None:
        loaded = saved_service.validated_state(chosen)
        if loaded is not None:
            if "q" in request.GET:
                loaded.quick = state.quick               # quick text typed on top of an offered search narrows it further
            if "sort" in request.GET:
                loaded.sort = state.sort
            if "adv" in request.GET and saved_service.can_build(request.user):
                loaded.tree = state.tree                 # filters edited on top of the loaded search (the form starts from its filters)
            loaded.page = state.page
            loaded.warnings = state.warnings
            state = loaded
    return spec, state, saved_list


def _list_cell(cfg: ResourceConfig, obj, name):
    """A list cell: image fields show a small thumbnail (served through the private file view), everything else its display value."""
    try:
        f = obj._meta.get_field(name)
    except Exception:
        f = None
    if isinstance(f, models.ImageField) and getattr(obj, name):
        url = reverse("core:resource_file", args=[cfg.slug, obj.pk, name])
        return format_html('<a href="{}" target="_blank" rel="noopener"><img src="{}" alt="" class="core-thumb" loading="lazy"></a>', url, url)
    return display_value(obj, name, cfg.date_formats)


def list_context(cfg: ResourceConfig, ctx: Ctx, request, *, mode="alive"):
    spec, state, saved_list = build_search_state(request, cfg.slug, "resource", ctx)
    qs = scoped_queryset(cfg, ctx, mode=mode, action="view")
    fk_names = [n for n in cfg.list_fields if n != "__str__" and _is_relation(cfg.model, n)]
    if fk_names:
        qs = qs.select_related(*[n for n in fk_names if _is_forward_fk(cfg.model, n)])
    try:
        qs = apply_state(spec, qs, state)
    except SearchError as exc:
        state.warnings.append(str(exc))
        state.tree = {"op": "AND", "items": []}
        qs = apply_state(spec, scoped_queryset(cfg, ctx, mode=mode, action="view"), state)
    page = paginate(qs, state, cfg.page_size)
    lock_statuses = model_lock_statuses(cfg.model)
    statuses = approvals.statuses_for(page.object_list) if lock_statuses is not None else {}
    rows = [{"obj": o, "cells": [_list_cell(cfg, o, n) for n in cfg.list_fields], "approval": statuses.get(o.pk),
             "can_change": cfg.allows("change") and allowed(request.user, cfg.perm("change"), o) and statuses.get(o.pk) not in (lock_statuses or ()),
             "can_delete": cfg.allows("delete") and allowed(request.user, cfg.perm("delete"), o) and statuses.get(o.pk) not in (lock_statuses or ())}
            for o in page.object_list]
    return {"spec": spec, "state": state, "saved_list": saved_list, "page": page, "rows": rows, "approval_column": lock_statuses is not None,
            "columns": [(n, cfg.field_label(n), n in spec.sortable_fields) for n in cfg.list_fields],
            "builder_fields": builder_fields(spec), "operator_labels": OPERATOR_LABELS, "target_type": "resource", "target_key": cfg.slug}


def _is_relation(model, name):
    try:
        return model._meta.get_field(name).is_relation
    except Exception:
        return False


def _is_forward_fk(model, name):
    try:
        f = model._meta.get_field(name)
        return f.is_relation and (f.many_to_one or f.one_to_one) and f.concrete
    except Exception:
        return False


def builder_fields(spec) -> list[dict]:
    """Typed description of the allowlisted filter fields for the Alpine builder."""
    out = []
    for entry in spec.filterable.values():
        out.append({"name": entry.name, "label": entry.label, "kind": entry.kind, "operators": [{"value": o, "label": OPERATOR_LABELS.get(o, o)}
                                                                                                  for o in entry.operators],
                    "choices": [{"value": v, "label": l} for v, l in entry.choices]})
    return out


def _export_scope(request) -> dict:
    """The "Companies" control of the list's export menu (empty unless the user's report setting is on)."""
    from core.reports.scope import report_ctx, scope_ui

    return scope_ui(request.user, report_ctx(request))


class ResourceList(ResourceMixin, View):
    def get(self, request):
        self.check("view")
        context = {**self.base_context(), **list_context(self.cfg, self.ctx, request),
                   "bulk_actions": ["delete"] * (self.cfg.allows("delete")) + list(self.cfg.actions),
                   "export_formats": [*sorted(available_formats()), "pdf"],
                   "scope": _export_scope(request) if self.cfg.allow_export else {},
                   **self.report_menu(),
                   "can_import": bool(self.cfg.allow_import and self.cfg.allows("add") and allowed(request.user, self.cfg.perm("add"))
                                      and allowed(request.user, self.cfg.perm("change")))}
        template = "core/crud/_table.html" if self.is_htmx and request.headers.get("HX-Target") == "resource-table" else \
            self.cfg.template("list", "core/crud/list.html")
        return render(request, template, context)

    def post(self, request):                                      # bulk actions
        self.check("view")
        name = request.POST.get("action", "")
        ids = [i for i in request.POST.getlist("ids")][:500]
        if not ids:
            messages.warning(request, "Select at least one row.")
            return redirect(request.path)
        qs = scoped_queryset(self.cfg, self.ctx, action="change" if name != "delete" else "delete").filter(pk__in=ids)
        try:
            messages.success(request, run_bulk_action(self.cfg, name, qs, self.ctx))
        except CrudError as exc:
            messages.error(request, str(exc))
        except PermissionDenied:
            messages.error(request, "You are not allowed to do that to every selected row.")
        return redirect(safe_next(request, request.path))


class ResourceTrash(ResourceMixin, View):
    def get(self, request):
        if not self.cfg.is_soft:
            raise Http404
        self.check("change")
        context = {**self.base_context(), **list_context(self.cfg, self.ctx, request, mode="deleted"), "trash": True}
        return render(request, self.cfg.template("list", "core/crud/list.html"), context)


def record_content(cfg: ResourceConfig, obj, request) -> tuple[list, list]:
    """What a record's detail page shows, shared with its PDF / Excel export: ``details`` = ``[(label, value, file_url, is_image)]``
    and ``children`` = ``[{"inline", "headers", "items"}]`` (one per inline, up to 200 rows each)."""
    names = list(cfg.detail_fields or editable_field_names(cfg.model))
    names = [n for n in dict.fromkeys([*names, *cfg.readonly_fields]) if n != "company"]
    details = [(cfg.field_label(n), display_value(obj, n, cfg.date_formats), *_file_info(cfg, obj, n)) for n in names]
    extra = cfg.hook("detail_extra")
    if extra:
        details += list(extra(obj, request))
    children = []
    for inline in cfg.inlines:
        if inline.kind == "generic":
            rel = [f for f in cfg.model._meta.private_fields if getattr(f, "related_model", None) is inline.model]
            items = getattr(obj, rel[0].name).all() if rel else []
        else:
            fk = _inline_fk_name(inline, cfg.model)
            items = inline.model._default_manager.filter(**{fk: obj})
            if inline.position_field:
                items = items.order_by(inline.position_field, "pk")
        children.append({"inline": inline, "items": [[display_value(i, f, cfg.date_formats) for f in inline.fields] for i in list(items)[:200]],
                         "headers": [field_label(inline.model, f) for f in inline.fields]})
    return details, children


def _file_info(cfg: ResourceConfig, obj, name):
    """(download url | None, is_image)"""
    try:
        f = obj._meta.get_field(name)
    except Exception:
        return None, False
    if isinstance(f, models.FileField) and getattr(obj, name):
        return reverse("core:resource_file", args=[cfg.slug, obj.pk, name]), isinstance(f, models.ImageField)
    return None, False


def signature_boxes(obj) -> list[dict]:
    """The approval signature row under a record (Prepared By, then one box per step), or [] when it was never submitted or the
    request was withdrawn. Signature images are inlined as data URIs, so viewers need no right to open other users' files."""
    from core.approvals.signatures import record_signatures

    return [{"label": s.label, "name": s.name, "when": s.when, "image": _data_uri(s.image)} for s in record_signatures(obj)]


def _data_uri(data: bytes | None) -> str:
    import base64
    import io

    from PIL import Image

    if not data:
        return ""
    try:
        kind = Image.MIME.get(Image.open(io.BytesIO(data)).format or "", "")
    except Exception:  # noqa: BLE001 - not an image: show no picture
        kind = ""
    return f"data:{kind};base64,{base64.b64encode(data).decode()}" if kind else ""


class ResourceDetail(ResourceMixin, View):
    def get(self, request, pk):
        obj = self.obj_or_404(pk)
        self.check("view", obj)
        details, children = record_content(self.cfg, obj, request)
        from core.reports.resources.record import detail_export_formats

        context = {**self.base_context(obj=obj), "obj": obj, "details": details, "children": children,
                   "can_change": self.cfg.allows("change") and allowed(request.user, self.cfg.perm("change"), obj) and not self.locked(obj),
                   "can_delete": self.cfg.allows("delete") and allowed(request.user, self.cfg.perm("delete"), obj) and not self.locked(obj),
                   "trashed": isinstance(obj, SoftDeleteModel) and obj.is_deleted, "approval": _approval_panel(request.user, obj),
                   "signatures": signature_boxes(obj), "exports": detail_export_formats(self.cfg, obj)}
        if is_modal(request):
            context["panel_next"] = reverse("core:resource_list", args=[self.slug])
            return render(request, self.cfg.template("modal_detail", "core/crud/_modal_detail.html"), context)
        return render(request, self.cfg.template("detail", "core/crud/detail.html"), context)


def _inline_fk_name(inline, parent_model) -> str:
    if inline.fk_name:
        return inline.fk_name
    for f in inline.model._meta.concrete_fields:
        if f.is_relation and f.related_model is not None and issubclass(parent_model, f.related_model):
            return f.name
    raise Http404


def _approval_panel(user, obj):
    try:
        from core.approvals.registry import target_for

        cfg = target_for(obj)
    except approvals.ApprovalError:
        return None
    req = approvals.service.latest_request(obj)
    return {"target": cfg, "status": approvals.status_for(obj), "request": req, "can_submit": approvals.can_submit(user, obj),
            "can_cancel": approvals.can_cancel(user, obj), "resubmit": bool(req and req.status == "rejected"),
            "can_act": bool(req and approvals.can_act(user, req)), "cancelable_record": isinstance(obj, CancelableModel)}


class ResourceForm(ResourceMixin, View):
    creating = False

    def _prepare(self, request, pk=None):
        if self.creating:
            self.check("add")
            if self.cfg.company_lookup == "company":
                self.require_company()
            instance = self.cfg.model()
            if self.cfg.company_lookup == "company":
                instance.company_id = self.ctx.company_id
        else:
            instance = self.obj_or_404(pk, action="change")
            if self.locked(instance):
                raise PermissionDenied("This record is locked while it is waiting for or has received approval.")
        form_class = self.cfg.form_class(self.cfg, self.ctx) if self.cfg.form_class else build_form_class(self.cfg, self.ctx, generic_fks=self.cfg.generic_fks)
        return instance, form_class

    def _render(self, request, form, nodes, instance, status=200):
        self._image_previews(form, instance)
        context = {**self.base_context(), "form": form, "nodes": nodes, "obj": instance if instance.pk else None,
                   "creating": self.creating, "layout": self.cfg.layout, "multipart": form.is_multipart(),
                   "form_tabs": build_tabs(form, nodes, self.cfg)}
        if is_modal(request):
            context["action"] = request.path
            return render(request, self.cfg.template("modal_form", "core/crud/_modal_form.html"), context, status=status)
        return render(request, self.cfg.template("form", "core/crud/form.html"), context, status=status)

    def _image_previews(self, form, instance):
        """Stored images of the record show as a thumbnail above their upload input (private file view, never a public URL)."""
        if not instance.pk:
            return
        for name, field in form.fields.items():
            widget = field.widget
            if not isinstance(widget, PrivateFileInput) or getattr(widget, "preview_url", None):
                continue
            try:
                model_field = instance._meta.get_field(name)
            except Exception:
                continue
            if isinstance(model_field, models.ImageField) and getattr(instance, name):
                widget.preview_url = reverse("core:resource_file", args=[self.slug, instance.pk, name])

    def get(self, request, pk=None):
        instance, form_class = self._prepare(request, pk)
        return self._render(request, form_class(instance=instance), fs.build_tree(self.cfg, instance, self.ctx), instance)

    def post(self, request, pk=None):
        instance, form_class = self._prepare(request, pk)
        form = form_class(request.POST, request.FILES, instance=instance)
        nodes = fs.build_tree(self.cfg, instance, self.ctx, request.POST, request.FILES)

        def before(obj, ctx):
            if self.cfg.company_lookup == "company" and not obj.company_id:
                obj.company_id = ctx.company_id
            hook = self.cfg.hook("before_save")
            if hook:
                hook(obj, ctx)

        def after(obj, ctx):
            if self.cfg.company_lookup == "companies" and not obj.companies.exists():
                obj.companies.add(ctx.company_id)
            hook = self.cfg.hook("after_save")
            if hook:
                hook(obj, ctx)

        saved = fs.save_all(form, nodes, self.ctx, before_save=before, after_save=after)
        if saved is None:
            if not is_modal(request):
                messages.error(request, "Please correct the errors below.")
            return self._render(request, form, nodes, instance, status=400)
        if is_modal(request):
            return modal_done(f"{self.cfg.label} saved.")
        messages.success(request, f"{self.cfg.label} saved.")
        return redirect("core:resource_list", self.slug)


class ResourceDelete(ResourceMixin, View):
    def _confirm(self, request, obj, error="", status=200):
        context = {**self.base_context(), "obj": obj, "soft": self.cfg.is_soft, "error": error, "action": request.path}
        if is_modal(request):
            return render(request, self.cfg.template("modal_confirm", "core/crud/_modal_confirm.html"), context, status=status)
        return render(request, self.cfg.template("confirm", "core/crud/confirm_delete.html"), context, status=status)

    def get(self, request, pk):
        return self._confirm(request, self.obj_or_404(pk, action="delete"))

    def post(self, request, pk):
        obj = self.obj_or_404(pk, action="delete")
        error = ""
        if self.locked(obj):
            error = "This record is locked by an approval and cannot be deleted."
        else:
            try:
                delete_object(self.cfg, obj, self.ctx)
            except CrudError as exc:
                error = str(exc)
        if error:
            if is_modal(request):
                return self._confirm(request, obj, error, status=400)       # friendly message inside the modal
            messages.error(request, error)
            return redirect("core:resource_list", self.slug)
        text = f"{self.cfg.label} deleted." + (" It can be restored from the trash." if self.cfg.is_soft else "")
        if is_modal(request):
            return modal_done(text)
        messages.success(request, text)
        return redirect("core:resource_list", self.slug)


class ResourceRestore(ResourceMixin, View):
    http_method_names = ["post"]

    def post(self, request, pk):
        obj = self.obj_or_404(pk, mode="deleted", action="change")
        try:
            restore_object(self.cfg, obj, self.ctx)
        except CrudError as exc:
            messages.error(request, str(exc))
            return redirect("core:resource_trash", self.slug)
        messages.success(request, f"{self.cfg.label} restored.")
        return redirect("core:resource_trash", self.slug)


class InlineRow(ResourceMixin, View):
    """HTMX: one empty inline form row. ``path`` is a dotted chain of allowlisted inline slugs; the prefix is regex-validated."""

    def get(self, request, path):
        self.check("change" if request.GET.get("edit") else "add")
        prefix, index = request.GET.get("prefix", ""), request.GET.get("index", "")
        if not PREFIX_RE.match(prefix) or not index.isdigit() or int(index) > 500:
            raise Http404
        parent_of_inline, inline = resolve_inline(self.cfg, path)
        klass = fs.formset_class(parent_of_inline, inline, self.ctx, path)
        formset = klass(instance=parent_of_inline(), prefix=prefix)
        form = formset.empty_form
        html = render(request, "core/crud/_inline_row.html", {"form": form, "inline": inline, "prefix": prefix, "index": int(index),
                                                              "path": path, "slug": self.slug, "cfg": self.cfg}).content.decode()
        return HttpResponse(html.replace("__prefix__", index))


def resolve_inline(cfg, path: str):
    """(parent model, Inline) for a dotted chain of allowlisted inline slugs; anything unknown is a 404."""
    parent_model, level, inline, parent_of = cfg.model, cfg.inlines, None, None
    for slug in path.split("."):
        inline = next((i for i in level if i.slug == slug), None)
        if inline is None:
            raise Http404
        parent_of, parent_model, level = parent_model, inline.model, inline.nested
    return parent_of, inline


class ResourceFile(ResourceMixin, View):
    """Private file download: allowlisted FileField of a visible object; never a public URL."""

    def get(self, request, pk, field):
        obj = self.obj_or_404(pk)
        self.check("view", obj)
        try:
            f = obj._meta.get_field(field)
        except Exception:
            raise Http404 from None
        if not isinstance(f, models.FileField) or field not in (*self.cfg.list_fields, *self.cfg.detail_fields, *self.cfg.form_fields, *editable_field_names(self.cfg.model)):
            raise Http404
        value = getattr(obj, field)
        if not value:
            raise Http404
        return _file_response(value)


def _file_response(value):
    try:
        handle = value.storage.open(value.name, "rb")                 # storage normalises/blocks path traversal
    except (FileNotFoundError, ValueError, OSError):
        raise Http404 from None
    name = value.name.rsplit("/", 1)[-1]
    ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
    inline = ctype.startswith("image/") and ctype != "image/svg+xml"
    response = FileResponse(handle, content_type=ctype, as_attachment=not inline, filename=name)
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "private, no-store"
    return response


class CompanyLogo(CoreMixin, View):
    """A company's logo (private storage) for the top bar: any member of that company (or a superuser)."""

    def get(self, request, pk):
        from core.models import Company
        from core.permissions import user_company_ids

        if not request.user.is_superuser and pk not in user_company_ids(request.user):
            raise Http404
        company = Company.objects.filter(pk=pk).first()
        if company is None or not company.logo:
            raise Http404
        response = _file_response(company.logo)
        # The top bar shows it on every page: let the browser keep it (private cache only) so it does not blink on each
        # navigation. The URL carries ?v=<updated_at>, which changes whenever the company (and so a new logo) is saved.
        response["Cache-Control"] = "private, max-age=86400"
        return response


class UserFile(CoreMixin, View):
    """A user's photo / signature (private storage): the user themself, or anyone who may view that user in their company scope."""

    def get(self, request, pk, field):
        if field not in ("user_img", "user_sign"):
            raise Http404
        cfg = get_resource("users")
        if cfg is None:
            raise Http404
        if str(request.user.pk) == str(pk):
            from core.services.org import ensure_profile

            profile = ensure_profile(request.user)
        else:
            if not allowed(request.user, cfg.perm("view")):
                raise PermissionDenied
            profile = get_object_or_404(cfg, self.ctx, pk, action="view").profile
        value = getattr(profile, field)
        if not value:
            raise Http404
        return _file_response(value)
