"""Compile a validated :class:`ReportSpec` into ORM calls (``values``/``annotate``/``filter``/``order_by``). No raw SQL, ever."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from django.db.models import DecimalField, ExpressionWrapper, F, FloatField, Value
from django.db.models.functions import NullIf
from django.utils import timezone

from core import conf
from core.audit.service import record_event
from core.crud.access import Ctx
from core.permissions import filter_by_perm
from core.permissions.access import restrict
from core.permissions.scope import scope_queryset
from core.reports.registry import AGGREGATES, ReportSource
from core.search.builder.state import SearchState, quick_q
from core.search.builder.tree import compile_tree
from core.search.registry import get_search

from .definition import ReportError, ReportSpec, date_range


@dataclass
class ReportResult:
    columns: list[tuple[str, str]]          # (alias, label)
    queryset: object                        # values() queryset, ordered, scoped; slice it to bound it
    fill: object = None                     # rows -> None: fills the Company column of a many-company source (see fill_companies)

    def fill_companies(self, rows: list) -> list:
        if self.fill is not None and rows:
            self.fill(rows)
        return rows


def scoped_base(source: ReportSource, ctx: Ctx):
    qs = source.base_queryset(ctx)
    qs = scope_queryset(qs, source.company_lookup, ctx.company_ids)
    qs = restrict(qs, ctx.user)
    for perm in source.permission:
        qs = filter_by_perm(ctx.user, perm, qs)
    return qs


def _values(qs, cols, names, extra=None):
    """``values()`` with plain fields as positional names (an alias equal to a model field would clash) and the rest as expressions."""
    args = [n for n in names if cols[n].orm() == n and "__" not in n]
    kwargs = {n: F(cols[n].orm()) for n in names if n not in args}
    return qs.values(*args, **kwargs, **(extra or {}))


def _company_column(source: ReportSource, ctx: Ctx, grouped: bool):
    """``(values extra, fill)`` for the Company column (more than one company in scope): a single-valued company path is a plain
    column (and a grouping key when grouped); a many-valued one is filled in per row from ``row_pk`` (not offered when grouped)."""
    from core.reports import scope

    if not scope.wants_company_column(ctx, source.company_lookup):
        return None, None
    lookup = source.company_lookup
    if scope.single_valued(source.model, lookup):
        return {scope.COMPANY_ALIAS: F(scope.abbreviation_path(lookup))}, None
    if grouped:
        return None, None

    def fill(rows):
        found = scope.row_companies(source.model, lookup, [r["row_pk"] for r in rows if "row_pk" in r], ctx)
        for r in rows:
            r[scope.COMPANY_ALIAS] = found.get(r.get("row_pk"), "")

    return {}, fill


def _calc_expr(op: str, left, right):
    def operand(x):
        return F(x) if isinstance(x, str) else Value(x)

    a, b = operand(left), operand(right)
    if op == "add":
        expr = a + b
    elif op == "sub":
        expr = a - b
    elif op == "mul":
        expr = a * b
    else:
        expr = a / NullIf(b, Value(0))                      # divide by zero -> NULL, never an error
    return ExpressionWrapper(expr, output_field=FloatField())


def build_queryset(source: ReportSource, spec: ReportSpec, ctx: Ctx, extra_state: SearchState | None = None) -> ReportResult:
    qs = scoped_base(source, ctx)
    search = get_search("report_source", source.key)
    if spec.filters.get("items"):
        qs = qs.filter(compile_tree(search, spec.filters))
    quick = (extra_state.quick if extra_state else "") or spec.quick
    if quick:
        qs = qs.filter(quick_q(search, quick))
    if extra_state and extra_state.tree.get("items"):
        qs = qs.filter(compile_tree(search, extra_state.tree))
    if source.date_field:
        start, end = date_range(spec.parameters, timezone.localdate())       # presets ("today" ...) are resolved on the day it runs
        if start:
            qs = qs.filter(**{f"{source.date_field}__gte": start.isoformat()})
        if end:                                              # inclusive end date -> exclusive next day
            qs = qs.filter(**{f"{source.date_field}__lt": (end + dt.timedelta(days=1)).isoformat()})
    cols = source.columns
    company, fill = _company_column(source, ctx, spec.grouped)
    if spec.grouped:
        qs = _values(qs.order_by(), cols, spec.groupings, company)
        aggs = {}
        for a in spec.aggregates:
            path = cols[a["column"]].orm() if a["column"] else "pk"
            aggs[a["alias"]] = AGGREGATES[a["fn"]](path)
        qs = qs.annotate(**aggs)
        default_order = list(spec.groupings)                  # deterministic even when grouped
    else:
        qs = _values(qs, cols, spec.columns, company)
        if fill is not None or any(cols[n].is_image for n in spec.columns):
            qs = qs.annotate(row_pk=F("pk"))                  # image cells need the row id to build their (authorised) URL
        default_order = []
    for c in spec.calculated:
        qs = qs.annotate(**{c["alias"]: _calc_expr(c["op"], c["left"], c["right"])})
    order = list(spec.sorting) + [o for o in default_order if o not in [s.lstrip("-") for s in spec.sorting]]
    if not spec.grouped:
        order.append("pk")
    qs = qs.order_by(*order)
    from core.reports import scope

    labels = [(scope.COMPANY_ALIAS, scope.COMPANY_LABEL)] if company is not None else []
    for alias in spec.output_aliases():
        if alias in cols:
            labels.append((alias, cols[alias].label))
        else:
            labels.append((alias, alias.replace("_", " ").capitalize()))
    return ReportResult(labels, qs, fill)


def run_preview(source: ReportSource, spec: ReportSpec, ctx: Ctx, state: SearchState | None = None, page: int = 1) -> dict:
    """Bounded, paginated preview. Audited as a report run."""
    result = build_queryset(source, spec, ctx, state)
    size = min(spec.page_size or conf.get("CORE_DEFAULT_PAGE_SIZE"), conf.get("CORE_PREVIEW_ROWS"))
    from django.core.paginator import Paginator

    paginator = Paginator(result.queryset, size)
    page_obj = paginator.get_page(page)
    from core.reports.scope import audit_note, company_names

    record_event("report_run", message=f"preview {source.key} · {audit_note(ctx)}", model_label=source.model._meta.label,
                 object_repr=source.label, company=ctx.company_id)
    rows = result.fill_companies(list(page_obj.object_list))
    images = image_aliases(source, result.columns)
    if images:
        from django.urls import NoReverseMatch, reverse

        for row in rows:
            for alias in images:
                route = source.columns[alias].image_route
                try:
                    row[alias] = reverse(route[0], args=[row["row_pk"], route[1]]) if row.get(alias) and "row_pk" in row else ""
                except NoReverseMatch:
                    row[alias] = ""
    return {"columns": result.columns, "page": page_obj, "rows": rows, "image_aliases": images, "chart": chart_data(source, spec, result),
            "company_names": company_names(ctx) if result.columns and result.columns[0][0] == "core_company" else {}}


def chart_data(source: ReportSource, spec: ReportSpec, result: ReportResult) -> dict | None:
    """Chart.js data from the first ``CORE_CHART_MAX_POINTS`` rows of the whole result (not just the preview page)."""
    if not spec.chart:
        return None
    from core.reports.query.service import _numeric, display

    rows = list(result.queryset[: conf.get("CORE_CHART_MAX_POINTS")])
    x = spec.chart["x"]
    column = source.columns.get(x)
    choices = {}
    if column is not None and column.kind == "choice":
        from core.search.registry import resolve_path

        try:
            choices = {str(k): str(v) for k, v in resolve_path(source.model, column.orm())[0].flatchoices}
        except Exception:  # noqa: BLE001 - labels fall back to raw values
            choices = {}
    labels = dict(result.columns)
    return {"type": spec.chart["type"], "stacked": spec.chart.get("stacked", False),
            "labels": [display(r.get(x), choices) or "(empty)" for r in rows],
            "datasets": [{"label": labels.get(y, y), "data": [_numeric(r.get(y)) for r in rows]} for y in spec.chart["y"]]}


def image_aliases(source: ReportSource, columns) -> list[str]:
    """Aliases of the output columns that are images."""
    return [a for a, _ in columns if a in source.columns and source.columns[a].is_image]
