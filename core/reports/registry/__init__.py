"""Report source registry (same shape as the approval and search registries).

``register_report_source`` is called by business apps from ``AppConfig.ready()``. Everything a user can pick in the builder --
columns, groupings, aggregates, sort, filters -- comes from this allowlist; nothing in a saved definition is trusted.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

from django.core.exceptions import ImproperlyConfigured
from django.db import models
from django.db.models import Avg, Count, Max, Min, Sum

from core.search.registry import kind_of, register_search, resolve_path, unregister_search

ALIAS_RE = re.compile(r"^[a-z][a-z0-9_]{0,40}$")
AGGREGATES = {"count": lambda p: Count(p), "count_distinct": lambda p: Count(p, distinct=True), "sum": Sum, "avg": Avg, "min": Min, "max": Max}
NUMERIC_ONLY = {"sum", "avg"}


@dataclass(frozen=True)
class ReportColumn:
    name: str                       # public alias (lower snake case)
    label: str = ""
    path: str = ""                  # ORM path; default == name. May name an annotation of the source.
    kind: str = "text"              # text|number|date|datetime|bool|choice|fk|uuid
    groupable: bool = False
    aggregates: tuple[str, ...] = ()        # allowed aggregate functions on this column
    image_route: tuple[str, ...] = ()       # ("url_name", "field"): the cell is an image served by reverse(url_name, args=[row pk, field])

    @property
    def is_image(self) -> bool:
        return bool(self.image_route)

    def orm(self) -> str:
        return self.path or self.name


@dataclass
class ReportSource:
    key: str
    model: type[models.Model]
    label: str
    description: str
    columns: dict[str, ReportColumn]
    queryset: Callable | None
    annotations: dict[str, Any]
    permission: tuple[str, ...]
    default_columns: tuple[str, ...]
    default_sort: tuple[str, ...]
    date_field: str | None
    pdf_template: Callable | None
    hooks: dict
    company_lookup: str | None

    def base_queryset(self, ctx):
        qs = self.queryset(ctx) if self.queryset else self.model._default_manager.all()
        if self.annotations:
            qs = qs.annotate(**self.annotations)
        return qs


_SOURCES: dict[str, ReportSource] = {}


def _infer_column(model, name: str, annotations) -> ReportColumn:
    if name in annotations:
        raise ImproperlyConfigured(f"Annotation column {name!r} needs an explicit ReportColumn(kind=...)")
    f, many = resolve_path(model, name.replace("__", "__"))
    if many:
        raise ImproperlyConfigured(f"{model._meta.label}: column {name!r} crosses a to-many relation; expose it as an aggregate source")
    kind = kind_of(f)
    numeric = kind == "number"
    fns = ("count", "count_distinct", "min", "max") + (("sum", "avg") if numeric else ())
    return ReportColumn(name.replace("__", "_"), str(getattr(f, "verbose_name", name)).capitalize(), name, kind,
                        groupable=kind in ("text", "date", "bool", "choice", "fk", "datetime") , aggregates=fns)


def register_report_source(key: str, model, *, label: str = "", description: str = "", columns: Sequence[ReportColumn | str] = (),
                           queryset: Callable | None = None, annotations: dict | None = None, searchable_fields: Sequence[str] = (),
                           filterable_fields: Any = (), searchable_relations: Sequence[str] = (), sortable_fields: Sequence[str] = (),
                           default_sort: Sequence[str] = (), permission: Sequence[str] | str | None = None, default_columns: Sequence[str] = (),
                           date_field: str | None = None, pdf_template: Callable | None = None, hooks: dict | None = None,
                           company_lookup: str | None = "auto") -> ReportSource:
    if key in _SOURCES:
        raise ImproperlyConfigured(f"Report source {key!r} is already registered.")
    if not ALIAS_RE.match(key.replace("-", "_").lower()):
        raise ImproperlyConfigured(f"Report source key {key!r} must be lower-case letters, digits, '-' or '_'.")
    annotations = annotations or {}
    cols: dict[str, ReportColumn] = {}
    for item in columns:
        col = item if isinstance(item, ReportColumn) else _infer_column(model, item, annotations)
        if not ALIAS_RE.match(col.name):
            raise ImproperlyConfigured(f"Column alias {col.name!r} must match {ALIAS_RE.pattern}")
        if col.name in cols:
            raise ImproperlyConfigured(f"Duplicate column {col.name!r}")
        if not col.path or col.path not in annotations:
            resolve_path(model, col.orm()) if col.orm() not in annotations else None
        for fn in col.aggregates:
            if fn not in AGGREGATES:
                raise ImproperlyConfigured(f"Unknown aggregate {fn!r} on column {col.name!r}")
        if col.image_route and (len(col.image_route) != 2 or col.groupable or col.aggregates):
            raise ImproperlyConfigured(f"Image column {col.name!r}: image_route is (url_name, field); an image cannot be grouped or aggregated")
        cols[col.name] = col if col.label else ReportColumn(col.name, col.name.replace("_", " ").capitalize(), col.path, col.kind,
                                                              col.groupable, col.aggregates, col.image_route)
    if not cols:
        raise ImproperlyConfigured("A report source needs at least one column.")
    meta = model._meta
    if permission is None:                              # default: the model's own view permission; () = any signed-in user
        perms = (f"{meta.app_label}.view_{meta.model_name}",)
    else:
        perms = (permission,) if isinstance(permission, str) else tuple(permission)
    if date_field:
        resolve_path(model, date_field)
    searchable_fields = tuple(searchable_fields)
    src = ReportSource(key, model, label or str(meta.verbose_name_plural).title(), description, cols, queryset, annotations, perms,
                       tuple(default_columns) or tuple(cols)[:6], tuple(default_sort), date_field, pdf_template, hooks or {},
                       getattr(model, "company_lookup", None) if company_lookup == "auto" else company_lookup)
    register_search(target_type="report_source", key=key, model=model, label=src.label, searchable_fields=searchable_fields,
                    filterable_fields=filterable_fields, searchable_relations=searchable_relations,
                    sortable_fields=tuple(sortable_fields) or tuple(c.orm() for c in cols.values() if c.orm() not in annotations),
                    default_sort=tuple(default_sort) or ("-pk",), base_queryset=None)
    _SOURCES[key] = src
    return src


def get_report_source(key: str) -> ReportSource | None:
    ensure_resource_sources()
    return _SOURCES.get(key)


def all_report_sources() -> list[ReportSource]:
    ensure_resource_sources()
    return sorted(_SOURCES.values(), key=lambda s: s.label.lower())


def report_sources_for_model(model) -> list[ReportSource]:
    return [s for s in all_report_sources() if s.model is model]


# ------------------------------------------------------------------------------------------- automatic sources for resources
AUTO_PREFIX = "r-"
_AUTO_DONE = False
_SKIP_COLUMN_NAMES = {"password", "is_deleted", "deleted_at"}


def _auto_columns(cfg) -> list[str]:
    """The resource's list fields first, then its other plain concrete fields; to-many paths, files and secrets are left out.
    Forward FKs become ``<fk>__<label field>`` when the related model has a ``name``/``title``/``code`` field."""
    meta = cfg.model._meta
    names = [n for n in cfg.list_fields if n != "__str__"] + [f.name for f in meta.concrete_fields]
    out: list[str] = []
    for name in names:
        try:
            f = meta.get_field(name)
        except Exception:
            continue                                         # a property / callable in list_fields
        if f.primary_key or name in _SKIP_COLUMN_NAMES or isinstance(f, (models.FileField, models.JSONField, models.BinaryField)):
            continue
        if f.is_relation:
            if not (f.many_to_one or f.one_to_one) or not f.concrete:
                continue
            label = next((n for n in ("name", "title", "code") if any(x.name == n for x in f.related_model._meta.concrete_fields)), None)
            if label is None:
                continue
            path = f"{name}__{label}"
        else:
            try:
                kind_of(f)
            except ImproperlyConfigured:
                continue
            path = name
        if path not in out:
            out.append(path)
    return out[:30]


def ensure_resource_sources() -> None:
    """Give every registered CRUD resource without a report source of its own an automatic one (``r-<slug>``), so reports can
    be built and run from any list page. Its rows come from the resource's own scoped queryset (company scope, get_queryset hook,
    view permission), so a report can never show more than the list page does. Off with ``CORE_AUTO_REPORT_SOURCES = False``.
    Runs lazily on first use, after every app has registered its resources."""
    global _AUTO_DONE
    if _AUTO_DONE:
        return
    from core import conf

    _AUTO_DONE = True
    if not conf.get("CORE_AUTO_REPORT_SOURCES"):
        return
    from core.crud.access import scoped_queryset
    from core.crud.registry import all_resources

    covered = {s.model for s in _SOURCES.values()}
    for cfg in all_resources():
        key = f"{AUTO_PREFIX}{cfg.slug}"
        if cfg.model in covered or key in _SOURCES or not cfg.allow_export:
            continue
        columns = _auto_columns(cfg)
        if not columns:
            continue
        sortable = tuple(c for c in columns if "__" not in c)
        try:
            register_report_source(
                key, cfg.model, label=cfg.label_plural, columns=columns,
                queryset=lambda ctx, cfg=cfg: scoped_queryset(cfg, ctx, action="view"), company_lookup=None,
                permission=(cfg.perm("view"),) if cfg.perm("view") else (), searchable_fields=cfg.searchable_fields,
                filterable_fields=cfg.filterable_fields, searchable_relations=cfg.searchable_relations, sortable_fields=sortable,
                default_sort=tuple(o for o in cfg.default_sort if o.lstrip("-") in sortable),
                default_columns=tuple(c.replace("__", "_") for c in columns if c.split("__")[0] in cfg.list_fields)[:6])
        except ImproperlyConfigured:
            import logging

            logging.getLogger("core.reports").warning("no automatic report source for resource %s", cfg.slug, exc_info=True)


def reset_resource_sources() -> None:
    """Tests: drop the automatic sources so the next lookup rebuilds them."""
    global _AUTO_DONE
    for key in [k for k in _SOURCES if k.startswith(AUTO_PREFIX)]:
        unregister_report_source(key)
    _AUTO_DONE = False


def unregister_report_source(key: str) -> None:
    _SOURCES.pop(key, None)
    unregister_search("report_source", key)
