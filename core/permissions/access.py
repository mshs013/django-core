"""Row-level access on top of model permissions, applied everywhere data is read (lists, details, exports, search, reports,
the query builder) and to relation dropdowns in forms.

Object access -- "this user works only for buyer H&M"::

    register_access_scope(Buyer)                                  # the buyer list itself
    register_access_scope(Buyer, target=Order, path="buyer")      # orders follow their buyer
    register_access_scope(Buyer, target=OrderLine, path="order__buyer")

A user then sees only the scoped rows assigned to them on the user form's *Object access* tab ("All buyers" lifts the limit).
**No assignment means no rows** for a scoped model. Superusers are never limited.

User access -- "this user also sees the records of Ann and Bob": register the resource with ``owner_field="created_by"``; a user
sees their own records plus those of the users on their *User access* tab ("Everyone" lifts the limit)."""
from __future__ import annotations

from dataclasses import dataclass

from django.core.exceptions import FieldDoesNotExist, ImproperlyConfigured
from django.db.models import Q

from core.permissions import cache


@dataclass(frozen=True)
class AccessScope:
    source: type            # the model whose rows are assigned to users (e.g. Buyer)
    target: type            # the model limited by it (Buyer itself, Order, ...)
    path: str               # ORM path from target to the source's primary key ("pk" when target is source)


_SCOPES: dict[type, list[AccessScope]] = {}
_SOURCES: list[type] = []


def _check_path(target, path: str, source) -> None:
    model = target
    for part in path.split("__"):
        try:
            field = model._meta.get_field(part)
        except FieldDoesNotExist:
            raise ImproperlyConfigured(f"{target._meta.label}: access path {path!r} -- no field {part!r} on {model._meta.label}") from None
        if not field.is_relation:
            raise ImproperlyConfigured(f"{target._meta.label}: access path {path!r} must follow relations to {source._meta.label}")
        model = field.related_model
    if model is not source:
        raise ImproperlyConfigured(f"{target._meta.label}: access path {path!r} leads to {model._meta.label}, not {source._meta.label}")


def register_access_scope(source, *, target=None, path: str = "") -> AccessScope:
    target = target or source
    if target is source:
        path = "pk"
    elif not path:
        raise ImproperlyConfigured("register_access_scope: give the path from the target model to the source, e.g. path='buyer'")
    else:
        _check_path(target, path, source)
    scope = AccessScope(source, target, path)
    scopes = _SCOPES.setdefault(target, [])
    if scope not in scopes:
        scopes.append(scope)
    if source not in _SOURCES:
        _SOURCES.append(source)
    return scope


def unregister_access_scope(scope: AccessScope) -> None:
    """For tests and plugins being unloaded."""
    scopes = _SCOPES.get(scope.target, [])
    if scope in scopes:
        scopes.remove(scope)
    if not any(s.source is scope.source for group in _SCOPES.values() for s in group) and scope.source in _SOURCES:
        _SOURCES.remove(scope.source)


def access_sources() -> list[type]:
    return list(_SOURCES)


def scopes_for(model) -> list[AccessScope]:
    if model._meta.proxy:
        model = model._meta.concrete_model
    return list(_SCOPES.get(model, ()))


def owner_field_for(model) -> str | None:
    from core.crud.registry import resource_for_model

    cfg = resource_for_model(model)
    return getattr(cfg, "owner_field", None) if cfg else None


def _unlimited(user) -> bool:
    return bool(getattr(user, "is_superuser", False))


def allowed_ids(user, source) -> list | None:
    """Primary keys of ``source`` rows ``user`` may work with, or None for "all"."""
    if _unlimited(user):
        return None
    from django.contrib.contenttypes.models import ContentType

    from core.models import ObjectAccess

    ct = ContentType.objects.get_for_model(source, for_concrete_model=True)
    cached = cache.get("objaccess", getattr(user, "pk", None), ct.pk)
    if cached is not None:
        return None if cached == "all" else cached
    rows = list(ObjectAccess.objects.filter(user_id=getattr(user, "pk", None), content_type=ct).values_list("object_id", flat=True))
    value = "all" if None in rows else sorted(rows)
    cache.put(value, "objaccess", getattr(user, "pk", None), ct.pk)
    return None if value == "all" else value


def visible_owner_ids(user) -> list | None:
    """Users whose records ``user`` sees on owner-scoped models (always including themselves), or None for everyone's."""
    if _unlimited(user):
        return None
    from core.models import UserAccess

    cached = cache.get("useraccess", getattr(user, "pk", None))
    if cached is not None:
        return None if cached == "all" else cached
    rows = list(UserAccess.objects.filter(user_id=getattr(user, "pk", None)).values_list("target_id", flat=True))
    value = "all" if None in rows else sorted({getattr(user, "pk", None), *rows})
    cache.put(value, "useraccess", getattr(user, "pk", None))
    return None if value == "all" else value


def access_q(model, user, prefix: str = "") -> Q | None:
    """The row-level condition for ``model`` (reached through ``prefix`` from a query's root), or None when nothing limits it."""
    if _unlimited(user):
        return None
    lead = f"{prefix}__" if prefix else ""
    q = Q()
    limited = False
    for scope in scopes_for(model):
        ids = allowed_ids(user, scope.source)
        if ids is None:
            continue
        q &= Q(**{f"{lead}{scope.path}__in": ids})
        limited = True
    owner = owner_field_for(model)
    if owner:
        owners = visible_owner_ids(user)
        if owners is not None:
            q &= Q(**{f"{lead}{owner}__in": owners})
            limited = True
    return q if limited else None


def restrict(qs, user):
    """``qs`` limited to the rows ``user`` may see under object access and user access."""
    q = access_q(qs.model, user)
    return qs.filter(q) if q is not None else qs
