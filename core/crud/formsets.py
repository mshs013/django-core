"""Multi-form service: parent ModelForm + N (nested) inline formsets saved in ONE ``transaction.atomic()``.

* Any invalid parent/child/grandchild form rejects the whole save; nothing is written.
* Each formset is saved inside its own *savepoint* so a database error (IntegrityError, ProtectedError) is attributed to that
  formset and the outer transaction rolls back completely (partial progress is never meaningful for a document + its lines).
* Rows keep their DOM order in ``position_field`` (0..n-1) after every save.
* Child deletes are soft for ``SoftDeleteModel`` children, hard only when the inline opts in (``hard_delete=True``).
* Child form relations are company scoped like the parent's.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from django import forms
from django.contrib.contenttypes.forms import BaseGenericInlineFormSet, generic_inlineformset_factory
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError, RestrictedError
from django.forms import BaseInlineFormSet, inlineformset_factory
from django.forms.utils import ErrorList

from core.audit.service import audited_update
from core.permissions import user_has_perm

from .forms import formfield_for, m2m_widgets_for, scope_form, style_form


class SaveFailed(Exception):
    def __init__(self, message: str, node=None):
        super().__init__(message)
        self.message, self.node = message, node


@dataclass
class Node:
    inline: object
    formset: object
    path: str                                  # dotted inline slugs, e.g. "lines.options"
    children_by_form: dict = field(default_factory=dict)      # form prefix -> [Node]


def _child_perm(inline, action: str) -> str:
    meta = inline.model._meta
    return f"{inline.permission_prefix or meta.app_label}.{action}_{meta.model_name}"


def _mixin(inline, ctx, path):
    class ScopedInlineMixin:
        _inline, _ctx, _path = inline, ctx, path

        def add_fields(self, form, index):
            super().add_fields(form, index)
            scope_form(form, self._ctx.company_ids, self._ctx.user)
            if self._inline.position_field and self._inline.position_field in form.fields:
                field = form.fields[self._inline.position_field]
                field.widget = forms.HiddenInput()
                field.required = False
                if index is None or index >= self.initial_form_count():
                    # the page numbers every row, so a blank new row must not count as "filled in" just because of its position
                    field.has_changed = lambda initial, data: False
            style_form(form)

        def _construct_form(self, i, **kwargs):
            form = super()._construct_form(i, **kwargs)
            if self.is_bound and i >= self.initial_form_count():
                head = f"{form.prefix}-"
                if not any(k.startswith(head) for k in self.data) and not any(k.startswith(head) for k in self.files or ()):
                    form.has_changed = lambda: False             # a row removed on the page leaves an empty slot: skip it
                    form._errors = None                          # style_form() already validated it once; drop that result
            form.nested = build_nested(self._inline, form, self._ctx, self._path, self.data if self.is_bound else None,
                                       self.files if self.is_bound else None)
            return form

        @property
        def empty_form(self):
            form = super().empty_form
            form.nested = build_nested(self._inline, form, self._ctx, self._path, None, None)
            return form

    return ScopedInlineMixin


def formset_class(parent_model, inline, ctx, path: str):
    mixin = _mixin(inline, ctx, path)
    common = dict(fields=list(inline.fields), extra=inline.extra, min_num=inline.min_num, can_delete=inline.deletable,
                  widgets={**m2m_widgets_for(inline.model, inline.fields, inline.m2m_widgets), **inline.widgets} or None, formfield_callback=lambda f, **kw: formfield_for(f, **kw))
    if inline.kind == "generic":
        base = type("GenericBase", (mixin, BaseGenericInlineFormSet), {})
        return generic_inlineformset_factory(inline.model, ct_field=inline.ct_field, fk_field=inline.fk_field, formset=base, **common)
    base = type("InlineBase", (mixin, BaseInlineFormSet), {})
    return inlineformset_factory(parent_model, inline.model, fk_name=inline.fk_name, formset=base, **common)


def build_nested(inline, form, ctx, path, data, files):
    """Formsets of ``inline.nested`` for one child ``form`` (prefix derived from the form's prefix)."""
    nodes = []
    for child in inline.nested:
        child_path = f"{path}.{child.slug}"
        klass = formset_class(inline.model, child, ctx, child_path)
        fs = klass(data=data, files=files, instance=form.instance, prefix=f"{form.prefix}-{child.slug}")
        nodes.append(Node(child, fs, child_path))
    return nodes


def build_tree(cfg, instance, ctx, data=None, files=None) -> list[Node]:
    nodes = []
    for inline in cfg.inlines:
        klass = formset_class(cfg.model, inline, ctx, inline.slug)
        nodes.append(Node(inline, klass(data=data, files=files, instance=instance, prefix=inline.slug), inline.slug))
    return nodes


def _live(form) -> bool:
    """A valid child form that will be written (not marked for deletion, and either existing or filled in)."""
    if form.cleaned_data.get("DELETE"):
        return False
    return form.instance.pk is not None or form.has_changed()


def tree_is_valid(nodes: list[Node]) -> bool:
    ok = True
    for node in nodes:
        if not node.formset.is_valid():
            ok = False
        for form in node.formset.forms:
            if form.is_valid() and _live(form) and getattr(form, "nested", None):
                if not tree_is_valid(form.nested):
                    ok = False
    return ok


def _delete_child(obj, inline, ctx) -> None:
    if not user_has_perm(ctx.user, _child_perm(inline, "delete"), obj):
        raise SaveFailed(f"You may not remove {inline.label.lower()}.")
    try:
        if inline.is_soft:
            obj.delete()
        elif inline.hard_delete:
            obj.delete()
        else:
            raise SaveFailed(f"{inline.label} cannot be removed.")
    except (ProtectedError, RestrictedError):
        raise SaveFailed(f"A {inline.model._meta.verbose_name} is still in use and cannot be removed.") from None


def _order_key(form, i: int, pos: str):
    value = form.cleaned_data.get(pos) if hasattr(form, "cleaned_data") else None
    return (value if value is not None else 10_000 + i, i)


def _renumber(node: Node) -> None:
    """Positions 0..n in the order the rows were shown (the submitted position; rows without one keep their form order)."""
    pos = node.inline.position_field
    if not pos:
        return
    deleted = getattr(node.formset, "deleted_forms", [])
    kept = [(i, f) for i, f in enumerate(node.formset.forms) if f.instance.pk is not None and f not in deleted]
    kept.sort(key=lambda item: _order_key(item[1], item[0], pos))
    for index, (_i, form) in enumerate(kept):
        if getattr(form.instance, pos, None) != index:
            audited_update(node.inline.model._base_manager.filter(pk=form.instance.pk), message="reordered", **{pos: index})
        setattr(form.instance, pos, index)


def save_tree(nodes: list[Node], ctx) -> None:
    for node in nodes:
        fs, inline = node.formset, node.inline
        try:
            with transaction.atomic():                      # savepoint
                for obj in fs.save(commit=False):
                    if obj.pk is None and not user_has_perm(ctx.user, _child_perm(inline, "add"), None):
                        raise SaveFailed(f"You may not add {inline.label.lower()}.")
                    if obj.pk is not None and not user_has_perm(ctx.user, _child_perm(inline, "change"), obj):
                        raise SaveFailed(f"You may not change {inline.label.lower()}.")
                    if inline.position_field and getattr(obj, inline.position_field) is None:
                        setattr(obj, inline.position_field, 10_000)    # temporary; _renumber sets the real order below
                    obj.save()
                for obj in fs.deleted_objects:
                    _delete_child(obj, inline, ctx)
                fs.save_m2m()
                _renumber(node)
        except SaveFailed as exc:
            exc.node = node
            raise
        except (IntegrityError, ProtectedError, RestrictedError) as exc:
            raise SaveFailed(f"{inline.label}: the data conflicts with existing records ({exc.__class__.__name__}).", node) from exc
        for form in fs.forms:
            if form.instance.pk is not None and getattr(form, "nested", None) and form not in getattr(fs, "deleted_forms", []):
                for child in form.nested:
                    child.formset.instance = form.instance
                save_tree(form.nested, ctx)


def save_all(form, nodes: list[Node], ctx, *, before_save=None, after_save=None):
    """Validate everything, then write parent + children atomically. Returns the saved parent or None (errors are on the forms)."""
    if not (form.is_valid() & tree_is_valid(nodes)):
        return None
    try:
        with transaction.atomic():
            if before_save:
                before_save(form.instance, ctx)
            obj = form.save()
            for node in nodes:
                node.formset.instance = obj
            save_tree(nodes, ctx)
            if after_save:
                after_save(obj, ctx)
    except SaveFailed as exc:
        if exc.node is not None:
            exc.node.formset._non_form_errors = ErrorList([exc.message], error_class="nonform")
        else:
            form.add_error(None, exc.message)
        return None
    return obj
