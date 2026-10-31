"""Query-builder model allowlist (same registry shape as resources, report sources and approvals).

A model is usable in the query builder only when it is registered here, either

* automatically: every CRUD resource (``register_resource``) whose rows are not narrowed by a ``get_queryset`` hook
  (``CORE_QUERY_AUTO_RESOURCES``, on by default); its root rows come from the resource's own scoped queryset, or
* explicitly: ``register_query_model(Model, ...)`` from a business app's ``AppConfig.ready()`` (e.g. a detail table that has no
  list page of its own, like ``wo_body``).

A registered model can be the *root* of a query (the FROM table) and can be *joined* from another registered model through its
ForeignKey / OneToOne / ManyToMany relations (both directions). Joined rows are limited to the active company and to rows that
are not soft-deleted, and the user needs the joined model's view permission. Fields with secret-looking names, files, binary and
JSON columns are never offered."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Sequence

from django.core.exceptions import ImproperlyConfigured
from django.db import models

from core.audit.redaction import is_sensitive_name


@dataclass
class QueryModel:
    model: type[models.Model]
    key: str                                   # "app_label.model_name"
    label: str
    permission: str | None                     # view permission ("" / None = any signed-in user is NOT allowed: see allowed_for)
    company_lookup: str | None
    exclude: frozenset = field(default_factory=frozenset)
    queryset: Callable | None = None           # (ctx) -> root queryset (already scoped); None = default manager + scope
    joinable: bool = True                      # may be reached through a relation from another model
    root: bool = True                          # may be chosen as the FROM model

    def field_allowed(self, f) -> bool:
        if f.name in self.exclude or is_sensitive_name(f.name):
            return False
        if isinstance(f, (models.FileField, models.BinaryField, models.JSONField)):
            return False
        return True


_MODELS: dict[str, QueryModel] = {}
_AUTO_DONE = False


def model_key(model) -> str:
    return model._meta.label_lower


def register_query_model(model, *, label: str = "", permission: str | None = "auto", company_lookup: str | None = "auto",
                         exclude_fields: Sequence[str] = (), queryset: Callable | None = None, joinable: bool = True,
                         root: bool = True) -> QueryModel:
    """Allow ``model`` in the query builder. ``permission`` defaults to the model's view permission; ``queryset(ctx)`` may narrow
    the root rows further (it must apply the company scope itself when given)."""
    meta = model._meta
    if meta.abstract:
        raise ImproperlyConfigured(f"{model.__name__} is abstract.")
    key = model_key(model)
    if key in _MODELS:
        raise ImproperlyConfigured(f"Query model {key!r} is already registered.")
    for name in exclude_fields:
        meta.get_field(name)
    if permission == "auto":
        permission = f"{meta.app_label}.view_{meta.model_name}"
    if not permission:
        raise ImproperlyConfigured(f"Query model {key!r} needs a view permission (fail closed).")
    entry = QueryModel(model=model, key=key, label=str(meta.verbose_name_plural).capitalize() if not label else label,
                       permission=permission,
                       company_lookup=getattr(model, "company_lookup", None) if company_lookup == "auto" else company_lookup,
                       exclude=frozenset(exclude_fields), queryset=queryset, joinable=joinable, root=root)
    _MODELS[key] = entry
    return entry


def unregister_query_model(key: str) -> None:
    _MODELS.pop(key, None)


def _ensure_auto() -> None:
    """Register every CRUD resource that shows plain company-scoped rows (lazy: after all apps registered their resources)."""
    global _AUTO_DONE
    if _AUTO_DONE:
        return
    _AUTO_DONE = True
    from core import conf

    if not conf.get("CORE_QUERY_AUTO_RESOURCES"):
        return
    from core.crud.access import scoped_queryset
    from core.crud.registry import all_resources

    for cfg in all_resources():
        key = model_key(cfg.model)
        perm = cfg.perm("view")
        # owner-only resources (notifications, jobs) and cross-company views (audit) depend on rules a join cannot repeat
        if key in _MODELS or cfg.hook("get_queryset") or cfg.superuser_unscoped or not perm or not cfg.allow_export:
            continue
        register_query_model(cfg.model, label=cfg.label_plural, permission=perm, company_lookup=cfg.company_lookup,
                             queryset=lambda ctx, cfg=cfg: scoped_queryset(cfg, ctx, action="view"))


def reset_auto_models() -> None:
    """Tests: rebuild the automatic entries on next use."""
    global _AUTO_DONE
    _AUTO_DONE = False


def get_query_model(key: str) -> QueryModel | None:
    _ensure_auto()
    return _MODELS.get(key)


def query_model_for(model) -> QueryModel | None:
    _ensure_auto()
    return _MODELS.get(model_key(model))


def all_query_models() -> list[QueryModel]:
    _ensure_auto()
    return sorted(_MODELS.values(), key=lambda m: m.label.lower())


def allowed_for(user, entry: QueryModel) -> bool:
    from core.crud.access import allowed

    return bool(entry.permission) and allowed(user, entry.permission)
