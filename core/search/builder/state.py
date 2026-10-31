"""Search state: quick text + filter tree + sort + paging, parsed from (and serialised to) a query string so URLs are bookmarkable
and exports reuse exactly what the list shows."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from urllib.parse import urlencode

from django.core.paginator import Paginator
from django.db.models import Q, QuerySet

from core import conf
from core.search.errors import SearchError
from core.search.registry import SearchSpec, resolve_path

from .tree import MAX_TREE_BYTES, compile_tree, validate_tree


@dataclass
class SearchState:
    quick: str = ""
    tree: dict = field(default_factory=lambda: {"op": "AND", "items": []})
    sort: list[str] = field(default_factory=list)
    page_size: int = 0
    page: int = 1
    saved_id: int | None = None
    warnings: list[str] = field(default_factory=list)

    def to_params(self) -> dict:
        params = {}
        if self.quick:
            params["q"] = self.quick
        if self.tree.get("items"):
            params["adv"] = json.dumps(self.tree, separators=(",", ":"))
        if self.sort:
            params["sort"] = ",".join(self.sort)
        if self.page_size:
            params["page_size"] = self.page_size
        if self.saved_id:
            params["saved"] = self.saved_id
        return params

    def querystring(self, **extra) -> str:
        return urlencode({**self.to_params(), **extra})


def clamp_page_size(value, default: int | None = None) -> int:
    maximum = conf.get("CORE_MAX_PAGE_SIZE")
    try:
        size = int(value)
    except (TypeError, ValueError):
        size = default or conf.get("CORE_DEFAULT_PAGE_SIZE")
    return max(1, min(size, maximum))


def clean_sort(spec: SearchSpec, items) -> list[str]:
    out = []
    for item in list(items)[:3]:
        item = str(item).strip()
        name = item.lstrip("-")
        if name == "pk" or name in spec.sortable_fields:
            out.append(item)
        else:
            raise SearchError(f"Cannot sort by {name[:40]!r}.")
    return out


def parse_state(spec: SearchSpec, params, *, strict: bool = False) -> SearchState:
    """Build a state from a QueryDict/dict. Invalid parts are dropped with a warning (``strict=True`` raises instead)."""
    state = SearchState()
    get = params.get
    quick = (get("q") or "").strip()
    state.quick = quick[: conf.get("CORE_MAX_SEARCH_VALUE_LENGTH")]
    raw = get("adv") or ""
    if raw:
        try:
            if len(raw) > MAX_TREE_BYTES:
                raise SearchError("The advanced filter is too large.")
            state.tree = validate_tree(spec, json.loads(raw))
        except (json.JSONDecodeError, SearchError) as exc:
            if strict:
                raise SearchError(str(exc) if isinstance(exc, SearchError) else "Invalid advanced filter.") from exc
            state.warnings.append("The advanced filter was ignored: " + (str(exc) if isinstance(exc, SearchError) else "invalid format."))
    sort_raw = get("sort") or ""
    if sort_raw:
        try:
            state.sort = clean_sort(spec, sort_raw.split(","))
        except SearchError as exc:
            if strict:
                raise
            state.warnings.append(str(exc))
    state.page_size = clamp_page_size(get("page_size"), None) if get("page_size") else 0
    try:
        state.page = max(1, int(get("page") or 1))
    except (TypeError, ValueError):
        state.page = 1
    try:
        state.saved_id = int(get("saved")) if get("saved") else None
    except (TypeError, ValueError):
        state.saved_id = None
    return state


# Models whose rows are known by a dotted "app.code" name: such a token is matched as (prefix path, rest path) as well,
# so "core.add_user" finds the permission whose app label is "core" and codename contains "add_user".
DOTTED_QUICK = {"auth.permission": ("content_type__app_label", "codename")}


def quick_q(spec: SearchSpec, text: str) -> Q:
    """Every whitespace token must match at least one searchable path (icontains: case-insensitive, ``%``/``_`` literal)."""
    tokens = [t for t in text.split() if t][:8]
    if not tokens or not spec.quick_paths:
        return Q()
    combined = Q()
    many = False
    for token in tokens:
        any_path = Q()
        for path in spec.quick_paths:
            any_path |= Q(**{f"{path}__icontains": token})
            many = many or resolve_path(spec.model, path)[1]
        dotted = DOTTED_QUICK.get(spec.model._meta.label_lower)
        if dotted and "." in token:
            prefix, rest = token.split(".", 1)
            any_path |= Q(**{f"{dotted[0]}__iexact": prefix, f"{dotted[1]}__icontains": rest})
        combined &= any_path
    if many:
        return Q(pk__in=spec.model._default_manager.filter(combined).values("pk"))
    return combined


def ordering_for(spec: SearchSpec, sort: list[str]) -> list[str]:
    order = list(sort) or list(spec.default_sort)
    if not any(o.lstrip("-") == "pk" for o in order):
        order.append("pk")                           # stable tie-breaker -> deterministic pagination
    return order


def apply_state(spec: SearchSpec, queryset: QuerySet, state: SearchState) -> QuerySet:
    """Apply quick text, filter tree and ordering to an ALREADY scoped/permission-filtered queryset."""
    qs = queryset
    if state.quick:
        qs = qs.filter(quick_q(spec, state.quick))
    if state.tree.get("items"):
        qs = qs.filter(compile_tree(spec, state.tree))
    return qs.order_by(*ordering_for(spec, state.sort))


def paginate(queryset: QuerySet, state: SearchState, default_size: int | None = None):
    size = state.page_size or clamp_page_size(None, default_size)
    return Paginator(queryset, size).get_page(state.page)
