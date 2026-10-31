"""Validate a report definition (a plain dict / ReportDefinition row) against its source allowlist. Pure of side effects."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from core.reports.registry import AGGREGATES, ALIAS_RE, NUMERIC_ONLY, ReportSource
from core.search.errors import SearchError

MAX_COLUMNS = 30
MAX_AGGREGATES = 10
MAX_CALCULATED = 5
CALC_OPS = ("add", "sub", "mul", "div")
CHART_TYPES = ("bar", "line", "area", "pie", "doughnut")
# run filter on the source's date_field: "" = date_from / date_to as given; "day" = the single day date_from; the rest are worked out
# on the day the report runs (a saved "Daily receive" with "today" always shows today's rows)
DATE_PRESETS = ("", "day", "today", "yesterday", "this_week", "this_month", "last_month")


class ReportError(SearchError):
    pass


@dataclass
class ReportSpec:
    columns: list[str] = field(default_factory=list)
    groupings: list[str] = field(default_factory=list)
    aggregates: list[dict] = field(default_factory=list)       # {"fn","column","alias"}
    calculated: list[dict] = field(default_factory=list)       # {"alias","op","left","right"}
    sorting: list[str] = field(default_factory=list)
    filters: dict = field(default_factory=dict)
    quick: str = ""
    parameters: dict = field(default_factory=dict)
    page_size: int = 0
    chart: dict = field(default_factory=dict)                  # {"type","x","y":[aliases],"stacked"}

    @property
    def grouped(self) -> bool:
        return bool(self.groupings or self.aggregates)

    def output_aliases(self) -> list[str]:
        base = self.groupings + [a["alias"] for a in self.aggregates] if self.grouped else self.columns
        return base + [c["alias"] for c in self.calculated]


def _alias(value, what: str, source: ReportSource | None = None) -> str:
    text = str(value or "").strip().lower()
    if source is not None and text in {f.name for f in source.model._meta.get_fields()} | set(source.annotations):
        raise ReportError(f"{text!r} clashes with a field of the data source; choose another name.")
    if not ALIAS_RE.match(text):
        raise ReportError(f"Invalid {what} name {text[:30]!r}: use lower-case letters, digits and underscores.")
    return text


def _date(value, what: str) -> str:
    if not value:
        return ""
    try:
        return dt.date.fromisoformat(str(value)[:10]).isoformat()
    except ValueError:
        raise ReportError(f"{what} must be a date (YYYY-MM-DD).") from None


def validate_definition(source: ReportSource, raw: dict) -> ReportSpec:
    from core.search.builder.state import clamp_page_size, clean_sort
    from core.search.builder.tree import validate_tree
    from core.search.registry import get_search

    raw = raw or {}
    spec = ReportSpec()
    cols = source.columns
    spec.columns = list(dict.fromkeys(raw.get("columns") or source.default_columns))[:MAX_COLUMNS]
    for name in spec.columns:
        if name not in cols:
            raise ReportError(f"Unknown column: {str(name)[:40]!r}.")
    spec.groupings = list(dict.fromkeys(raw.get("groupings") or []))
    for name in spec.groupings:
        if name not in cols or not cols[name].groupable:
            raise ReportError(f"Cannot group by {str(name)[:40]!r}.")
    aliases = set(spec.groupings)
    for item in (raw.get("aggregates") or [])[:MAX_AGGREGATES]:
        if not isinstance(item, dict):
            raise ReportError("Invalid aggregate.")
        fn, column = item.get("fn"), item.get("column") or ""
        if fn not in AGGREGATES:
            raise ReportError(f"Unknown aggregate function: {str(fn)[:20]!r}.")
        if column not in cols and not (fn == "count" and not column):
            raise ReportError(f"Unknown aggregate column: {str(column)[:40]!r}.")
        if column:
            if fn not in cols[column].aggregates:
                raise ReportError(f"{fn} is not allowed on {cols[column].label}.")
        alias = _alias(item.get("alias") or f"{fn}_{column or 'rows'}", "aggregate", source)
        if alias in aliases or alias in cols:
            raise ReportError(f"Duplicate or reserved name: {alias!r}.")
        aliases.add(alias)
        spec.aggregates.append({"fn": fn, "column": column, "alias": alias})
    if spec.groupings and not spec.aggregates:
        spec.aggregates.append({"fn": "count", "column": "", "alias": "rows"})
        aliases.add("rows")
    available = set(spec.groupings) | {a["alias"] for a in spec.aggregates} if spec.grouped else set(spec.columns)
    for item in (raw.get("calculated") or [])[:MAX_CALCULATED]:
        if not isinstance(item, dict) or item.get("op") not in CALC_OPS:
            raise ReportError("Invalid calculated column.")
        alias = _alias(item.get("alias"), "calculated column", source)
        if alias in available or alias in cols:
            raise ReportError(f"Duplicate or reserved name: {alias!r}.")
        operands = []
        for side in ("left", "right"):
            operand = item.get(side)
            if isinstance(operand, (int, float)) and not isinstance(operand, bool):
                operands.append(operand)
            elif isinstance(operand, str) and operand in available:
                operands.append(operand)
            else:
                raise ReportError("Calculated columns may only use columns shown in the report, or numbers.")
        spec.calculated.append({"alias": alias, "op": item["op"], "left": operands[0], "right": operands[1]})
        available.add(alias)
    sort = []
    for item in (raw.get("sorting") or [])[:3]:
        name = str(item).lstrip("-")
        if name not in available:
            raise ReportError(f"Cannot sort by {name[:40]!r}.")
        sort.append(str(item))
    spec.sorting = sort
    search = get_search("report_source", source.key)
    spec.filters = validate_tree(search, raw.get("filters"))
    spec.quick = str(raw.get("quick") or "")[:200]
    params = raw.get("parameters") if isinstance(raw.get("parameters"), dict) else {}
    spec.parameters = {"date_from": _date(params.get("date_from"), "From date"), "date_to": _date(params.get("date_to"), "To date")}
    preset = str(params.get("date_preset") or "")
    if preset not in DATE_PRESETS:
        raise ReportError("Unknown date choice.")
    if preset:
        spec.parameters["date_preset"] = preset
    if preset == "day" and not spec.parameters["date_from"]:
        raise ReportError("Choose the day.")
    if not preset and spec.parameters["date_from"] and spec.parameters["date_to"] and spec.parameters["date_from"] > spec.parameters["date_to"]:
        raise ReportError("The From date must not be after the To date.")
    if (spec.parameters["date_from"] or spec.parameters["date_to"] or preset) and not source.date_field:
        raise ReportError("This report has no date to filter on.")
    spec.page_size = clamp_page_size(raw.get("page_size") or None)
    spec.chart = _chart(source, spec, raw.get("chart"))
    return spec


def date_range(parameters: dict, today: dt.date | None = None) -> tuple[dt.date | None, dt.date | None]:
    """Inclusive ``(from, to)`` dates of a validated ``parameters`` dict on the day the report runs (None = open end)."""
    today = today or dt.date.today()
    preset = parameters.get("date_preset") or ""
    if preset == "today":
        return today, today
    if preset == "yesterday":
        day = today - dt.timedelta(days=1)
        return day, day
    if preset == "this_week":
        return today - dt.timedelta(days=today.weekday()), today
    if preset == "this_month":
        return today.replace(day=1), today
    if preset == "last_month":
        end = today.replace(day=1) - dt.timedelta(days=1)
        return end.replace(day=1), end
    start = dt.date.fromisoformat(parameters["date_from"]) if parameters.get("date_from") else None
    if preset == "day":
        return start, start
    return start, dt.date.fromisoformat(parameters["date_to"]) if parameters.get("date_to") else None


def _chart(source: ReportSource, spec: ReportSpec, raw) -> dict:
    """Chart settings: labels from any output column, values from number columns (aggregates, calculated, number columns)."""
    if not isinstance(raw, dict) or not raw.get("type"):
        return {}
    outputs = spec.output_aliases()
    if raw["type"] not in CHART_TYPES or raw.get("x") not in outputs:
        raise ReportError("Choose the chart type and the column for its labels.")
    numeric = {a["alias"] for a in spec.aggregates} | {c["alias"] for c in spec.calculated}
    numeric |= {n for n in outputs if n in source.columns and source.columns[n].kind == "number"}
    ys = [y for y in (raw.get("y") or []) if y in outputs and y != raw["x"]][:6]
    if not ys:
        raise ReportError("Choose at least one value column for the chart.")
    if any(y not in numeric for y in ys):
        raise ReportError("Chart values must be number columns (counts, sums, averages ...).")
    return {"type": raw["type"], "x": raw["x"], "y": ys, "stacked": bool(raw.get("stacked"))}
