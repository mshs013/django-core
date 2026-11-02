"""Custom reports: reports written in Python for what the report builder cannot express -- a document for one record (work order,
invoice, delivery challan), cross-model summaries, raw business logic, a fixed layout with totals and signatures.

Same registry shape as the other engines: ``register_custom_report`` from ``AppConfig.ready()`` -> allowlist -> ``get_custom_report``.
Each report gets a page at ``/reports/custom/<key>/`` (parameter form -> preview -> PDF / spreadsheet download) and is listed on
the Reports page for users holding its permission::

    from django import forms
    from core.reports.custom import ReportOutput, register_custom_report

    class Params(forms.Form):
        date_from = forms.DateField()

    def run(ctx, params):                      # ctx: user / active company; params: the form's cleaned_data
        rows = Order.objects.filter(company_id=ctx.company_id, date__gte=params["date_from"]).values("no", "qty")
        return ReportOutput(columns=[("no", "Order no"), ("qty", "Qty")], rows=list(rows), totals={"qty": sum(r["qty"] for r in rows)})

    register_custom_report("orders-since", "Orders since a date", run, form=Params, permission="sales.view_order")

``run`` must scope its own queries (``ctx.company_ids`` is the data scope; see ``core.permissions.scope.scope_queryset``): a custom
report runs arbitrary code, so the registry cannot scope it for you. Permission is checked before ``run`` is ever called.

``menu="Inventory > Yarn"`` also shows the report in the sidebar under that menu group (titles from the top level; the group is
created in the Menu builder), to users holding its permission. ``register_stock_ledger`` builds a ready-made stock ledger (opening
balance, receipts, issues, running balance) over any stock movement model.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Sequence

from django.core.exceptions import ImproperlyConfigured

KEY_RE = re.compile(r"^[a-z][a-z0-9_-]{0,60}$")
DEFAULT_FORMATS = ("pdf", "xlsx", "csv")


@dataclass
class ReportOutput:
    """What ``run`` returns. Only ``columns`` and ``rows`` are required; the rest shapes the document."""

    columns: list[tuple[str, str]]                         # [(key, label)] in display order
    rows: list[dict]                                       # one dict per row, keyed by column key
    title: str = ""                                        # document title (default: the report label)
    subtitle: str = ""                                     # small line under the header
    notice: str = ""                                       # highlighted line under the header (e.g. "Quote this no. on the invoice")
    info: list[tuple[str, object]] = field(default_factory=list)   # label/value pairs printed above the table
    totals: dict = field(default_factory=dict)             # {column key: value} -> a bold "Total" row under the table
    notes: list[str] = field(default_factory=list)         # paragraphs printed after the table
    signatures: list = field(default_factory=list)         # [core.reports.letterhead.Signature]; see core.approvals.signatures
    numeric: tuple[str, ...] = ()                          # column keys aligned right

    def total_row(self) -> dict | None:
        if not self.totals:
            return None
        first = self.columns[0][0] if self.columns else ""
        return {first: "Total", **{k: v for k, v in self.totals.items() if k != first or v not in (None, "")}}


@dataclass
class CustomReport:
    key: str
    label: str
    run: Callable
    form: type | None
    permission: tuple[str, ...]
    description: str = ""
    category: str = ""
    formats: tuple[str, ...] = DEFAULT_FORMATS
    pdf_template: Callable | None = None                    # fn(buffer, report, output, meta) -> None; default: render_custom_pdf
    letterhead_class: object = None                         # LetterheadTemplate subclass (or dotted path) for this report only
    portrait: bool = False
    date_formats: object = None                             # format string or {"date"|"datetime"|"time": ...}; None = site formats
    menu: str = ""                                          # "Inventory > Yarn": the menu group it is shown under ("" = not in the menu)
    menu_label: str = ""                                    # menu text (default: the label)
    menu_position: int = 100

    def make_form(self, data=None, ctx=None):
        """The parameter form; a form class with ``takes_ctx = True`` also gets the run's ``ctx`` (e.g. to scope its choices)."""
        if self.form is None:
            return None
        kwargs = {"ctx": ctx} if getattr(self.form, "takes_ctx", False) else {}
        return self.form(data, **kwargs) if data is not None else self.form(**kwargs)


_REPORTS: dict[str, CustomReport] = {}


def register_custom_report(key: str, label: str, run: Callable, *, permission: Sequence[str] | str, form: type | None = None,
                           description: str = "", category: str = "", formats: Sequence[str] = DEFAULT_FORMATS,
                           pdf_template: Callable | None = None, letterhead_class=None, portrait: bool = False,
                           date_formats: str | dict | None = None, menu: str = "", menu_label: str = "",
                           menu_position: int = 100) -> CustomReport:
    """``permission`` is required (fail closed): a codename, a list of codenames (all needed), or ``()`` for any signed-in user.
    ``menu``: the menu group's titles from the top level, e.g. ``"Inventory > Yarn"`` (shown there when the group exists)."""
    if key in _REPORTS:
        raise ImproperlyConfigured(f"Custom report {key!r} is already registered.")
    if not KEY_RE.match(key):
        raise ImproperlyConfigured(f"Custom report key {key!r} must be lower-case letters, digits, '-' or '_'.")
    if not callable(run):
        raise ImproperlyConfigured(f"Custom report {key!r}: run must be callable as run(ctx, params).")
    perms = (permission,) if isinstance(permission, str) else tuple(permission)
    bad = [f for f in formats if f != "pdf" and not re.match(r"^[a-z]{2,5}$", f)]
    if bad or not formats:
        raise ImproperlyConfigured(f"Custom report {key!r}: invalid formats {list(formats)}")
    report = CustomReport(key, label, run, form, perms, description, category, tuple(formats), pdf_template, letterhead_class, portrait,
                          date_formats, str(menu or "").strip(), str(menu_label or "").strip()[:80], int(menu_position))
    _REPORTS[key] = report
    return report


def get_custom_report(key: str) -> CustomReport | None:
    return _REPORTS.get(key)


def all_custom_reports() -> list[CustomReport]:
    return sorted(_REPORTS.values(), key=lambda r: (r.category.lower(), r.label.lower()))


def unregister_custom_report(key: str) -> None:
    _REPORTS.pop(key, None)


# ------------------------------------------------------------------------------------------------------------------- running
def execute(report: CustomReport, ctx, params: dict) -> ReportOutput:
    """Run the report and check what came back (a bad ``run`` fails loudly in development, not with a half-drawn PDF)."""
    from core import conf
    from core.audit.service import record_event

    output = report.run(ctx, params)
    if not isinstance(output, ReportOutput):
        raise ImproperlyConfigured(f"Custom report {report.key!r}: run() must return a ReportOutput.")
    output.rows = list(output.rows)
    limit = conf.get("CORE_EXPORT_MAX_ROWS")
    if len(output.rows) > limit:
        from core.reports.builder.definition import ReportError

        raise ReportError(f"This report has more than {limit} rows; narrow the parameters.")
    from core.reports.scope import audit_note

    record_event("report_run", message=f"custom {report.key}; {audit_note(ctx)}", object_repr=report.label, company=ctx.company_id,
                 user=ctx.user)
    return output


# ------------------------------------------------------------------------------------------------------------------- stock ledger
def register_stock_ledger(key: str, label: str, model, *, item_field: str, date_field: str, qty_in: str, qty_out: str,
                          permission: Sequence[str] | str | None = None, columns: Sequence[tuple[str, str]] = (),
                          company_field: str | None = "company", item_label: str = "", queryset: Callable | None = None,
                          **options) -> CustomReport:
    """A stock ledger over a movement model (one row per receipt or issue) -- what the report builder cannot do (an opening balance
    and a running balance need the rows in order). Per item: an *Opening balance* row (everything before *From*), each movement of the
    period in date order with its receipt / issue and the running balance, and a *Closing balance* row.

    * ``item_field``: FK to the item (yarn, lot ...); ``date_field``: date or datetime; ``qty_in`` / ``qty_out``: number fields (a row
      may fill either); ``columns``: extra ``(path, label)`` per movement, e.g. ``(("challan_no", "Challan"), ("party__name", "Party"))``.
    * Parameters: item (empty = every item, each with its own balances), from / to dates; the companies are the run's company scope
      (the *Companies* control). With several companies in scope each item is balanced per company and a Company column is added.
    * Rows are scoped like a report source: company (``company_field``), row-level access, then ``permission`` (default
      ``<app>.view_<model>``). ``queryset(ctx)`` may narrow the base rows (e.g. one store). ``options`` go to
      ``register_custom_report`` (``menu="Inventory > Yarn"``, ``category``, ``description``, ``formats`` ...).
    """
    import datetime as dt

    from django import forms
    from django.db.models import Sum

    from core.permissions import filter_by_perm
    from core.permissions.access import restrict
    from core.permissions.scope import scope_queryset

    opts = model._meta
    if permission is None:
        permission = f"{opts.app_label}.view_{opts.model_name}"
    perms = (permission,) if isinstance(permission, str) else tuple(permission)
    item_fk = opts.get_field(item_field)
    item_model = item_fk.related_model
    if item_model is None or not item_fk.many_to_one:
        raise ImproperlyConfigured(f"Stock ledger {key!r}: {item_field!r} must be a foreign key to the item.")
    item_label = item_label or str(item_fk.verbose_name).capitalize()
    extra = [(path, str(text)) for path, text in columns]
    item_col = item_fk.attname
    company_col = opts.get_field(company_field).attname if company_field and "__" not in company_field else company_field

    def movements(ctx):
        qs = queryset(ctx) if queryset is not None else model._default_manager.all()
        if company_field:
            qs = scope_queryset(qs, company_field, ctx.company_ids)
        qs = restrict(qs, ctx.user)
        for perm in perms:
            qs = filter_by_perm(ctx.user, perm, qs)
        return qs

    class Params(forms.Form):
        takes_ctx = True
        item = forms.ModelChoiceField(queryset=item_model._default_manager.none(), required=False, label=item_label, empty_label="All")
        date_from = forms.DateField(label="From", required=False, initial=lambda: dt.date.today().replace(day=1))
        date_to = forms.DateField(label="To", required=False, initial=dt.date.today)

        def __init__(self, *args, ctx=None, **kwargs):
            super().__init__(*args, **kwargs)
            if ctx is not None:                            # only items that have movements the user may see
                self.fields["item"].queryset = item_model._default_manager.filter(pk__in=movements(ctx).values(item_col))

        def clean(self):
            data = super().clean()
            if data.get("date_from") and data.get("date_to") and data["date_from"] > data["date_to"]:
                raise forms.ValidationError("From must not be after To.")
            return data

    def run(ctx, params):
        from core import conf
        from core.reports.scope import company_labels

        qs = movements(ctx)
        if params.get("item") is not None:
            qs = qs.filter(**{item_col: params["item"].pk})
        multi = bool(company_field) and len(ctx.company_ids or ()) > 1
        group = [item_col, company_col] if multi else [item_col]
        start, end = params.get("date_from"), params.get("date_to")
        opening: dict = {}
        if start:
            before = qs.filter(**{f"{date_field}__lt": start}).values(*group).annotate(i=Sum(qty_in), o=Sum(qty_out)).order_by()
            opening = {tuple(r[g] for g in group): (r["i"] or 0) - (r["o"] or 0) for r in before}
        period = qs
        if start:
            period = period.filter(**{f"{date_field}__gte": start})
        if end:                                              # inclusive end date, also for datetime fields
            period = period.filter(**{f"{date_field}__lt": end + dt.timedelta(days=1)})
        limit = conf.get("CORE_EXPORT_MAX_ROWS")             # execute() refuses more; never load an unbounded period
        moves = list(period.order_by(*group, date_field, "pk").values(*group, date_field, qty_in, qty_out, *[p for p, _ in extra])[:limit + 1])
        by_key: dict = {}
        for m in moves:
            by_key.setdefault(tuple(m[g] for g in group), []).append(m)
        keys = set(opening) | set(by_key)
        names = {o.pk: str(o) for o in item_model._base_manager.filter(pk__in={k[0] for k in keys})}
        companies = company_labels({k[1] for k in keys}) if multi else {}
        keys = sorted(keys, key=lambda k: (names.get(k[0], "").casefold(), k[0], companies.get(k[1], ("",))[0] if multi else ""))
        rows, total_in, total_out, closing_sum = [], 0, 0, 0
        for k in keys:
            base = {"item": names.get(k[0], f"#{k[0]}")}
            if multi:
                base["company"] = companies.get(k[1], ("",))[0]
            balance = opening.get(k, 0)
            rows.append({**base, "date": start, "particulars": "Opening balance", "balance": balance})
            got = out = 0
            for m in by_key.get(k, ()):
                received, issued = m[qty_in] or 0, m[qty_out] or 0
                balance += received - issued
                got, out = got + received, out + issued
                rows.append({**base, "date": m[date_field], **{p: m[p] for p, _ in extra}, "received": received or "",
                             "issued": issued or "", "balance": balance})
            rows.append({**base, "date": end, "particulars": "Closing balance", "received": got, "issued": out, "balance": balance})
            total_in, total_out, closing_sum = total_in + got, total_out + out, closing_sum + balance
        cols = [("item", item_label)] + ([("company", "Company")] if multi else []) + [("date", "Date"), ("particulars", "Particulars")]
        cols += extra + [("received", "Received"), ("issued", "Issued"), ("balance", "Balance")]
        item = params.get("item")
        info = [(item_label, str(item) if item is not None else "All"), ("From", start or "—"), ("To", end or "—")]
        return ReportOutput(columns=cols, rows=rows, info=info, numeric=("received", "issued", "balance"),
                            totals={"received": total_in, "issued": total_out, "balance": closing_sum} if keys else {})

    return register_custom_report(key, label, run, permission=perms, form=Params, **options)


def render_custom_pdf(buffer, report: CustomReport, output: ReportOutput, meta: dict) -> None:
    """Default PDF body: info grid, highlighted notice, table (+ total row), notes. Letterhead and signatures come from
    :func:`~core.reports.resources.pdf.build_branded`."""
    from xml.sax.saxutils import escape

    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, Spacer, Table, TableStyle

    from core.reports.resources.pdf import _cell, build_branded

    fmt = report.date_formats
    styles = getSampleStyleSheet()
    small =styles["BodyText"].clone("cr_cell", fontSize=7.5, leading=9.5)
    right = small.clone("cr_right", alignment=2)
    body = []
    if output.notice:
        notice = styles["BodyText"].clone("cr_notice", fontSize=8.5, alignment=1, textColor=colors.HexColor("#d63939"))
        body += [Paragraph(f"<b>{escape(output.notice)}</b>", notice), Spacer(1, 6)]
    if output.subtitle:
        body += [Paragraph(escape(output.subtitle), styles["Normal"]), Spacer(1, 4)]
    if output.info:
        per_row = 3
        cells = [Paragraph(f"{escape(str(label))} : <b>{escape(_cell(value, fmt))}</b>", small) for label, value in output.info]
        cells += [""] * (-len(cells) % per_row)
        grid = Table([cells[i:i + per_row] for i in range(0, len(cells), per_row)], hAlign="LEFT")
        grid.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 2), ("TOPPADDING", (0, 0), (-1, -1), 1),
                                  ("BOTTOMPADDING", (0, 0), (-1, -1), 1)]))
        body += [grid, Spacer(1, 6)]
    keys = [k for k, _ in output.columns]
    data = [[Paragraph(f"<b>{escape(label)}</b>", small) for _, label in output.columns]]
    data += [[Paragraph(escape(_cell(row.get(k), fmt)), right if k in output.numeric else small) for k in keys] for row in output.rows]
    total = output.total_row()
    if total is not None:
        data.append([Paragraph(f"<b>{escape(_cell(total.get(k), fmt))}</b>", right if k in output.numeric else small) for k in keys])
    table = Table(data, repeatRows=1)
    style = [("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e9ecef")), ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
             ("VALIGN", (0, 0), (-1, -1), "TOP")]
    if total is not None:
        style.append(("LINEABOVE", (0, -1), (-1, -1), 0.8, colors.black))
    table.setStyle(TableStyle(style))
    body.append(table)
    for note in output.notes:
        body += [Spacer(1, 6), Paragraph(escape(str(note)).replace("\n", "<br/>"), small)]
    build_branded(buffer, output.title or report.label, body, meta)


def custom_report_bytes(report: CustomReport, output: ReportOutput, ctx, ext: str) -> tuple[bytes, str, str]:
    """(data, file name, content type) for one of the report's formats."""
    import io

    if ext not in report.formats:
        raise ValueError("Unsupported export format.")
    if ext == "pdf":
        from reportlab.lib.pagesizes import A4, landscape

        from core.reports.resources.export import pdf_meta

        meta = pdf_meta(ctx, title=output.title or report.label, signatures=list(output.signatures),
                        pagesize=A4 if report.portrait else landscape(A4))
        if report.letterhead_class:
            meta["letterhead_class"] = report.letterhead_class
        buffer = io.BytesIO()
        (report.pdf_template or render_custom_pdf)(buffer, report, output, meta)
        return buffer.getvalue(), f"{report.key}.pdf", "application/pdf"
    from core.reports.resources.formats import get_format
    from core.reports.resources.resources import as_row, rows_resource

    keys = [k for k, _ in output.columns]
    rows = [as_row({k: row.get(k) for k in keys}) for row in output.rows]
    total = output.total_row()
    if total is not None:
        rows.append(as_row({k: total.get(k, "") for k in keys}))
    fmt = get_format(ext)
    data = fmt.export_data(rows_resource(output.columns).export(queryset=rows))
    data = data.encode("utf-8") if isinstance(data, str) else data
    if ext == "csv":
        data = "﻿".encode("utf-8") + data
    return data, f"{report.key}.{ext}", fmt.get_content_type()
