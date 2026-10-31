# Dynamic Report Builder & Custom Reports

Four ways to get a report out of the system, all printed on the same PDF letterhead:

| | Who builds it | Where | Use it for |
|---|---|---|---|
| **Report builder** (§2–§4) | End users, no code | *Reports → New report from …* | Lists, totals and breakdowns of one data source: pick columns, filter, group, aggregate, save, share, export. |
| **Query builder** ([QUERY_BUILDER.md](QUERY_BUILDER.md)) | End users, no code | *Reports → Query builder* | Free-form queries over any allowed table: join related tables, functions (DATE, HOUR, text before ','), IN / BETWEEN / ≠ filters, conditional aggregates, CASE, HAVING, charts. |
| **Report sources** (§5) | Developers, a few lines | `register_report_source()` | Deciding *what* the builder may offer for a model (columns, aggregates, filters, permissions). Every list page also gets an automatic source. |
| **Custom reports** (§6) | Developers, Python | `register_custom_report()` | Anything the builder cannot express: one-record documents (work order, invoice, challan), multi-model logic, fixed layouts with totals and signatures. |

All PDFs share the header/footer template described in §7 (logo, company block, title, "Generated On/By", "Page X of Y", optional
signature boxes). It is replaceable in one setting.

Code: `core/reports/` · views: `core/views/reports.py`, `core/views/exports.py` · templates: `core/templates/core/reports/` ·
tests: `tests/test_reports.py`, `tests/test_custom_reports.py`

---

## 1. Concepts

* **Report source** — an allowlist registered in code for one model: which columns exist, which can be grouped, which aggregate
  functions each column allows, which fields can be filtered, who may use it. Nothing in a saved report is trusted beyond it.
* **Report definition** (`ReportDefinition`) — a saved report: JSON choices (columns, filters, groupings, aggregates, calculated
  columns, sorting, parameters, page size) + name, owner and sharing (no company: a saved report is offered in every company and runs on the active company's rows). It is **re-validated against the source every time it
  is opened**, so removing a column from a source can never expose data through an old report.
* **Custom report** — a Python function `run(ctx, params) -> ReportOutput` with an optional parameter form, registered under a key.
* **Export** — CSV, TSV, XLSX, XLS, ODS, JSON, YAML, HTML (whatever tablib supports on the install) and PDF.

---

## 2. Using the report builder (end users)

### 2.1 Build a report
1. Open **Reports** (`/reports/`). The left column, **New report from**, lists the data sources you are allowed to use.
2. Pick one. The builder opens with the source's default columns ticked and a live preview.
3. **Columns** — tick the columns to show (max 30). Order follows the source.
4. **Filters** — use the search bar above the builder exactly like on a list page (quick search, **+ Filter**, AND/OR groups;
   see [SEARCH_BUILDER.md](SEARCH_BUILDER.md)). The filter is stored with the report.
5. **Group by** — tick one or more groupable columns to get one row per group instead of one row per record.
6. **Aggregates** — **+ Aggregate**, then choose a function and a column: `count`, `count_distinct`, `sum`, `avg`, `min`, `max`
   (sum/avg only on number columns; the source decides what is allowed per column). Choose **(rows)** with `count` to count records.
   Grouping without an aggregate automatically adds a `rows` count.
7. **Chart** (optional) — Bar, Line, Area, Pie or Doughnut; labels from one output column, values from number columns
   (counts, sums, averages, number columns). It is drawn above the results from the first `CORE_CHART_MAX_POINTS` rows and is
   saved with the report.
8. **Preview** refreshes the chart and the result (paged, at most `CORE_PREVIEW_ROWS` rows per page; the pager shows
   *Showing 1 to 20 of 65 entries* and *First « 1 2 3 … » Last*).

The options (Columns, Group by, Aggregates, Chart, Export, Save) sit in a panel on the left. **Hide options** collapses it so the
chart and results use the full width; the choice is remembered in the browser.

### 2.2 Save and share
Type a **Report name** and choose who can open it:

| Visibility | Who can run it |
|---|---|
| **Only me** | You. |
| **Selected users / groups** | You plus the users (active people who share a company with you) and groups you tick. |
| **All users** (public) | Everyone who may use the source, in every company. |
| **Standard report** *(superusers)* | Offered to every user who can use the source, **in every company**; only superusers can change or delete it. |

Saved reports appear on the Reports page under **Standard reports** or **My & shared reports**, with **Clone** (make your own private
copy) and **Delete** (yours only). Opening someone else's report and pressing Save stores *your own copy*. A report never grants
access: rows are always limited by the source's permission and your active company.

### 2.3 Export
Pick a format and press **Export**. Up to `CORE_EXPORT_SYNC_MAX_ROWS` (5,000) rows download immediately; larger exports run in the
background — download them from **Exports** (`/exports/`) when ready; files expire after `CORE_EXPORT_EXPIRY_HOURS` (24 h). Exports
above `CORE_EXPORT_MAX_ROWS` (100,000) are refused ("narrow the filter"); PDFs stop at 5,000 rows with a note. Spreadsheet cells that
would run as a formula (`=`, `+`, `-`, `@` …) are neutralised with a leading apostrophe.

Every list page also has its own Export menu (exactly the rows the list shows) and a Reports menu with the reports of that model.

### Company scope (which companies a report covers)

By default every report, query and export covers the **active company** only. A user whose profile has **Report shows all
companies** ticked (user form, *Reports* tab) gets a **Companies** control on the report builder, the query builder, custom
reports and the list pages' Export menu:

| Choice | Rows from |
| --- | --- |
| Active company | the active company (as before) |
| All my companies | every company the user belongs to; for a superuser every active company |
| Chosen companies | the companies picked, limited to the above (a company the user has left is dropped when the report runs) |

* The control starts on the user's default (profile) or, for a saved report, on the choice saved with it (*Remember the company
  choice* on Save). Each run can change it; the choice travels with the run (query string / form, background export jobs) and is
  re-checked on the server every time. With the profile checkbox off, every run uses the active company whatever is sent.
* When more than one company is in scope a **Company** column (abbreviation; the full name as a tooltip) is added to the preview
  and to every export. A grouped report then gets one row per company and group. Sources without `company_lookup` are unchanged;
  a source whose companies are many-valued (e.g. users: `profile__companies`) shows the column on plain rows only, not when grouped.
* Nothing in scope (no active company, no chosen company left) = no rows.
* Audit: `report_run` / export rows add `companies: All my companies (A, B)`; never row data.
* Code: `core.reports.scope` (`resolve_report_companies(user, mode=None, chosen_ids=None)`, `report_ctx(request, saved=None)`).

### 2.4 Build a custom report without code — worked example
*"Total order value per status, only for this year"* on a source `orders` that has `status` (groupable) and `total` (sum allowed):
1. Reports → New report from **Orders**.
2. Search bar: **+ Filter** → `Ordered on` · `year is` · `2026` → Search.
3. Group by: tick **Status**.
4. **+ Aggregate** → `sum` of **Total**; **+ Aggregate** → `count` of **(rows)**.
5. Preview → name it *Order value by status 2026* → visibility **All users** → Save. Export as XLSX or PDF any time.

---

### 2.5 Dates: daily, monthly and "from – to" reports
When the source has a `date_field`, the options panel has a date filter headed with that field's name (e.g. *Received on*):
**Any date / from – to** (two date boxes, either may stay empty), **One day**, **Today**, **Yesterday**, **This week**, **This month**,
**Last month**. Anyone who can open the report changes it and presses **Preview** or **Export** (it does not need the *+ Filter*
right); the owner's **Save** keeps it. Today / Yesterday / This week / This month / Last month are worked out on the day the report
runs, so a saved *Daily receive* with **Today** always shows today's rows (background exports: the day the job runs). Stored as
`parameters = {"date_preset": "today"}` (or `{"date_from": ..., "date_to": ...}`); a From after the To is refused.

### 2.6 Show a report in the menu
On the Save form, **Show in menu under** puts the report in the sidebar under a menu group (made once in the Menu builder, e.g.
`Inventory > Yarn`), with an optional **menu label** (default: the report name) and **position**. It shows to the people who may open
the report and use its source, so a private report is only in its owner's menu. Only people who may change the report see these
fields; the server checks the group again. A report in the menu has a ☰ icon on the Reports page. Full rules and the
*Inventory › Yarn / Yarn dyeing* recipe: [MENU_BUILDER.md](MENU_BUILDER.md) §4.1.

---

## 3. Advanced definition options

The builder screen covers columns, filters, group-by and aggregates. The engine also supports the options below; they are part of the
stored definition, are kept when a report is opened and re-saved in the builder, and can be set from code (e.g. when you ship
standard reports — see §3.2).

| Key | What it does | Limits |
|---|---|---|
| `calculated` | Extra columns computed from shown columns or numbers: `{"alias": "margin", "op": "sub", "left": "revenue", "right": "cost"}`. `op` = `add`, `sub`, `mul`, `div` (divide by zero gives an empty cell, never an error). Calculated columns can use earlier calculated columns. | 5 |
| `sorting` | `["-total", "status"]` — any shown column, aggregate or calculated alias. | 3 |
| `parameters` | `{"date_from": "2026-01-01", "date_to": "2026-03-31"}` — inclusive date range on the source's `date_field`; or `{"date_preset": "today"}` (`day` with `date_from`, `today`, `yesterday`, `this_week`, `this_month`, `last_month`), resolved when it runs (§2.5). | source must set `date_field` |
| `page_size` | Preview page size. | `CORE_MAX_PAGE_SIZE` |
| `chart` | `{"type": "bar", "x": "status", "y": ["value"], "stacked": false}` — `type` = bar, line, area, pie, doughnut; `x` any output column, `y` number columns. | 6 value columns |

Aliases (aggregates/calculated) must be lower-case letters, digits and `_`, and must not clash with a field of the model.

### 3.1 Full definition format
```json
{
  "columns": ["number", "customer_name", "total"],
  "filters": {"op": "AND", "items": [{"field": "status", "op": "in", "value": ["open", "late"]}]},
  "groupings": ["status"],
  "aggregates": [{"fn": "sum", "column": "total", "alias": "value"}, {"fn": "count", "column": "", "alias": "orders"}],
  "calculated": [{"alias": "avg_value", "op": "div", "left": "value", "right": "orders"}],
  "sorting": ["-value"],
  "parameters": {"date_from": "2026-01-01", "date_to": ""},
  "page_size": 50,
  "chart": {"type": "bar", "x": "status", "y": ["value"]}
}
```
When `groupings` or `aggregates` are present the output is one row per group (`groupings` + aggregate aliases); otherwise one row per
record (`columns`). Calculated columns are appended in both cases.

### 3.2 Shipping standard reports from code
```python
from core.models import ReportDefinition
from core.reports.builder import validate_definition
from core.reports.registry import get_report_source

spec = validate_definition(get_report_source("orders"), definition_dict)          # raises ReportError if invalid
ReportDefinition.objects.update_or_create(
    owner=admin_user, slug="order-value-by-status",
    defaults={"name": "Order value by status", "source_key": "orders", "is_system": True, "visibility": "public",
              "columns": spec.columns, "filters": spec.filters, "groupings": spec.groupings, "aggregates": spec.aggregates,
              "calculated": spec.calculated, "sorting": spec.sorting, "parameters": spec.parameters, "page_size": spec.page_size})
```
Run it from a data migration or a management command.

### 3.3 Running a report from code
```python
from core.reports.builder import build_queryset, validate_definition
from core.reports.registry import get_report_source
from core.reports.resources.export import ctx_for, export_report_bytes

source = get_report_source("orders")
spec = validate_definition(source, definition_dict)
ctx = ctx_for(user, company_id)                            # scope = that user's membership of that company
rows = build_queryset(source, spec, ctx).queryset           # an ORM values() queryset, scoped and permission-filtered
data, filename, content_type = export_report_bytes(source, spec, ctx, None, "xlsx")
```

---

## 4. What the builder guarantees
* Only registered columns/aggregates/filters can be used; everything else raises `ReportError` with a readable message.
* Queries are built with the ORM only (`values()` / `annotate()` / `filter()` / `order_by()`); there is no raw SQL path.
* The base queryset is limited to the run's companies (`company_lookup`; the active company unless the user's report company scope
  says otherwise, see 2.3) and filtered by the source's permission(s) (object-level grants count) before any user filter is applied.
* Previews and exports are audited as `report_run` events.
* Large exports run as background jobs owned by the user; the worker re-derives scope from the owner, not from stored ids (the
  stored company choice is resolved again against the owner's memberships and setting).

---

## 5. Extending: report sources (developers)

### 5.1 Register a source
Call from your app's `AppConfig.ready()`:
```python
from django.db.models import Count
from core.reports.registry import ReportColumn, register_report_source
from .models import Order

register_report_source(
    "orders", Order, label="Orders", description="Sales orders with customer and totals",
    columns=[
        ReportColumn("number", "Order no", "number", "text"),
        ReportColumn("customer_name", "Customer", "customer__name", "text", groupable=True, aggregates=("count", "count_distinct")),
        ReportColumn("status", "Status", "status", "choice", groupable=True, aggregates=("count",)),
        ReportColumn("ordered_on", "Ordered on", "ordered_on", "date", groupable=True),
        ReportColumn("total", "Total", "total", "number", aggregates=("sum", "avg", "min", "max")),
        ReportColumn("line_count", "Lines", "line_count", "number", aggregates=("sum", "avg")),   # an annotation, below
        "note",                                                                                     # a plain string = inferred
    ],
    annotations={"line_count": Count("lines")},               # computed per row; must be declared with an explicit ReportColumn
    queryset=lambda ctx: Order.objects.filter(is_cancel=False),   # optional base queryset (ctx = user / company)
    searchable_fields=("number",), filterable_fields=("status", "total", "ordered_on", "customer"),
    searchable_relations=("customer__name",), default_sort=("-ordered_on",),
    default_columns=("number", "customer_name", "total"),
    permission="sales.view_order",                            # default: <app>.view_<model>; () = any signed-in user
    date_field="ordered_on",                                  # enables the date_from / date_to parameters
    pdf_template=None,                                        # optional custom PDF body, see §7.4
)
```

| Argument | Meaning |
|---|---|
| `key` | Unique id, lower-case letters/digits/`-`/`_`. Appears in URLs (`/reports/builder/<key>/`). |
| `columns` | `ReportColumn`s or field paths (inferred: label from `verbose_name`, kind from the field, groupable for text/date/bool/choice/fk, aggregates by kind). Paths may follow forward relations (`customer__name`); to-many paths are refused. |
| `annotations` | `{alias: expression}` added to the base queryset; reference them as column paths. |
| `queryset(ctx)` | Base rows before scope/permission/filters are applied (e.g. exclude cancelled). |
| `searchable_fields`, `filterable_fields`, `searchable_relations`, `sortable_fields`, `default_sort` | The search allowlist for the builder's filter bar (same meaning as in `register_resource`). |
| `permission` | One codename or a list (all required). Default `<app>.view_<model>`. |
| `company_lookup` | ORM path to the company id. Default `"auto"` = the model's `company_lookup`; `None` = not company-scoped. |
| `date_field` | Field used by the `date_from`/`date_to` parameters. |
| `pdf_template` | `fn(buffer, title, columns, rows, meta)` to replace the default PDF body. |

`ReportColumn(name, label, path, kind, groupable=False, aggregates=(), image_route=())`:
* `kind`: `text`, `number`, `date`, `datetime`, `bool`, `choice`, `fk`, `uuid`.
* `aggregates`: any of `count`, `count_distinct`, `sum`, `avg`, `min`, `max` (`sum`/`avg` only make sense for numbers).
* `image_route=("url_name", "field")`: the cell is an image served by `reverse(url_name, args=[row pk, field])` (the URL must do its
  own permission check). Image columns cannot be grouped or aggregated; PDFs embed the image, other formats print *Yes*.

### 5.2 Automatic sources for every list page
With `CORE_AUTO_REPORT_SOURCES = True` (default), every registered CRUD resource that allows export and has no source of its own gets
one named `r-<slug>`: its list fields first, then other plain fields (FKs as `<fk>__name|title|code`), with the resource's own scoped
queryset and view permission. Register an explicit source for the model to replace it.

### 5.3 Add an aggregate function
Add it to `AGGREGATES` in `core/reports/registry/__init__.py` (`name -> callable(path) -> Django aggregate`), and to `NUMERIC_ONLY`
if it needs numbers. It becomes available to columns that list it in `aggregates=`.

---

## 6. Custom reports (code)

Use a custom report when the builder's "one source → rows/groups" model is not enough: a printable document for one record, data from
several models, business rules, a summary block plus a table plus totals plus signatures.

### 6.1 What users see
Custom reports the user is allowed to run appear on the Reports page under **Custom reports**. Each opens at
`/reports/custom/<key>/`: a **Parameters** form on the left, **Run** to preview (paged), and one download button per format (**PDF**,
**XLSX**, **CSV** by default). Parameters are in the URL, so a filled-in report can be bookmarked.

### 6.2 Write one
```python
# myapp/reports.py  (import it from AppConfig.ready())
from django import forms
from core.approvals.signatures import approval_signatures
from core.reports.custom import ReportOutput, register_custom_report
from .models import YarnWorkOrder


class WorkOrderParams(forms.Form):
    number = forms.IntegerField(label="Work order no")


def yarn_work_order(ctx, params):
    # 1) SCOPE YOUR QUERY: a custom report runs your code, so the registry cannot scope it for you.
    wo = YarnWorkOrder.objects.filter(company_id__in=ctx.company_ids, number=params["number"]).first()
    if wo is None:
        return ReportOutput(columns=[("job", "Job No")], rows=[], subtitle="No work order with that number.")
    rows = [{"no": n, "job": l.job_no, "order": l.order_no, "style": str(l.style), "buyer": l.buyer.name, "type": l.yarn_type,
             "uom": l.uom, "qty": l.qty, "price": l.price, "amount": l.qty * l.price}
            for n, l in enumerate(wo.lines.select_related("buyer", "style"), 1)]
    return ReportOutput(
        title=f"Yarn Work Order No - {wo.number}",
        notice="Work Order No Must be Mention in Proforma Invoice (PI) and Delivery Challan",
        info=[("WO Date", wo.date.strftime("%d-%b-%Y")), ("Bill To", wo.bill_to), ("Delivery Place", wo.delivery_place),
              ("WO For", wo.wo_for), ("Payment Term", wo.payment_term), ("Incoterm", wo.incoterm or "N/A"),
              ("Supplier", wo.supplier.name), ("Currency", wo.currency), ("Source", wo.source)],
        columns=[("no", "#"), ("job", "Job No"), ("order", "Order No"), ("style", "Style No"), ("buyer", "Buyer"),
                 ("type", "Yarn Type"), ("uom", "UOM"), ("qty", "WO Qty"), ("price", "WO Price"), ("amount", "Amount")],
        rows=rows, numeric=("qty", "price", "amount"),
        totals={"qty": sum(r["qty"] for r in rows), "amount": sum(r["amount"] for r in rows)},
        notes=[f"In Word : {wo.amount_in_words}", "Remarks & Terms and Condition", wo.remarks],
        signatures=approval_signatures(wo, labels={"check": "Checked By", "audit": "Audited By", "approve": "Approved By"}),
    )


register_custom_report("yarn-work-order", "Yarn work order", yarn_work_order, form=WorkOrderParams,
                       permission="purchase.view_yarnworkorder", category="Purchase",
                       description="Printable yarn work order with totals and approval signatures.")
```
The default PDF of that report is the layout of `work-order.pdf`: letterhead, red notice, three-column info block, table with a bold
*Total* row, notes, signature boxes at the bottom of the last page. A working, tested example lives in
`tests/dummyapp/registrations.py` (`garment-sheet`).

### 6.3 `register_custom_report(key, label, run, *, permission, ...)`
| Argument | Meaning |
|---|---|
| `key` | Unique, lower-case letters/digits/`-`/`_`; the URL is `/reports/custom/<key>/`. |
| `label`, `description`, `category` | Shown on the Reports page (sorted by category, then label). |
| `run(ctx, params)` | Your function. `ctx.user`, `ctx.company_id` (active company), `ctx.company_ids` (data scope), `ctx.member_ids`. `params` = the form's `cleaned_data` (`{}` without a form). Must return a `ReportOutput`. |
| `permission` | **Required** (fail closed): a codename, a list (all needed), or `()` for any signed-in user. Checked before `run` is called and to list the report. |
| `form` | A Django `Form` class for the parameters. Without one, the report runs as soon as it is opened. `DateField`s get a date picker. |
| `formats` | Default `("pdf", "xlsx", "csv")`; any tablib format extension, plus `pdf`. |
| `pdf_template(buffer, report, output, meta)` | Replace the PDF body (letterhead still available, §7.4). |
| `letterhead_class` | A `LetterheadTemplate` subclass (or dotted path) for this report only. |
| `portrait` | `True` for A4 portrait (default landscape). |
| `menu`, `menu_label`, `menu_position` | Show the report in the sidebar under a menu group: `menu="Inventory > Yarn"` (the group's titles from the top level, case-insensitive; the group is made in the Menu builder; nothing is shown while it does not exist). Seen by holders of `permission`. |

A form class with `takes_ctx = True` is built as `Form(data, ctx=ctx)`, so it can scope its choices to the run (`ctx.user`,
`ctx.company_ids`), as the stock ledger's item picker does.

### 6.4 `ReportOutput`
| Field | Meaning |
|---|---|
| `columns` | `[(key, label)]` in display order. **Required.** |
| `rows` | List of dicts keyed by column key. **Required.** Max `CORE_EXPORT_MAX_ROWS`. |
| `title` | Document title in the header (default: the report label). |
| `subtitle` | Small line under the header. |
| `notice` | Highlighted (red, bold, centred) line under the header. |
| `info` | `[(label, value)]` printed as a 3-column block above the table. |
| `totals` | `{column key: value}` → a bold *Total* row (also appended to spreadsheet exports). |
| `notes` | Paragraphs after the table (newlines kept). |
| `signatures` | `[Signature(label, name, when)]` printed at the bottom of the last page. |
| `numeric` | Column keys to right-align. |

### 6.5 Rules for `run`
* **Scope every query** to `ctx.company_ids` (or use `core.permissions.scope.scope_queryset(qs, lookup, ctx.company_ids)`), and if
  the data needs object-level rights use `core.permissions.filter_by_perm(ctx.user, perm, qs)`. The report's `permission` only
  decides who may *run* it.
* Raise `core.reports.builder.definition.ReportError("message")` for a user-facing problem; it is shown on the page.
* Keep it read-only. Each run is audited as a `report_run` event.
* Return plain values (str, numbers, dates) in rows; they are escaped in HTML and PDF and formula-neutralised in spreadsheets.

### 6.6 Approval signatures
`core.approvals.signatures.approval_signatures(obj, prepared_label="Prepared By", labels=None, include_pending=True)` reads the
record's latest approval request: *Prepared By* = the submitter, then one box per chain step that was not skipped (approver + time).
Steps not decided yet give an empty box to sign by hand (`include_pending=False` drops them). A record never submitted gives `[]`.

### 6.7 Stock ledger (`register_stock_ledger`)
A stock ledger needs an opening balance, every receipt and issue in date order and a running balance: the report builder cannot do it
(it has no running totals or opening rows). Core ships a custom report for it, over any movement model (one row per receipt or issue):

```python
# inventory/apps.py -> ready()
from core.reports.custom import register_stock_ledger
from .models import YarnMove

register_stock_ledger(
    "yarn-ledger", "Yarn stock ledger", YarnMove,
    item_field="yarn", date_field="moved_on", qty_in="qty_in", qty_out="qty_out",
    columns=(("challan_no", "Challan"), ("party__name", "Party")),      # extra columns per movement (optional)
    queryset=lambda ctx: YarnMove.objects.filter(section="yarn"),        # optional: narrow the rows (e.g. yarn vs dyed yarn)
    permission="inventory.view_yarnmove",                                  # default <app>.view_<model>
    category="Inventory", menu="Inventory > Yarn", menu_label="Stock ledger", menu_position=40)
```

* Parameters: **item** (only items with movements the user may see; *All* = every item with its own balances), **From** / **To**
  (default: this month). The companies are the run's company scope (the *Companies* control, §2.3); with several companies each item
  is balanced per company and a Company column is added.
* Rows per item: *Opening balance* (everything before From) · each movement with Received / Issued / Balance · *Closing balance*
  (with the period's received and issued totals). The total row adds up received, issued and the closing balances.
* Scope: company (`company_field`, default `company`), row-level access, then `permission` with object grants, like a report source.
  More than `CORE_EXPORT_MAX_ROWS` movements are refused ("narrow the parameters").
* For *Yarn dyeing*, register a second ledger (another key, another `queryset` or model, `menu="Inventory > Yarn dyeing"`).

---

## 7. PDF letterhead (header & footer template)

Every PDF — list exports, report-builder and query-builder exports, custom reports, record PDFs — is built by `core.reports.resources.pdf.build_branded()`, which
draws the letterhead on each page. Only the header and footer are templated; the body belongs to the report.

Default layout (from `work-order.pdf`):
* **Header** — logo top left; company name, address lines (map-pin icon), phone, e-mail and website (envelope/globe icons) top right;
  document title centred above a full-width rule.
* **Footer** — left: *Generated On 11 October, 2026 01:47:58 PM* / *Generated By: <user>*; right: *Page X of Y* /
  *This report is generated by <brand>*.
* **Signatures** (optional) — equal columns at the bottom of the last page (moved to a new page if they do not fit).

### 7.1 Where the details come from (the active company)
Every PDF — list exports, report builder, query builder, custom reports, one-record PDFs and background export jobs — prints the
**active company's** letterhead: the company the user is working in when the PDF is made (a background job stores that company
and uses it when it runs). The company supplies its **name, address, factory address, e-mail, phone, website and logo**
(Companies page). With the multi-company report scope (§2 *Company scope*) the data may cover several companies, but the header is
still the active company's. Nothing in the letterhead is cached: switch company (or edit it) and the next PDF shows the change.

For each field: the company's value → else the `CORE_PDF_LETTERHEAD` value → else empty. A missing or deleted company prints the
setting's values only. The built-in defaults (`DEFAULT_LETTERHEAD` in `core/reports/letterhead.py`) contain **no company details
and no logo**, so a reused `core` never prints anybody else's name.

```python
# settings.py -- optional; every key is optional and only fills fields the company leaves blank
CORE_PDF_LETTERHEAD = {
    "name": "",                                    # used when no company is active
    "address_lines": [],
    "phone": "",
    "email": "",
    "website": "",
    "logo": BASE_DIR / "branding" / "letterhead-logo.png",   # a file path (PNG/JPG) or bytes; "" = no logo
    "brand": None,                                 # "This report is generated by <brand>"; None = the company's name, "" = no line
    "from_company": True,                          # False: ignore the company and print only the values above
}
```
No logo is drawn by default. `branding/letterhead-logo.png` (project root, not part of `core`) is a sample logo; point `"logo"` at it
(or at your own file) if you want a logo for companies that have none.

### 7.2 Change the layout (one class)
Subclass `LetterheadTemplate` and point the setting at it:
```python
# myproject/pdf.py
from core.reports.letterhead import LetterheadTemplate

class MyLetterhead(LetterheadTemplate):
    header_height = 95                       # space reserved at the top (points)
    font, font_bold, font_italic = "DejaVu", "DejaVu-Bold", "DejaVu-Oblique"   # register TTFs first, see 7.5

    def draw_header(self, canvas, doc):
        super().draw_header(canvas, doc)     # or draw everything yourself
        canvas.setFont(self.font, 7)
        canvas.drawString(self.margin_x, canvas._pagesize[1] - self.header_height - 10, "ISO 9001 certified")

    def draw_footer(self, canvas, page, total):
        canvas.setFont(self.font, 8)
        canvas.drawCentredString(canvas._pagesize[0] / 2, 20, f"{self.letterhead.name} · page {page}/{total}")

# settings.py
CORE_PDF_LETTERHEAD_CLASS = "myproject.pdf.MyLetterhead"
```
What you can override:

| Member | Purpose |
|---|---|
| `margin_x`, `header_height`, `footer_height`, `signature_height` | Geometry (points). The body frame starts below `header_height` and ends above `footer_height`. |
| `font`, `font_bold`, `font_italic`, `text_color`, `icon_color` | Typography. |
| `generated_on_format`, `signature_date_format` | `None` (default) = site `CORE_DATETIME_FORMAT`; or a Django date pattern such as `"d/m/Y H:i"` (a `strftime` pattern with `%` still works). |
| `draw_header(canvas, doc)` | Called at the start of every page. `self.title`, `self.letterhead`, `self.meta` are available. |
| `draw_footer(canvas, page, total)` | Called once per page after the whole document is laid out (so `total` is known). |
| `draw_signatures(canvas, signatures, x, y, width)` | The signature row. |
| `draw_icon(canvas, kind, x, y, size)` | `pin`, `mail`, `globe`, `phone` vector icons. |

For a single report only, pass `letterhead_class=MyLetterhead` to `register_custom_report`, or put `"letterhead_class"` in a PDF's
`meta`.

### 7.3 A different letterhead per document
Pass a ready-made `Letterhead` in `meta["letterhead"]` (fields: `name`, `address_lines`, `email`, `website`, `phone`, `logo`,
`brand`), e.g. from a custom `pdf_template`. Otherwise the letterhead is `letterhead_for(meta["company_id"])`, and `pdf_meta(ctx)`
(`core/reports/resources/export.py`) sets `company_id` to the active company; build your own `meta` with it.

### 7.4 Custom PDF bodies keep the letterhead
```python
from reportlab.platypus import Paragraph, Spacer
from core.reports.resources.pdf import build_branded

def my_pdf(buffer, report, output, meta):                 # custom report pdf_template
    story = [Paragraph("Anything reportlab can draw", ...), Spacer(1, 12), ...]
    build_branded(buffer, output.title or report.label, story, meta)   # header, footer, page numbers, signatures

def my_source_pdf(buffer, title, columns, rows, meta):     # report-source pdf_template
    build_branded(buffer, title, [...], meta)
```
`meta` keys read by `build_branded`: `title`, `generated_by`, `generated_at`, `company_id`, `signatures`, `letterhead`,
`letterhead_class`, `pagesize` (default A4 landscape).

### 7.5 Non-Latin text
The default fonts are the PDF base fonts (Helvetica), which cover Latin-1 only. For Bengali, Arabic etc. register a TTF once (e.g. in
`AppConfig.ready()`) with `reportlab.pdfbase.pdfmetrics.registerFont(TTFont("DejaVu", path))` and set the template's `font*`
attributes; table bodies use reportlab paragraph styles, so set their `fontName` in your `pdf_template` too.

---

## 8. Settings
| Setting | Default | Meaning |
|---|---|---|
| `CORE_AUTO_REPORT_SOURCES` | `True` | Automatic `r-<slug>` sources for list pages. |
| `CORE_PREVIEW_ROWS` | 50 | Max rows per preview page. |
| `CORE_EXPORT_SYNC_MAX_ROWS` | 5,000 | Above this an export becomes a background job. |
| `CORE_EXPORT_MAX_ROWS` | 100,000 | Hard limit for exports and custom-report rows. |
| `CORE_EXPORT_EXPIRY_HOURS` | 24 | Background export files are deleted after this. |
| `CORE_PDF_LETTERHEAD` | `{}` | Fallbacks for letterhead fields the active company leaves blank; `from_company`, `brand` (§7.1). |
| `CORE_PDF_LETTERHEAD_CLASS` | `""` | Dotted path of the layout class (§7.2). |
| `CORE_CHART_MAX_POINTS` | 200 | Rows drawn in a report chart. |

## 9. File map
| File | Role |
|---|---|
| `core/reports/registry/__init__.py` | `register_report_source`, `ReportColumn`, aggregates, automatic sources |
| `core/reports/builder/definition.py` | Definition validation (`validate_definition`, `ReportSpec`, `ReportError`) |
| `core/reports/builder/run.py` | ORM compilation, preview, image cells |
| `core/reports/service.py` | Visibility, sharing, edit rights of saved reports |
| `core/reports/custom.py` | Custom reports: registry, `ReportOutput`, default PDF body, exports, `register_stock_ledger` |
| `core/reports/menu.py` | Reports in the menu: group picker (`group_choices`), Save-form check (`read_placement`), `find_group` for `menu=` |
| `core/menu/service.py` | `_reports_in_menu`: the per-user report entries merged into the sidebar |
| `core/reports/letterhead.py` | PDF header/footer/signature template and its data |
| `core/reports/resources/pdf.py` | `build_branded`, default table PDF |
| `core/reports/resources/export.py` | Export orchestration and background jobs |
| `core/approvals/signatures.py` | Signature boxes from approval history |
| `core/models/reports.py` | `ReportDefinition` (incl. `menu_parent`, `menu_label`, `menu_position`) |
| `core/views/reports.py` | Reports page, builder, saved-report actions, custom report page |
