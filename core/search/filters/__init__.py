"""django-filter integration: one dynamic ``FilterSet`` per search spec, with one filter per allowlisted (field, operator).

Filter names are ``<field path>::<operator>`` built only from the registry, never from user input. A builder leaf is executed by
binding the FilterSet to exactly one filter, so values go through django-filter's form fields (type validation, parsing) and
Django's lookups (which escape ``%`` and ``_`` for LIKE-based lookups).
"""
from __future__ import annotations

import django_filters as df

from core.search.registry import LOOKUPS


class CharInFilter(df.BaseInFilter, df.CharFilter):
    pass


class NumberInFilter(df.BaseInFilter, df.NumberFilter):
    pass


def filter_name(path: str, operator: str) -> str:
    return f"{path}::{operator}"


def _filter_for(entry, operator: str):
    lookup = LOOKUPS[operator]
    path = entry.name
    if operator == "isnull":
        return df.BooleanFilter(field_name=path, lookup_expr="isnull")
    if operator in ("year", "month"):
        return df.NumberFilter(field_name=path, lookup_expr=lookup)
    if operator == "in":
        cls = NumberInFilter if entry.pk_kind == "int" and entry.kind in ("fk", "m2m") else CharInFilter
        return cls(field_name=path, lookup_expr="in")
    kind = entry.kind
    if kind == "text":
        return df.CharFilter(field_name=path, lookup_expr=lookup)
    if kind == "number":
        return df.NumberFilter(field_name=path, lookup_expr=lookup)
    if kind == "date":
        return df.DateFilter(field_name=path, lookup_expr=lookup)
    if kind == "datetime":
        return df.DateTimeFilter(field_name=path, lookup_expr=lookup)
    if kind == "bool":
        return df.BooleanFilter(field_name=path, lookup_expr="exact")
    if kind == "fk" and entry.pk_kind == "int":
        return df.NumberFilter(field_name=path, lookup_expr="exact")
    return df.CharFilter(field_name=path, lookup_expr="exact")


def build_filterset(spec):
    attrs = {}
    for entry in spec.filterable.values():
        for operator in entry.operators:
            if operator == "range":          # a range is two executions: gte + lte (see builder.compile)
                continue
            attrs[filter_name(entry.name, operator)] = _filter_for(entry, operator)
    meta = type("Meta", (), {"model": spec.model, "fields": []})
    attrs["Meta"] = meta
    return type(f"{spec.model.__name__}SearchFilterSet", (df.FilterSet,), attrs)
