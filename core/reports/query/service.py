"""Run a validated query: bounded preview, chart data, file export. Every run is audited as a report run."""
from __future__ import annotations

import datetime as dt
import decimal

from django.core.paginator import Paginator

from core import conf
from core.audit.service import record_event
from core.formatting import format_value

from .catalogue import QueryError
from .engine import QueryResult, QuerySpec, build


def display(value, choices: dict | None = None) -> str:
    """One cell as text: choice labels, dates in the site format, floats without noise."""
    if value is None:
        return ""
    if choices and str(value) in choices:
        return choices[str(value)]
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, float):
        return f"{value:,.4f}".rstrip("0").rstrip(".") if value % 1 else f"{value:,.0f}"
    if isinstance(value, (dt.date, dt.time, decimal.Decimal)):
        return format_value(value)
    return str(value)


def _numeric(value):
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float, decimal.Decimal)):
        return float(value)
    try:
        return float(str(value))
    except ValueError:
        return None


class _Page:
    """Paginator page over a sliced query (the count runs once, rows are fetched for this page only)."""

    def __init__(self, result: QueryResult, number, size: int):
        self.paginator = Paginator(range(result.count()), size)
        page = self.paginator.get_page(number)
        self.number, self.start_index, self.end_index = page.number, page.start_index, page.end_index
        self.has_next, self.has_previous = page.has_next, page.has_previous
        self.next_page_number, self.previous_page_number = page.next_page_number, page.previous_page_number
        start = (page.number - 1) * size
        self.object_list = result.rows(start, start + size) if self.paginator.count else []


def preview(spec: QuerySpec, ctx, page=1) -> dict:
    result = build(spec, ctx)
    page_obj = _Page(result, page, conf.get("CORE_PREVIEW_ROWS"))
    from core.reports.scope import COMPANY_ALIAS, audit_note, company_names

    record_event("report_run", message=f"query preview {spec.root.key} · {audit_note(ctx)}", model_label=spec.root.model._meta.label,
                 object_repr=spec.root.label, company=ctx.company_id)
    choices = {c.alias: c.choices for c in spec.columns}
    numeric = {c.alias for c in spec.columns if c.kind == "number"}
    rows = [[(display(row.get(a), choices.get(a)), a in numeric) for a, _ in result.columns] for row in page_obj.object_list]
    with_company = bool(result.columns) and result.columns[0][0] == COMPANY_ALIAS
    return {"columns": result.columns, "page": page_obj, "rows": rows, "chart": chart_data(spec, result), "numeric": numeric,
            "company_names": company_names(ctx) if with_company else {}}


def chart_data(spec: QuerySpec, result: QueryResult) -> dict | None:
    """``{"type", "labels", "datasets": [{"label", "data"}], "stacked"}`` from the first ``CORE_CHART_MAX_POINTS`` rows."""
    chart = spec.chart
    if not chart:
        return None
    rows = result.rows(0, conf.get("CORE_CHART_MAX_POINTS"))
    labels_by_alias = dict(result.columns)
    x_choices = next((c.choices for c in spec.columns if c.alias == chart["x"]), {})
    return {"type": chart["type"], "stacked": chart.get("stacked", False),
            "labels": [display(r.get(chart["x"]), x_choices) or "(empty)" for r in rows],
            "datasets": [{"label": labels_by_alias[y], "data": [_numeric(r.get(y)) for r in rows]} for y in chart["y"]]}


def export_bytes(spec: QuerySpec, ctx, ext: str, title: str = "") -> tuple[bytes, str, str]:
    """(data, file name, content type). Bounded by ``CORE_EXPORT_MAX_ROWS``."""
    from core.reports.resources.export import pdf_meta
    from core.reports.resources.formats import available_formats, get_format
    from core.reports.resources.pdf import render_pdf
    from core.reports.resources.resources import as_row, rows_resource

    if ext != "pdf" and ext not in available_formats():
        raise QueryError("Unsupported export format.")
    result = build(spec, ctx)
    total = result.count()
    limit = conf.get("CORE_PDF_MAX_ROWS") if ext == "pdf" else conf.get("CORE_EXPORT_MAX_ROWS")
    if total > limit:
        raise QueryError(f"This export has {total} rows; the limit is {limit}. Narrow the filters or set a row limit.")
    rows = result.rows()
    choices = {c.alias: c.choices for c in spec.columns}
    title = title or spec.root.label
    name = f"{spec.root.model._meta.model_name}-query.{ext}"
    if ext == "pdf":
        data = render_pdf(title, [label for _, label in result.columns],
                          [[display(r.get(a), choices.get(a)) for a, _ in result.columns] for r in rows],
                          pdf_meta(ctx, subtitle=f"{total} rows"))
        content_type = "application/pdf"
    else:
        def cell(alias, value):
            if choices.get(alias) and value is not None:
                return choices[alias].get(str(value), value)
            if isinstance(value, dt.datetime) and value.tzinfo is not None:
                return format_value(value)                       # spreadsheets cannot hold time-zone aware values
            return value

        fmt = get_format(ext)
        out = fmt.export_data(rows_resource(result.columns).export(queryset=[as_row({a: cell(a, r.get(a)) for a, _ in result.columns})
                                                                             for r in rows]))
        data = out.encode("utf-8") if isinstance(out, str) else out
        if ext == "csv":
            data = "﻿".encode("utf-8") + data
        content_type = fmt.get_content_type()
    from core.reports.scope import audit_note

    record_event("report_run", message=f"query export {spec.root.key}.{ext} · {audit_note(ctx)}", model_label=spec.root.model._meta.label,
                 object_repr=title[:200], company=ctx.company_id, user=ctx.user)
    return data, name, content_type
