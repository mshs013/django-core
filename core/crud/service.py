"""CRUD domain logic shared by views, bulk actions and tests: delete/restore with reference rules, value display."""
from __future__ import annotations

import datetime as dt

from django.contrib.contenttypes.fields import GenericRelation
from django.core.exceptions import PermissionDenied
from django.db import models, transaction
from django.utils import timezone
from django.utils.html import format_html

from core.formatting import format_value
from core.permissions import user_has_perm

from .access import Ctx, allowed


class CrudError(Exception):
    """A user-safe refusal (message can be shown as-is)."""


def _active_references(obj) -> list[str]:
    """Alive rows that PROTECT/RESTRICT this object -- they forbid deleting it."""
    blockers = []
    for rel in obj._meta.related_objects:
        if rel.on_delete not in (models.PROTECT, models.RESTRICT):
            continue
        manager = rel.related_model._default_manager
        if manager.filter(**{rel.field.name: obj}).exists():
            blockers.append(str(rel.related_model._meta.verbose_name_plural))
    return blockers


def _cascade_children(obj):
    from core.models.base import SoftDeleteModel

    for rel in obj._meta.related_objects:
        if rel.on_delete is models.CASCADE and issubclass(rel.related_model, SoftDeleteModel) and not rel.many_to_many:
            yield from rel.related_model._default_manager.filter(**{rel.field.name: obj})


def delete_object(cfg, obj, ctx: Ctx) -> None:
    """Soft-delete (``SoftDeleteModel``) with cascade to soft-delete children, or hard delete otherwise. Refuses when alive rows
    PROTECT/RESTRICT the object. Atomic."""
    if not cfg.allows("delete") or not allowed(ctx.user, cfg.perm("delete"), obj):
        raise PermissionDenied
    guard = cfg.hook("delete_guard")
    if guard:
        guard(obj, ctx)                                  # may raise CrudError with a tailored, friendly message
    blockers = _active_references(obj)
    if blockers:
        raise CrudError("This record is still used by: " + ", ".join(sorted(set(blockers))) + ".")
    with transaction.atomic():
        hook = cfg.hook("before_delete")
        if hook:
            hook(obj, ctx)
        if cfg.is_soft:
            when = timezone.now()
            stack = [obj]
            while stack:
                node = stack.pop()
                stack.extend(_cascade_children(node))
                node._soft_delete(ctx.user, at=when)
        else:
            try:
                obj.delete()
            except (models.ProtectedError, models.RestrictedError):
                raise CrudError("This record is still referenced and cannot be deleted.") from None
        hook = cfg.hook("after_delete")
        if hook:
            hook(obj, ctx)


def restore_object(cfg, obj, ctx: Ctx) -> None:
    if not cfg.is_soft:
        raise CrudError("This resource does not support restore.")
    if not cfg.allows("change") or not allowed(ctx.user, cfg.perm("change"), obj):
        raise PermissionDenied
    with transaction.atomic():
        when = obj.deleted_at
        children = [c for c in _deleted_cascade_children(obj, when)]
        obj.restore()
        for child in children:
            child.restore()


def _deleted_cascade_children(obj, when):
    from core.models.base import SoftDeleteModel

    for rel in obj._meta.related_objects:
        if rel.on_delete is models.CASCADE and issubclass(rel.related_model, SoftDeleteModel) and not rel.many_to_many:
            yield from rel.related_model.deleted_objects.filter(**{rel.field.name: obj}, deleted_at=when)


def run_bulk_action(cfg, name: str, queryset, ctx: Ctx):
    """Bulk actions are an allowlist declared in the registry (plus the built-in 'delete'). ``queryset`` is already scoped."""
    if name == "delete":
        done = 0
        for obj in queryset:
            delete_object(cfg, obj, ctx)
            done += 1
        return f"{done} deleted."
    action = cfg.actions.get(name)
    if action is None:
        raise CrudError("Unknown action.")
    return action(queryset, ctx) or "Done."


# ------------------------------------------------------------------------------------------------------------- display helpers
def field_label(model, name: str) -> str:
    if name == "__str__":
        return str(model._meta.verbose_name).capitalize()
    try:
        return str(model._meta.get_field(name).verbose_name).capitalize()
    except Exception:
        if "__" in name and not hasattr(model, name):
            from core.crud.display import label_for

            return label_for(model, name)
        attr = getattr(model, name, None)
        label = getattr(attr, "short_description", None) or getattr(getattr(attr, "fget", None), "short_description", None)
        return str(label or name.replace("_", " ")).capitalize()


def display_value(obj, name: str, date_formats=None):
    """Value for tables/detail: related objects via ``__str__``, choices via display, files as names, bytes as size, dates and times
    in the site format (``date_formats`` overrides it: one format string or ``{"date"|"datetime"|"time": ...}``)."""
    if name == "__str__":
        return str(obj)
    if "__" in name and not hasattr(obj, name):                     # related path, e.g. profile__dept__name
        *hops, last = name.split("__")
        for hop in hops:
            obj = getattr(obj, hop, None)
            if obj is None:
                return None
        return display_value(obj, last, date_formats)
    try:
        f = obj._meta.get_field(name)
    except Exception:
        value = getattr(obj, name, None)
        value = value() if callable(value) else value
        return format_value(value, date_formats) if isinstance(value, (dt.datetime, dt.date, dt.time)) else value
    if isinstance(f, GenericRelation):
        return ", ".join(str(o) for o in getattr(obj, name).all()[:10])
    if f.many_to_many or f.one_to_many:
        return ", ".join(str(o) for o in getattr(obj, name).all()[:10])
    if getattr(f, "choices", None):
        return getattr(obj, f"get_{name}_display")()
    value = getattr(obj, name, None)
    if isinstance(f, models.BinaryField):
        return f"{len(value or b'')} bytes"
    if isinstance(f, models.FileField):
        return value.name.rsplit("/", 1)[-1] if value else ""
    if isinstance(f, models.JSONField):
        import json

        return json.dumps(value, ensure_ascii=False)[:200] if value not in (None, {}, []) else ""
    if isinstance(value, bool):
        return format_html('<span class="badge bg-{}-lt">{}</span>', "green" if value else "secondary", "Yes" if value else "No")
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return format_value(value, date_formats)
    return value
