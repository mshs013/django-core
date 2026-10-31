"""Filter tree validation and compilation (no raw SQL).

Tree format (one nesting level -- documented limit)::

    {"op": "AND", "items": [
        {"field": "name", "op": "icontains", "value": "shirt"},
        {"op": "OR", "items": [{"field": "price", "op": "range", "value": ["10", "20"]},
                               {"field": "status", "op": "in", "value": ["a", "b"], "negate": true}]}]}

Every leaf runs through the spec's django-filter ``FilterSet`` bound to ONE filter; the result is combined as
``Q(pk__in=<leaf queryset>)`` so related (to-many) filters can never duplicate rows and ``distinct()`` is never needed.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from django.core.exceptions import ValidationError
from django.db.models import Q

from core import conf
from core.search.errors import SearchError
from core.search.filters import filter_name
from core.search.registry import SearchSpec

MAX_TREE_BYTES = 16000
NULL_ALIASES = {"is_null": True, "not_null": False}


def _text(value: Any) -> str:
    text = "" if value is None else str(value).strip()
    if len(text) > conf.get("CORE_MAX_SEARCH_VALUE_LENGTH"):
        raise SearchError("A filter value is too long.")
    if "\x00" in text:
        raise SearchError("Invalid character in a filter value.")
    return text


def _validate_leaf(spec: SearchSpec, leaf: dict) -> dict:
    if not isinstance(leaf, dict):
        raise SearchError("Invalid filter row.")
    name, op = leaf.get("field"), leaf.get("op")
    if op in NULL_ALIASES:                         # "is_null"/"not_null" (older trees, API callers) == isnull true/false
        leaf = {**leaf, "op": "isnull", "value": NULL_ALIASES[op]}
        op = "isnull"
    entry = spec.filterable.get(name) if isinstance(name, str) else None
    if entry is None:
        raise SearchError(f"Unknown filter field: {str(name)[:60]!r}.")
    if op not in entry.operators:
        raise SearchError(f"Operator {str(op)[:30]!r} is not allowed for {entry.label}.")
    value = leaf.get("value")
    if op == "range":
        if not isinstance(value, (list, tuple)) or len(value) != 2 or not any(str(v or "").strip() for v in value):
            raise SearchError("A range needs a lower and/or an upper value.")
        value = [_text(value[0]), _text(value[1])]
    elif op == "in":
        values = value if isinstance(value, (list, tuple)) else [value]
        value = [_text(v) for v in values if _text(v) != ""][:100]
        if not value:
            raise SearchError("Choose at least one value.")
    elif op == "isnull" or (entry.kind == "bool" and op == "eq"):
        value = _text(value).lower() in ("1", "true", "yes", "on", "") if op == "isnull" else _text(value).lower() in ("1", "true", "yes", "on")
    else:
        value = _text(value)
        if value == "":
            raise SearchError(f"Enter a value for {entry.label}.")
    return {"field": name, "op": op, "value": value, "negate": bool(leaf.get("negate"))}


def validate_tree(spec: SearchSpec, tree: Any) -> dict:
    """Return a normalised copy of ``tree`` or raise SearchError. Empty/None -> empty AND group."""
    if not tree:
        return {"op": "AND", "items": []}
    if not isinstance(tree, dict):
        raise SearchError("Invalid filter definition.")
    count = [0]

    def group(node: dict, depth: int) -> dict:
        op = str(node.get("op", "AND")).upper()
        if op not in ("AND", "OR"):
            raise SearchError("A group must combine rows with AND or OR.")
        items = node.get("items", [])
        if not isinstance(items, list):
            raise SearchError("Invalid filter definition.")
        out = []
        for item in items:
            if isinstance(item, dict) and "items" in item:
                if depth >= 1:
                    raise SearchError("Groups can be nested one level only.")
                out.append(group(item, depth + 1))
            else:
                count[0] += 1
                if count[0] > conf.get("CORE_MAX_SEARCH_ROWS"):
                    raise SearchError("Too many filter rows.")
                out.append(_validate_leaf(spec, item))
        return {"op": op, "items": [i for i in out if not ("items" in i and not i["items"])]}

    return group(tree, 0)


def _day_bounds(text: str) -> tuple[str, str]:
    try:
        day = dt.date.fromisoformat(text[:10])
    except ValueError:
        raise SearchError("Enter the date as YYYY-MM-DD.") from None
    return day.isoformat() + " 00:00:00", (day + dt.timedelta(days=1)).isoformat() + " 00:00:00"


def _run(spec: SearchSpec, name: str, op: str, data_value: Any) -> Q:
    key = filter_name(name, op)
    if isinstance(data_value, bool):
        data_value = "true" if data_value else "false"
    elif isinstance(data_value, list):
        data_value = ",".join(data_value)
    filterset = spec.filterset_class(data={key: data_value}, queryset=spec.model._default_manager.all())
    try:
        if not filterset.is_valid():
            raise SearchError("Invalid value for " + spec.filterable[name].label + ".")
        subquery = filterset.qs.values("pk")
        str(subquery.query)                       # compile now: invalid values surface here, not later in the view
    except (ValueError, TypeError, ValidationError, OverflowError) as exc:
        raise SearchError("Invalid value for " + spec.filterable[name].label + ".") from exc
    return Q(pk__in=subquery)


def compile_leaf(spec: SearchSpec, leaf: dict) -> Q:
    name, op, value = leaf["field"], leaf["op"], leaf["value"]
    entry = spec.filterable[name]
    if op == "range":
        parts = []
        if str(value[0]).strip():
            parts.append(_run(spec, name, "gte", value[0]))
        if str(value[1]).strip():
            parts.append(_run(spec, name, "lte", value[1]))
        q = parts[0] if len(parts) == 1 else parts[0] & parts[1]
    elif op == "eq" and entry.kind == "datetime":
        lo, hi = _day_bounds(value)
        q = _run(spec, name, "gte", lo) & _run(spec, name, "lt", hi)
    else:
        q = _run(spec, name, op, value)
    return ~q if leaf.get("negate") else q


def compile_tree(spec: SearchSpec, tree: dict) -> Q:
    def group(node: dict) -> Q:
        combined: Q | None = None
        for item in node["items"]:
            q = group(item) if "items" in item else compile_leaf(spec, item)
            combined = q if combined is None else (combined & q if node["op"] == "AND" else combined | q)
        return combined if combined is not None else Q()

    return group(tree)
