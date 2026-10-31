"""Search registry: ``register_search`` turns allowlists (searchable/filterable/relations/sortable) into a django-filter FilterSet
per target. ``register_resource`` and ``register_report_source`` call it, so business apps normally never call it directly.

Same shape as the approval and report registries: a module-level dict, ``register_*`` called from ``AppConfig.ready()``,
``get_*`` that raises a clean error for unknown keys (the allowlist), and no imports of business code."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

from django.core.exceptions import ImproperlyConfigured
from django.db import models

from core.search.errors import SearchError

# ------------------------------------------------------------------------------------------------ operator sets (spec section 8)
OPERATORS: dict[str, tuple[str, ...]] = {
    "text": ("iexact", "icontains", "istartswith", "isnull"),
    "number": ("eq", "gt", "gte", "lt", "lte", "range", "isnull"),
    "date": ("eq", "gt", "gte", "lt", "lte", "year", "month", "range", "isnull"),
    "datetime": ("eq", "gt", "gte", "lt", "lte", "year", "month", "range", "isnull"),
    "fk": ("eq", "in", "isnull"),
    "bool": ("eq",),
    "m2m": ("in", "isnull"),
    "choice": ("eq", "in", "isnull"),
    "uuid": ("eq", "in", "isnull"),
}
OPERATOR_LABELS = {"iexact": "is", "icontains": "contains", "istartswith": "starts with", "isnull": "is empty", "eq": "equals",
                   "gt": ">", "gte": "≥", "lt": "<", "lte": "≤", "range": "between", "year": "year is", "month": "month is",
                   "in": "is any of"}
# operator -> Django lookup used by the generated FilterSet
LOOKUPS = {"iexact": "iexact", "icontains": "icontains", "istartswith": "istartswith", "isnull": "isnull", "eq": "exact",
           "gt": "gt", "gte": "gte", "lt": "lt", "lte": "lte", "year": "year", "month": "month", "in": "in"}


def kind_of(f) -> str:
    if f.is_relation:
        return "m2m" if (f.many_to_many or f.one_to_many) else "fk"
    if getattr(f, "choices", None):
        return "choice"
    if isinstance(f, (models.BooleanField,)):
        return "bool"
    if isinstance(f, models.DateTimeField):
        return "datetime"
    if isinstance(f, models.DateField):
        return "date"
    if isinstance(f, (models.IntegerField, models.DecimalField, models.FloatField)):
        return "number"
    if isinstance(f, models.UUIDField):
        return "uuid"
    if isinstance(f, (models.CharField, models.TextField)):
        return "text"
    raise ImproperlyConfigured(f"Field {f.name!r} ({type(f).__name__}) is not filterable; list only text/number/date/bool/choice/relation fields.")


def resolve_path(model: type[models.Model], path: str) -> tuple[models.Field, bool]:
    """Walk ``a__b__c`` through the model graph. Returns (final field, crosses_to_many). Raises ImproperlyConfigured when invalid."""
    current, many, parts = model, False, path.split("__")
    for index, part in enumerate(parts):
        try:
            f = current._meta.get_field(part)
        except Exception as exc:  # FieldDoesNotExist
            raise ImproperlyConfigured(f"{model._meta.label}: unknown field in path {path!r}: {part}") from exc
        last = index == len(parts) - 1
        if f.is_relation:
            if f.many_to_many or f.one_to_many:
                many = True
            if not last:
                current = f.related_model
        elif not last:
            raise ImproperlyConfigured(f"{model._meta.label}: {path!r} traverses non-relation {part!r}")
    return f, many


@dataclass(frozen=True)
class FilterableField:
    name: str                      # public key == ORM path (allowlisted; never user-composed)
    label: str
    kind: str
    operators: tuple[str, ...]
    many: bool = False
    choices: tuple = ()
    pk_kind: str = "int"           # for relations: "int" | "text"


@dataclass
class SearchSpec:
    target_type: str               # "resource" | "report_source"
    key: str
    model: type[models.Model]
    label: str
    searchable_fields: tuple[str, ...]
    searchable_relations: tuple[str, ...]
    filterable: dict[str, FilterableField]
    sortable_fields: tuple[str, ...]
    default_sort: tuple[str, ...]
    filterset_class: Any = None
    base_queryset: Callable | None = None
    quick_paths: tuple[str, ...] = field(default=())

    def queryset(self):
        return self.base_queryset() if self.base_queryset else self.model._default_manager.all()


_SPECS: dict[tuple[str, str], SearchSpec] = {}


def _field_entry(model, name: str, operators: Sequence[str] | None) -> FilterableField:
    f, many = resolve_path(model, name)
    kind = kind_of(f)
    allowed = OPERATORS[kind]
    chosen = tuple(operators) if operators else allowed
    bad = [o for o in chosen if o not in allowed]
    if bad:
        raise ImproperlyConfigured(f"{model._meta.label}.{name}: operators {bad} are not allowed for {kind} fields {allowed}")
    pk_kind = "int"
    if kind in ("fk", "m2m"):
        target = f.target_field if hasattr(f, "target_field") else f.related_model._meta.pk
        pk_kind = "int" if isinstance(target, (models.IntegerField,)) else "text"
    choices = tuple((str(v), str(l)) for v, l in (f.choices or ())) if kind == "choice" else ()
    return FilterableField(name, str(getattr(f, "verbose_name", name)).capitalize(), kind, chosen, many, choices, pk_kind)


def register_search(*, target_type: str, key: str, model, label: str = "", searchable_fields: Sequence[str] = (),
                    filterable_fields: Sequence[str] | dict[str, Sequence[str]] = (), searchable_relations: Sequence[str] = (),
                    sortable_fields: Sequence[str] = (), default_sort: Sequence[str] = (), base_queryset: Callable | None = None) -> SearchSpec:
    from core.search.filters import build_filterset

    if (target_type, key) in _SPECS:
        raise ImproperlyConfigured(f"Search target {target_type}:{key} is already registered.")
    items = filterable_fields.items() if isinstance(filterable_fields, dict) else ((n, None) for n in filterable_fields)
    filterable = {name: _field_entry(model, name, ops) for name, ops in items}
    for rel in searchable_relations:
        if "__" not in rel:
            raise ImproperlyConfigured(f"searchable_relations entry {rel!r} must be a related path like 'customer__name'.")
        filterable.setdefault(rel, _field_entry(model, rel, None))
    quick = []
    for path in (*searchable_fields, *searchable_relations):
        f, _ = resolve_path(model, path)
        if kind_of(f) != "text":
            raise ImproperlyConfigured(f"{model._meta.label}: quick-search path {path!r} must be a text field.")
        quick.append(path)
    sortable = tuple(sortable_fields)
    for path in sortable:
        resolve_path(model, path)
    for item in default_sort:
        if item.lstrip("-") not in sortable and item.lstrip("-") != "pk":
            raise ImproperlyConfigured(f"default_sort {item!r} must be in sortable_fields (or pk).")
    spec = SearchSpec(target_type, key, model, label or model._meta.verbose_name_plural.title(), tuple(searchable_fields),
                      tuple(searchable_relations), filterable, sortable, tuple(default_sort) or ("-pk",), None, base_queryset, tuple(quick))
    spec.filterset_class = build_filterset(spec)
    _SPECS[(target_type, key)] = spec
    return spec


def get_search(target_type: str, key: str) -> SearchSpec:
    try:
        return _SPECS[(target_type, key)]
    except KeyError:
        raise SearchError("Unknown search target.") from None


def all_specs() -> list[SearchSpec]:
    return list(_SPECS.values())


def unregister_search(target_type: str, key: str) -> None:
    _SPECS.pop((target_type, key), None)
