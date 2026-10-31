"""Validate a query definition (plain JSON) and compile it into ORM calls -- ``filter`` / ``values`` / ``annotate`` / ``order_by``
with Django expressions and bound parameters. Never raw SQL: every field path goes through :func:`catalogue.resolve`, every
function / aggregate / operator name through the tables below, and every value through the field's own ``to_python``.

Definition (all keys optional except ``columns``)::

    {"columns": [{"path": "wo__id", "alias": "wo_id"},                                   # plain column (GROUP BY key when grouped)
                 {"path": "created_at", "fn": "date", "alias": "wo_date"},              # DATE(created_at)
                 {"path": "value", "agg": "max", "where": {"path": "data_type_id", "op": "eq", "value": 190}, "alias": "job_id"},
                 {"path": "value", "fn": "before", "arg": ",", "agg": "max", "where": {...}, "alias": "inquery_id"},
                 {"case": {"when": {"path": "wo__wo_order_type", "op": "in", "value": [0, 1, 4]}, "then": 2, "else": 1},
                  "agg": "max", "alias": "inquery_type"},
                 {"agg": "count", "alias": "rows"}],                                    # COUNT(*)
     "filters": {"op": "AND", "items": [{"path": "attend_date", "op": "between", "value": ["2019-06-01", "2019-06-30"]},
                                        {"op": "OR", "items": [...]}]},                 # one nested level
     "having": [{"alias": "rows", "op": "gt", "value": 1}],
     "sort": [{"alias": "wo_id", "dir": "desc"}], "distinct": false, "limit": 0,
     "chart": {"type": "bar", "x": "wo_date", "y": ["rows"]}}

Any column with ``agg`` turns the query into a grouped one: the columns without ``agg`` are the GROUP BY keys (like SQL)."""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field

from django.db import DatabaseError, models, transaction
from django.db.models import Case, Count, F, Max, Min, Q, Sum, Avg, Value, When
from django.db.models import functions as fn
from django.db.models.lookups import GreaterThan
from django.utils import timezone

from core import conf
from core.permissions import filter_by_perm
from core.permissions.access import access_q, restrict
from core.permissions.scope import scope_queryset

from .catalogue import FieldRef, QueryError, catalogue, resolve, table_of, tables
from .registry import QueryModel, allowed_for, query_model_for

ALIAS_RE = re.compile(r"^[a-z][a-z0-9_]{0,40}$")
MAX_COLUMNS, MAX_FILTERS, MAX_HAVING, MAX_SORT, MAX_IN, MAX_JOINS = 40, 40, 10, 5, 500, 20

AGGREGATES = {"count": "Count", "count_distinct": "Count distinct", "sum": "Sum", "avg": "Average", "min": "Minimum", "max": "Maximum"}
NUMERIC_AGGREGATES = {"sum", "avg"}

# function -> (label, kinds it applies to, result kind, needs an argument)
FUNCTIONS = {
    "date": ("Date", ("datetime",), "date", False),
    "year": ("Year", ("date", "datetime"), "number", False),
    "quarter": ("Quarter", ("date", "datetime"), "number", False),
    "month": ("Month", ("date", "datetime"), "number", False),
    "week": ("Week", ("date", "datetime"), "number", False),
    "weekday": ("Weekday (1 = Sunday)", ("date", "datetime"), "number", False),
    "day": ("Day", ("date", "datetime"), "number", False),
    "hour": ("Hour", ("datetime", "time"), "number", False),
    "minute": ("Minute", ("datetime", "time"), "number", False),
    "month_start": ("Month (first day)", ("date", "datetime"), "date", False),
    "year_start": ("Year (first day)", ("date", "datetime"), "date", False),
    "upper": ("Upper case", ("text", "choice"), "text", False),
    "lower": ("Lower case", ("text", "choice"), "text", False),
    "trim": ("Trim spaces", ("text",), "text", False),
    "length": ("Length", ("text",), "number", False),
    "before": ("Text before…", ("text",), "text", True),
    "after": ("Text after…", ("text",), "text", True),
    "to_number": ("As number", ("text", "choice"), "number", False),
    "abs": ("Absolute value", ("number",), "number", False),
    "round": ("Round to… decimals", ("number",), "number", True),
}
# date-part functions usable inside a filter (they are Django lookups: ``out_time__hour__gt``)
FILTER_FUNCTIONS = {"date": "date", "year": "year", "quarter": "quarter", "month": "month", "week": "week", "weekday": "week_day",
                    "day": "day", "hour": "hour", "minute": "minute"}

_COMMON = ("eq", "ne", "in", "not_in", "isnull", "notnull")
_ORDERED = ("gt", "gte", "lt", "lte", "between", "not_between")
OPERATORS = {
    "text": _COMMON + ("contains", "not_contains", "startswith", "endswith") + _ORDERED,
    "number": _COMMON + _ORDERED,
    "date": _COMMON + _ORDERED + ("last_days", "last_months"),
    "datetime": _COMMON + _ORDERED + ("last_days", "last_months"),
    "time": _COMMON + _ORDERED,
    "bool": ("eq", "isnull", "notnull"),
    "choice": _COMMON,
    "fk": _COMMON,
    "uuid": _COMMON,
}
OPERATOR_LABELS = {"eq": "=", "ne": "≠", "in": "is any of", "not_in": "is none of", "isnull": "is empty", "notnull": "is not empty",
                   "contains": "contains", "not_contains": "does not contain", "startswith": "starts with", "endswith": "ends with",
                   "gt": ">", "gte": "≥", "lt": "<", "lte": "≤", "between": "between", "not_between": "not between",
                   "last_days": "in the last … days", "last_months": "in the last … months"}
HAVING_OPERATORS = ("eq", "ne", "gt", "gte", "lt", "lte", "between", "in", "isnull", "notnull")
CHART_TYPES = ("bar", "line", "area", "pie", "doughnut")


# ----------------------------------------------------------------------------------------------------------------- the spec
@dataclass
class Leaf:
    ref: FieldRef
    op: str
    value: object
    fn: str = ""

    @property
    def kind(self) -> str:
        return "number" if self.fn and self.fn not in ("date",) else ("date" if self.fn == "date" else self.ref.kind)


@dataclass
class Column:
    alias: str
    label: str
    ref: FieldRef | None = None
    fn: str = ""
    arg: object = None
    agg: str = ""
    where: Leaf | None = None
    case: dict | None = None            # {"when": Leaf, "then": v, "else": v}
    kind: str = "text"

    @property
    def choices(self) -> dict:
        if self.ref is not None and not self.fn and self.agg in ("", "min", "max") and self.ref.kind == "choice":
            return {str(k): str(v) for k, v in self.ref.field.flatchoices}
        return {}


@dataclass
class QuerySpec:
    root: QueryModel
    columns: list[Column] = field(default_factory=list)
    filters: dict = field(default_factory=lambda: {"op": "AND", "items": []})
    having: list[dict] = field(default_factory=list)
    sort: list[dict] = field(default_factory=list)
    distinct: bool = False
    limit: int = 0
    chart: dict = field(default_factory=dict)
    joins: list[dict] = field(default_factory=list)             # [{"path": "lines", "type": "inner"|"left"}]
    refs: list[FieldRef] = field(default_factory=list)          # every path used (for the join scope)

    @property
    def grouped(self) -> bool:
        return any(c.agg for c in self.columns)

    def labels(self) -> list[tuple[str, str]]:
        return [(c.alias, c.label) for c in self.columns]


# ----------------------------------------------------------------------------------------------------------- value coercion
def _months_ago(n: int) -> dt.date:
    today = timezone.localdate()
    month = today.month - 1 - n
    year = today.year + month // 12
    month = month % 12 + 1
    import calendar

    return today.replace(year=year, month=month, day=min(today.day, calendar.monthrange(year, month)[1]))


def _coerce(ref: FieldRef | None, kind: str, value):
    """One value as the column type (raises QueryError with a readable message)."""
    if value is None or (isinstance(value, str) and value.strip() == ""):
        raise QueryError("Enter a value for every condition.")
    if isinstance(value, (list, dict)):
        raise QueryError("Invalid value.")
    text = str(value).strip()[:200]
    try:
        if kind == "number":
            return float(text) if any(c in text for c in ".eE") else int(text)
        if kind == "bool":
            if text.lower() in ("1", "true", "yes", "on"):
                return True
            if text.lower() in ("0", "false", "no", "off"):
                return False
            raise ValueError
        if kind == "date":
            return dt.date.fromisoformat(text[:10])
        if kind == "datetime":
            if len(text) <= 10:
                return dt.date.fromisoformat(text)                   # a whole day; the operator decides the range
            value = dt.datetime.fromisoformat(text)
            return timezone.make_aware(value) if timezone.is_naive(value) and conf_use_tz() else value
        if kind == "time":
            return dt.time.fromisoformat(text)
        if kind in ("fk", "choice", "uuid") and ref is not None:
            target = ref.field.target_field if kind == "fk" else ref.field
            return target.to_python(text)
    except (ValueError, TypeError, ArithmeticError) as exc:
        raise QueryError(f"{text[:30]!r} is not a valid value for {ref.label if ref else 'this column'}.") from exc
    except Exception as exc:                                       # ValidationError from to_python
        raise QueryError(f"{text[:30]!r} is not a valid value for {ref.label if ref else 'this column'}.") from exc
    return text


def conf_use_tz() -> bool:
    from django.conf import settings

    return bool(settings.USE_TZ)


def _values_list(value) -> list:
    if isinstance(value, str):
        value = [v for v in (x.strip() for x in value.split(",")) if v]
    if not isinstance(value, (list, tuple)) or not value:
        raise QueryError("Enter at least one value.")
    if len(value) > MAX_IN:
        raise QueryError(f"At most {MAX_IN} values in one list.")
    return list(value)


def _operand(op: str, ref: FieldRef | None, kind: str, value):
    if op in ("isnull", "notnull"):
        return None
    if op in ("in", "not_in"):
        return [_coerce(ref, kind, v) for v in _values_list(value)]
    if op in ("between", "not_between"):
        items = _values_list(value)
        if len(items) != 2:
            raise QueryError("Between needs two values.")
        return [_coerce(ref, kind, v) for v in items]
    if op in ("last_days", "last_months"):
        try:
            n = int(str(value).strip())
        except (TypeError, ValueError):
            raise QueryError("Enter a whole number.") from None
        if not 0 < n <= (3660 if op == "last_days" else 240):
            raise QueryError("That period is out of range.")
        return n
    return _coerce(ref, kind, value)


# --------------------------------------------------------------------------------------------------------------- validation
def _leaf(root: QueryModel, user, raw, refs: list) -> Leaf:
    if not isinstance(raw, dict):
        raise QueryError("Invalid condition.")
    ref = resolve(root, user, raw.get("path"))
    refs.append(ref)
    fn_name = str(raw.get("fn") or "")
    kind = ref.kind
    if fn_name:
        if fn_name not in FILTER_FUNCTIONS or kind not in FUNCTIONS[fn_name][1]:
            raise QueryError(f"{fn_name!r} cannot be used on {ref.label} in a condition.")
        kind = FUNCTIONS[fn_name][2]
    op = str(raw.get("op") or "")
    if op not in OPERATORS.get(kind, ()):
        raise QueryError(f"{OPERATOR_LABELS.get(op, op)!r} cannot be used on {ref.label}.")
    return Leaf(ref, op, _operand(op, ref, kind, raw.get("value")), fn_name)


def _tree(root, user, raw, refs, depth=0) -> dict:
    if not raw:
        return {"op": "AND", "items": []}
    if not isinstance(raw, dict) or str(raw.get("op", "AND")).upper() not in ("AND", "OR"):
        raise QueryError("Invalid filter.")
    items = []
    for item in (raw.get("items") or [])[:MAX_FILTERS]:
        if isinstance(item, dict) and "items" in item:
            if depth >= 1:
                raise QueryError("Filters can be nested one level deep.")
            sub = _tree(root, user, item, refs, depth + 1)
            if sub["items"]:
                items.append(sub)
        else:
            items.append(_leaf(root, user, item, refs))
    return {"op": str(raw.get("op", "AND")).upper(), "items": items}


def _alias(value, fallback: str, used: set) -> str:
    text = str(value or "").strip().lower() or fallback
    text = re.sub(r"[^a-z0-9_]+", "_", text).strip("_")[:41] or "col"
    if not text[0].isalpha():
        text = f"c_{text}"[:41]
    if not ALIAS_RE.match(text):
        raise QueryError(f"Invalid column name {text[:30]!r}.")
    base, n = text, 2
    while text in used:
        text = f"{base[:37]}_{n}"
        n += 1
    used.add(text)
    return text


def _constant(value):
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return value
    text = str(value).strip()[:200]
    try:
        return int(text)
    except ValueError:
        try:
            return float(text) if re.fullmatch(r"-?\d+(\.\d+)?", text) else text
        except ValueError:
            return text


def _column(root, user, raw, used: set, refs: list) -> Column:
    if not isinstance(raw, dict):
        raise QueryError("Invalid column.")
    agg = str(raw.get("agg") or "")
    if agg and agg not in AGGREGATES:
        raise QueryError(f"Unknown aggregate {agg[:20]!r}.")
    col = Column(alias="", label="", agg=agg)
    if raw.get("case"):
        case = raw["case"]
        if not isinstance(case, dict):
            raise QueryError("Invalid CASE column.")
        when = _leaf(root, user, case.get("when"), refs)
        then, other = _constant(case.get("then")), _constant(case.get("else"))
        col.case = {"when": when, "then": then, "else": other}
        numeric = all(isinstance(v, (int, float)) or v is None for v in (then, other))
        col.kind = "number" if numeric else "text"
        fallback = "case_value"
        label = "Case"
    elif raw.get("path"):
        ref = resolve(root, user, raw["path"])
        refs.append(ref)
        col.ref, col.kind, label, fallback = ref, ref.kind, ref.label, ref.path
        fn_name = str(raw.get("fn") or "")
        if fn_name:
            if fn_name not in FUNCTIONS or ref.kind not in FUNCTIONS[fn_name][1]:
                raise QueryError(f"{fn_name!r} cannot be used on {ref.label}.")
            fn_label, _kinds, col.kind, needs_arg = FUNCTIONS[fn_name]
            col.fn = fn_name
            if needs_arg:
                arg = str(raw.get("arg") if raw.get("arg") is not None else "").strip()
                if fn_name == "round":
                    try:
                        col.arg = max(0, min(int(arg or 0), 6))
                    except ValueError:
                        raise QueryError("Round needs a number of decimals (0-6).") from None
                else:
                    if not arg or len(arg) > 10:
                        raise QueryError(f"{fn_label} needs the separator text (1-10 characters).")
                    col.arg = arg
            label = f"{fn_label.rstrip('…. ')} of {label}" if fn_name not in ("before", "after") else f"{label} ({fn_label.rstrip('…')} '{col.arg}')"
            fallback = f"{fn_name}_{ref.path}"
    elif agg == "count":
        label, fallback, col.kind = "Rows", "rows", "number"
    else:
        raise QueryError("Every column needs a field (only Count can stand alone).")
    if agg:
        if agg in NUMERIC_AGGREGATES and col.kind != "number":
            raise QueryError(f"{AGGREGATES[agg]} needs a number column (use the “As number” function on text).")
        label = f"{AGGREGATES[agg]} of {label}" if (col.ref or col.case) else label
        fallback = f"{agg}_{fallback}" if (col.ref or col.case) else fallback
        if agg in ("count", "count_distinct"):
            col.kind = "number"
        if raw.get("where"):
            col.where = _leaf(root, user, raw["where"], refs)
    elif raw.get("where"):
        raise QueryError("“Only where” applies to aggregated columns.")
    col.alias = _alias(raw.get("alias"), fallback.replace("__", "_"), used)
    col.label = str(raw.get("label") or "").strip()[:80] or label
    return col


def validate(root: QueryModel, user, raw: dict) -> QuerySpec:
    raw = raw if isinstance(raw, dict) else {}
    spec = QuerySpec(root=root)
    used: set = set()
    refs: list = []
    columns = raw.get("columns") or []
    if not isinstance(columns, list) or not columns:
        raise QueryError("Add at least one column.")
    for item in columns[:MAX_COLUMNS]:
        spec.columns.append(_column(root, user, item, used, refs))
    spec.filters = _tree(root, user, raw.get("filters"), refs)
    aliases = {c.alias: c for c in spec.columns}
    for item in (raw.get("having") or [])[:MAX_HAVING]:
        if not isinstance(item, dict) or item.get("alias") not in aliases:
            raise QueryError("A result condition must use a column of the report.")
        if not spec.grouped:
            raise QueryError("Result conditions need a grouped report (add an aggregate column).")
        col, op = aliases[item["alias"]], str(item.get("op") or "")
        if op not in HAVING_OPERATORS or op not in OPERATORS.get(col.kind, _COMMON + _ORDERED):
            raise QueryError(f"{OPERATOR_LABELS.get(op, op)!r} cannot be used on {col.label}.")
        spec.having.append({"alias": col.alias, "op": op, "value": _operand(op, col.ref if not col.fn and not col.agg else None,
                                                                              col.kind, item.get("value"))})
    for item in (raw.get("sort") or [])[:MAX_SORT]:
        if not isinstance(item, dict) or item.get("alias") not in aliases:
            raise QueryError("Sort by a column of the report.")
        spec.sort.append({"alias": item["alias"], "dir": "desc" if item.get("dir") == "desc" else "asc"})
    spec.distinct = bool(raw.get("distinct")) and not spec.grouped
    try:
        spec.limit = max(0, min(int(raw.get("limit") or 0), conf.get("CORE_EXPORT_MAX_ROWS")))
    except (TypeError, ValueError):
        raise QueryError("Limit must be a whole number.") from None
    chart = raw.get("chart") or {}
    if isinstance(chart, dict) and chart.get("type"):
        if chart["type"] not in CHART_TYPES or chart.get("x") not in aliases:
            raise QueryError("Choose the chart type and the column for its labels.")
        ys = [y for y in (chart.get("y") or []) if y in aliases and y != chart["x"]][:6]
        if not ys:
            raise QueryError("Choose at least one number column for the chart.")
        if any(aliases[y].kind != "number" for y in ys):
            raise QueryError("Chart values must be number columns.")
        spec.chart = {"type": chart["type"], "x": chart["x"], "y": ys, "stacked": bool(chart.get("stacked"))}
    spec.refs = refs
    spec.joins = _joins(root, user, raw, refs)
    return spec


def _ancestors(path: str) -> list[str]:
    parts = path.split("__")
    return ["__".join(parts[:i]) for i in range(1, len(parts) + 1)]


def _joins(root: QueryModel, user, raw: dict, refs: list) -> list[dict]:
    """The joined tables. A definition with a ``joins`` list must join every table it uses (the builder's Joins tab); an older
    definition without one gets its joins from the paths it uses (all LEFT)."""
    used = sorted({table_of(r.path) for r in refs} - {""})
    if "joins" not in raw:
        return [{"path": p, "type": "left"} for p in used]
    items = raw.get("joins")
    if not isinstance(items, list):
        raise QueryError("Joins must be a list.")
    known = {t["p"]: t for t in tables(catalogue(root, user))}
    joins, seen = [], set()
    for item in items[:MAX_JOINS]:
        if not isinstance(item, dict) or item.get("path") not in known:
            raise QueryError("Join one of the related tables offered.")
        if item["path"] in seen:
            continue
        seen.add(item["path"])
        joins.append({"path": item["path"], "type": "inner" if item.get("type") == "inner" else "left"})
    reachable = {a for j in joins for a in _ancestors(j["path"])}
    for path in used:
        if path not in reachable:
            raise QueryError(f"Join {known[path]['l'] if path in known else path} first (Joins tab): a column or filter uses it.")
    return joins


def to_json(spec: QuerySpec) -> dict:
    """The validated definition, normalised, ready to be saved."""
    def leaf(l: Leaf) -> dict:
        value = l.value
        if isinstance(value, list):
            value = [v.isoformat() if hasattr(v, "isoformat") else v for v in value]
        elif hasattr(value, "isoformat"):
            value = value.isoformat()
        return {"path": l.ref.path, "op": l.op, "value": value, **({"fn": l.fn} if l.fn else {})}

    def tree(t: dict) -> dict:
        return {"op": t["op"], "items": [tree(i) if isinstance(i, dict) else leaf(i) for i in t["items"]]}

    columns = []
    for c in spec.columns:
        item = {"alias": c.alias, "label": c.label}
        if c.case:
            item["case"] = {"when": leaf(c.case["when"]), "then": c.case["then"], "else": c.case["else"]}
        if c.ref is not None:
            item["path"] = c.ref.path
        if c.fn:
            item["fn"] = c.fn
            if c.arg is not None:
                item["arg"] = c.arg
        if c.agg:
            item["agg"] = c.agg
        if c.where is not None:
            item["where"] = leaf(c.where)
        columns.append(item)
    having = [{**h, "value": [v.isoformat() if hasattr(v, "isoformat") else v for v in h["value"]] if isinstance(h["value"], list)
               else (h["value"].isoformat() if hasattr(h["value"], "isoformat") else h["value"])} for h in spec.having]
    return {"joins": spec.joins, "columns": columns, "filters": tree(spec.filters), "having": having, "sort": spec.sort,
            "distinct": spec.distinct, "limit": spec.limit, "chart": spec.chart}


# ---------------------------------------------------------------------------------------------------------------- compiling
def _leaf_q(leaf: Leaf) -> Q:
    path = leaf.ref.path + (f"__{FILTER_FUNCTIONS[leaf.fn]}" if leaf.fn else "")
    op, v, kind = leaf.op, leaf.value, leaf.kind
    whole_days = kind == "datetime" and not leaf.fn

    def day_range(d):
        """A date given for a date-time field means the whole day."""
        start = dt.datetime.combine(d, dt.time.min)
        if conf_use_tz():
            start = timezone.make_aware(start)
        return start, start + dt.timedelta(days=1)

    if op == "isnull":
        return Q(**{f"{path}__isnull": True})
    if op == "notnull":
        return Q(**{f"{path}__isnull": False})
    if op == "last_days":
        since = timezone.now() - dt.timedelta(days=v) if kind == "datetime" else timezone.localdate() - dt.timedelta(days=v)
        return Q(**{f"{path}__gte": since})
    if op == "last_months":
        since = _months_ago(v)
        return Q(**{f"{path}__gte": day_range(since)[0] if kind == "datetime" else since})
    if whole_days and isinstance(v, dt.date) and not isinstance(v, dt.datetime):
        start, end = day_range(v)
        if op == "eq":
            return Q(**{f"{path}__gte": start, f"{path}__lt": end})
        if op == "ne":
            return Q(**{f"{path}__lt": start}) | Q(**{f"{path}__gte": end})
        v = {"gt": end, "gte": start, "lt": start, "lte": end}.get(op, v)
        op = {"gt": "gte", "lte": "lt"}.get(op, op)
    if op in ("between", "not_between"):
        low, high = v
        if whole_days:
            low = day_range(low)[0] if not isinstance(low, dt.datetime) else low
            high = day_range(high)[1] if not isinstance(high, dt.datetime) else high
            inside = Q(**{f"{path}__gte": low, f"{path}__lt": high})
            return inside if op == "between" else (Q(**{f"{path}__lt": low}) | Q(**{f"{path}__gte": high}))
        return Q(**{f"{path}__range": (low, high)}) if op == "between" else (Q(**{f"{path}__lt": low}) | Q(**{f"{path}__gt": high}))
    if op == "eq":
        return Q(**{path: v})
    if op == "ne":                                       # row-level "!=" (NULLs excluded, like SQL), also across joins
        if kind == "bool":
            return Q(**{path: not v})
        return Q(**{f"{path}__lt": v}) | Q(**{f"{path}__gt": v})
    if op == "in":
        return Q(**{f"{path}__in": v})
    if op == "not_in":
        return Q(**{f"{path}__isnull": False}) & ~Q(**{f"{path}__in": v})
    if op == "contains":
        return Q(**{f"{path}__icontains": v})
    if op == "not_contains":
        return Q(**{f"{path}__isnull": False}) & ~Q(**{f"{path}__icontains": v})
    if op == "startswith":
        return Q(**{f"{path}__istartswith": v})
    if op == "endswith":
        return Q(**{f"{path}__iendswith": v})
    return Q(**{f"{path}__{op}": v})                     # gt / gte / lt / lte


def _tree_q(tree: dict) -> Q:
    q = Q()
    for item in tree.get("items", []):
        part = _tree_q(item) if isinstance(item, dict) else _leaf_q(item)
        q = (q | part) if tree.get("op") == "OR" and q else (q & part)
    return q


def _expr(col: Column):
    if col.case:
        when, then, other = col.case["when"], col.case["then"], col.case["else"]
        output = models.FloatField() if col.kind == "number" and any(isinstance(v, float) for v in (then, other)) else \
            models.IntegerField() if col.kind == "number" else models.CharField()
        return Case(When(_leaf_q(when), then=Value(then)), default=Value(other), output_field=output)
    if col.ref is None:
        return None
    f, name = F(col.ref.path), col.fn
    if not name:
        return f
    if name == "date":
        return fn.TruncDate(f)
    if name in ("year", "quarter", "month", "week", "day", "hour", "minute"):
        return {"year": fn.ExtractYear, "quarter": fn.ExtractQuarter, "month": fn.ExtractMonth, "week": fn.ExtractWeek,
                "day": fn.ExtractDay, "hour": fn.ExtractHour, "minute": fn.ExtractMinute}[name](f)
    if name == "weekday":
        return fn.ExtractWeekDay(f)
    if name == "month_start":
        return fn.TruncMonth(f, output_field=col.ref.field.__class__()) if col.ref.kind == "date" else fn.TruncMonth(f)
    if name == "year_start":
        return fn.TruncYear(f, output_field=col.ref.field.__class__()) if col.ref.kind == "date" else fn.TruncYear(f)
    if name == "upper":
        return fn.Upper(f)
    if name == "lower":
        return fn.Lower(f)
    if name == "trim":
        return fn.Trim(f)
    if name == "length":
        return fn.Length(f)
    if name == "to_number":
        return fn.Cast(f, models.FloatField())
    if name == "abs":
        return fn.Abs(f)
    if name == "round":
        return fn.Round(f, precision=col.arg or 0)
    sep = Value(col.arg)
    position = fn.StrIndex(f, sep)
    if name == "before":                                 # like MySQL SUBSTRING_INDEX(value, ',', 1)
        return Case(When(GreaterThan(position, 0), then=fn.Left(f, position - 1)), default=f, output_field=models.TextField())
    return Case(When(GreaterThan(position, 0), then=fn.Substr(f, position + len(col.arg))), default=Value(None),   # after
                output_field=models.TextField())


def _aggregate(col: Column):
    expr = _expr(col) if (col.ref is not None or col.case) else F("pk")
    extra = {"filter": _leaf_q(col.where)} if col.where is not None else {}
    if col.agg == "count":
        return Count(expr, **extra)
    if col.agg == "count_distinct":
        return Count(expr, distinct=True, **extra)
    return {"sum": Sum, "avg": Avg, "min": Min, "max": Max}[col.agg](expr, **extra)


def root_queryset(root: QueryModel, ctx):
    if root.queryset is not None:
        qs = root.queryset(ctx)
    else:
        qs = root.model._default_manager.all()
        if root.company_lookup:
            qs = scope_queryset(qs, root.company_lookup, ctx.company_ids)
    qs = restrict(qs, ctx.user)
    return filter_by_perm(ctx.user, root.permission, qs) if root.permission else qs.none()


def _join_scope(spec: QuerySpec, ctx) -> Q:
    """Rows reached through a join must be in the active company, not soft-deleted and allowed by the user's row-level access
    (a missing related row is kept)."""
    from core.models.base import SoftDeleteModel

    q, seen = Q(), set()
    for ref in spec.refs:
        for hop in ref.hops:
            if hop.prefix in seen:
                continue
            seen.add(hop.prefix)
            missing = Q(**{f"{hop.prefix}__isnull": True})
            if hop.entry.company_lookup:
                q &= Q(**{f"{hop.prefix}__{hop.entry.company_lookup}__in": list(ctx.company_ids)}) | missing
            if issubclass(hop.entry.model, SoftDeleteModel):
                q &= Q(**{f"{hop.prefix}__deleted_at__isnull": True}) | missing
            limited = access_q(hop.entry.model, ctx.user, hop.prefix)
            if limited is not None:
                q &= limited | missing
    return q


@dataclass
class QueryResult:
    columns: list[tuple[str, str]]          # (alias, label)
    queryset: object                        # values() queryset with internal names c0..cN, or a list (one aggregate row)
    names: dict                             # alias -> internal name
    spec: QuerySpec
    ctx: object = None                      # the run's Ctx; related-record names are looked up within it
    company_fill: bool = False              # many-company root: the Company column is filled per row from ``core_pk``

    def rows(self, start: int = 0, stop: int | None = None) -> list[dict]:
        """Rows keyed by alias, sliced on the database side."""
        data = self.queryset[start:stop]
        out = []
        for raw in _run(lambda: list(data)):
            out.append({alias: raw.get(name) for alias, name in self.names.items()})
        self._label_relations(out)
        self._fill_companies(out)
        return out

    def _fill_companies(self, rows: list[dict]) -> None:
        if not self.company_fill or not rows:
            return
        from core.reports import scope

        found = scope.row_companies(self.spec.root.model, self.spec.root.company_lookup, [r.get(scope.PK_ALIAS) for r in rows], self.ctx)
        for r in rows:
            r[scope.COMPANY_ALIAS] = found.get(r.get(scope.PK_ALIAS), "")

    def _label_relations(self, rows: list[dict]) -> None:
        """A plain related-record column (``kind == "fk"``) shows the record's name instead of its id, but only for records the
        user may see in that table (same scoping as a join); anything else keeps its id. One query per column."""
        if self.ctx is None:
            return
        for col in self.spec.columns:
            if col.agg or col.case or col.fn or col.ref is None or col.ref.kind != "fk":
                continue
            ids = {r[col.alias] for r in rows if r.get(col.alias) is not None}
            target = query_model_for(col.ref.field.related_model)
            if not ids or target is None or not allowed_for(self.ctx.user, target):
                continue
            found = root_queryset(target, self.ctx).in_bulk(ids)
            for r in rows:
                obj = found.get(r.get(col.alias))
                if obj is not None:
                    r[col.alias] = str(obj)

    def count(self) -> int:
        return _run(lambda: len(self.queryset) if isinstance(self.queryset, list) else self.queryset.count())


def _run(call):
    """Run a database call in a savepoint; a failing query becomes a readable QueryError instead of a 500."""
    try:
        with transaction.atomic():
            return call()
    except DatabaseError as exc:
        raise QueryError("The database could not run this query (check functions such as “As number” on text that is not "
                         "a number).") from exc


def build(spec: QuerySpec, ctx) -> QueryResult:
    qs = root_queryset(spec.root, ctx)
    where = _tree_q(spec.filters) & _join_scope(spec, ctx)
    for join in spec.joins:
        if join["type"] == "inner":                       # INNER: keep only rows that have a matching related row
            where &= Q(**{f"{join['path']}__isnull": False})
    if where:
        qs = qs.filter(where)                            # ONE filter() call: conditions on a to-many join apply to the same row
    names = {c.alias: f"c{i}" for i, c in enumerate(spec.columns)}
    labels = spec.labels()
    company, fill = _company_column(spec, ctx)
    if spec.grouped:
        keys = {names[c.alias]: _expr(c) for c in spec.columns if not c.agg}
        aggs = {names[c.alias]: _aggregate(c) for c in spec.columns if c.agg}
        if not keys:                                     # aggregates only: one row (over every company in scope)
            row = _run(lambda: qs.aggregate(**aggs))
            rows = [row] if spec.having == [] or _having_ok(row, spec, names) else []
            return QueryResult(labels, rows, names, spec, ctx)
        if company is not None:                          # one row per company and group
            keys.update(company)
        qs = qs.order_by().values(**keys).annotate(**aggs)
        default_order = list(keys)
    else:
        if fill and spec.distinct:
            fill = False                                 # the row id would defeat "distinct rows": no Company column then
            company = None
        qs = qs.values(**{names[c.alias]: _expr(c) for c in spec.columns}, **(company or {}))
        if spec.distinct:
            qs = qs.distinct()
        default_order = [] if spec.distinct else ["pk"]
    for h in spec.having:
        qs = qs.filter(_having_q(names[h["alias"]], h["op"], h["value"]))
    order = [("-" if s["dir"] == "desc" else "") + names[s["alias"]] for s in spec.sort]
    if spec.distinct and not order:
        order = [names[spec.columns[0].alias]]
    qs = qs.order_by(*order, *[o for o in default_order if o not in [x.lstrip("-") for x in order]])
    if spec.limit:
        qs = qs[: spec.limit]
    if company is not None:
        from core.reports import scope

        names = {scope.COMPANY_ALIAS: scope.COMPANY_ALIAS, **names, **({scope.PK_ALIAS: scope.PK_ALIAS} if fill else {})}
        labels = [(scope.COMPANY_ALIAS, scope.COMPANY_LABEL)] + labels
    return QueryResult(labels, qs, names, spec, ctx, fill)


def _company_column(spec: QuerySpec, ctx):
    """``(values extra, fill)`` for the Company column when more than one company is in scope: a single-valued company path is a
    plain column (a grouping key when grouped); a many-valued one is filled per row from the row id (not when grouped)."""
    from core.reports import scope

    lookup = spec.root.company_lookup
    if ctx is None or not scope.wants_company_column(ctx, lookup):
        return None, False
    if scope.single_valued(spec.root.model, lookup):
        return {scope.COMPANY_ALIAS: F(scope.abbreviation_path(lookup))}, False
    if spec.grouped:
        return None, False
    return {scope.PK_ALIAS: F("pk")}, True


def _having_q(name: str, op: str, value) -> Q:
    if op == "isnull":
        return Q(**{f"{name}__isnull": True})
    if op == "notnull":
        return Q(**{f"{name}__isnull": False})
    if op == "between":
        return Q(**{f"{name}__range": tuple(value)})
    if op == "ne":
        return Q(**{f"{name}__lt": value}) | Q(**{f"{name}__gt": value})
    if op == "eq":
        return Q(**{name: value})
    return Q(**{f"{name}__{op}": value})


def _having_ok(row: dict, spec: QuerySpec, names: dict) -> bool:
    for h in spec.having:
        v, want, op = row.get(names[h["alias"]]), h["value"], h["op"]
        checks = {"isnull": lambda: v is None, "notnull": lambda: v is not None, "eq": lambda: v == want,
                  "ne": lambda: v is not None and v != want, "gt": lambda: v is not None and v > want,
                  "gte": lambda: v is not None and v >= want, "lt": lambda: v is not None and v < want,
                  "lte": lambda: v is not None and v <= want, "in": lambda: v in want,
                  "between": lambda: v is not None and want[0] <= v <= want[1]}
        if not checks[op]():
            return False
    return True
