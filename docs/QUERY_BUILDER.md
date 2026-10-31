# Query builder (custom report builder)

Users build their own queries in the browser, without writing SQL:

* pick a **root model** (the `FROM` table)
* **join related models** through ForeignKey / OneToOne / ManyToMany relations, in both directions (`LEFT JOIN`)
* choose **display columns**, optionally through a **function** (`DATE()`, `HOUR()`, `MONTH()`, text before/after a separator ...)
* **filter** with `=`, `≠`, `IN`, `NOT IN`, `BETWEEN`, `NOT BETWEEN`, `>`, `≥`, `<`, `≤`, contains, starts/ends with, is empty,
  *in the last N days / months*, grouped with AND / OR
* **group and aggregate**: `COUNT`, `COUNT DISTINCT`, `SUM`, `AVG`, `MIN`, `MAX`, aggregates **"only where"** a condition holds
  (`MAX(CASE WHEN … THEN … END)`), and **CASE** columns
* filter the grouped result (**HAVING**), **sort**, **limit** (top N), **distinct**
* draw a **chart**, **save** and **share** the query, **export** it (CSV, XLSX, XLS, ODS, TSV, JSON, YAML, HTML, PDF with letterhead)

Everything is compiled to the Django ORM with bound parameters. There is no raw SQL anywhere, and every field, function and
operator is checked against an allowlist on the server each time a query runs.

Code: `core/reports/query/` (registry, catalogue, engine, service) · views: `core/views/query.py` · templates:
`core/templates/core/reports/query_*.html` · JS: `core/static/core/js/query.js`, `charts.js` · tests: `tests/test_query_builder.py`

---

## 1. Using it

**Reports → Query builder** (`/reports/query/`) lists the tables you may query and your saved queries.

The page is laid out like the app itself: the **results** (summary line, **Run**, **Export**, chart and table) fill the middle,
and the **editor** is a sidebar on the right. Its icon rail (**Joins · Columns · Filters · Group · Sort · Chart · Save**, with counts)
picks the section shown in the pane next to it; clicking the active icon again, or **Hide** at the bottom of the rail, folds the
pane away so the results get the full width (remembered per browser). **Save** holds the report name, who can use it and the
Save button. The pane scrolls on its own and stays in view while the results scroll. On narrow screens the editor sits above
the results with the rail as a row of icons. The left menu can also be folded to its icons with the
sidebar button at the far left of the top bar.

1. **Choose a table.** This is the root of the query. Every row it returns is already limited to your active company and to
   what you may see on its list page (including object access and user access, see [ACCESS_CONTROL.md](ACCESS_CONTROL.md)).
   **Joins** tab: **+ Join table** adds a related table (any table reachable through relationships, e.g. *Garments › Fabric*,
   *Garments › Garment lines (many)*) with a join type:
   * **LEFT** keeps every row even when there is no related row (its columns are empty).
   * **INNER** keeps only rows that have a related row.

   The field pickers then offer the root table and the joined tables only. A query opened from before joins existed gets its joins
   from the fields it already uses (all LEFT). If a column or filter uses a table that is not joined, the Joins tab lists it under
   *Used but not joined* with a **join them** link, and Run explains which table to join.
2. **Columns** (one entry per column: the field on the first line, then function · aggregate · name; the ⚙ button opens the rarely used options
   (heading, "only where", CASE) underneath, and a dot on ⚙ shows that something is set there):
   * **+ Field**: pick a field. The picker lists the table's own fields and, under headings such as *Garments › Fabric*, the
     fields of the joined tables.
   * **Function** (optional, depends on the field type): Date, Year, Quarter, Month, Week, Weekday, Day, Hour, Minute,
     Month/Year (first day), Upper/Lower case, Trim, Length, Text before… / Text after… a separator, As number, Absolute value,
     Round to… decimals.
   * **Aggregate** (optional): Count, Count distinct, Sum, Average, Minimum, Maximum. As soon as one column is aggregated, the
     query is **grouped**: every column *without* an aggregate becomes a GROUP BY column (like SQL).
   * **only where…** (aggregated columns): the aggregate only uses rows matching a condition, e.g. *Maximum of value only where
     data type = 190*.
   * **+ Count rows**: `COUNT(*)`.
   * **+ CASE value**: *when <condition> then <value> else <value>*, which can itself be aggregated.
   * **name** is the column's short name (used by sort, result filters and the chart); **heading** is the column title. Both are
     filled in automatically when left empty.
3. **Filters**: add conditions; *match all (AND)* or *any (OR)*; **+ Group** adds a bracketed group with its own AND/OR.
   For *is any of* type values separated by commas (or pick several for choice fields). A date given for a date-time field
   means that whole day.
4. **Group & HAVING** (shown for grouped queries): lists the GROUP BY columns and the result filters, e.g. *rows > 1*.
5. **Sort & limit**: sort by any column; *distinct rows*; *Top N rows*.
6. **Chart**: Bar, Line, Area, Pie or Doughnut; labels from one column, values from one or more number columns.
7. **Run** shows the chart and the first page of results. **Export** downloads the whole result in the chosen format.
   **Save** stores it under a name with the same sharing choices as other reports (only me, selected users / groups,
   all users; superusers can also make it a *standard* report).


---

## 2. Your SQL, as builder queries

The examples from the requirements, assuming the business models are registered (section 4). Field names follow the models.

### 2.1 Work order body pivot (`MAX(CASE WHEN …)`, `SUBSTRING_INDEX`, `GROUP BY`)

Root: **Wo body** (`wo_body`, with `wo` → `wo_head`).

| Builder column | SQL |
|---|---|
| Field `Wo › Id`, name `wo_id` | `a.wo_id` |
| Field `Wo › Wo order type` | `b.wo_order_type` |
| Field `Row num` | `a.row_num` |
| Field `Wo › Is confirm`, `Wo › Is approval` | `b.is_confirm, b.is_approval` |
| Field `Value`, aggregate **Maximum**, only where `Data type = 190`, name `job_id` | `MAX(CASE WHEN a.data_type_id = 190 THEN a.value END) AS job_id` |
| Field `Value`, function **Text before…** `,`, aggregate **Maximum**, only where `Data type = 191`, name `inquery_id` | `MAX(CASE WHEN … = 191 THEN SUBSTRING_INDEX(a.value, ',', 1) END)` |
| Field `Value`, function **Text after…** `,`, aggregate **Maximum**, only where `Data type = 191`, name `po_id` | the `REPLACE(…)` part: text after the first comma, empty when there is none |
| … one column per data type (192, 193, …, 273) … | |
| **CASE value**: when `Wo › Wo order type` *is any of* `0, 1, 4, 5, 13, 14, 15` then `2` else `1`, aggregate **Maximum**, name `inquery_type` | `MAX(CASE WHEN FIND_IN_SET(…) THEN 2 ELSE 1 END)` |
| Field `Created by` (Maximum) | `a.created_by` |
| Field `Created at`, function **Date** (Maximum), name `wo_date` | `DATE(a.created_at) AS wo_date` |

Filters (all): `Wo › Wo type = 12`, `Wo › Parent` *is empty*, `Data type` *is any of* `190, 191, …, 273`, `Created at`
*in the last 3 months*. Soft-deleted rows (`deleted_at IS NULL`) are left out automatically, for the root and for every
joined table. Sort: `wo_id` descending.

Note: SQL `GROUP BY a.wo_id, a.row_num` lets MySQL return columns that are not grouped. In the builder every non-aggregated
column is grouped, so give columns like *Created by* and *wo_date* an aggregate (Maximum) to keep one row per
`wo_id, row_num`, exactly like the SQL.

### 2.2 Overtime by unit (joins + `IN`, `!=`, `BETWEEN`)

Root: **Employee daily attendances**. Columns: `Employee › Employee code`, `Attend date`, `Ot hours`. Filters:
`Employee › Official info › Unit` *is any of* `5, 8`; `Ot hours > 3`; `Employee › Official info › Section ≠ 53`;
`Attend date` *between* `2019-06-01` and `2019-06-30`.

### 2.3 Employees leaving per hour (`HOUR()`, `COUNT(DISTINCT)`, `HAVING`)

Root: **Employee daily attendances**. Columns: `Out time` with function **Hour**, name `hr`; `Employee` with aggregate
**Count distinct**. Filters: `Attend date = 2020-10-25`; `Out time` *is not empty*. Result filter: `hr > 17`.
(`Out time` must be a time or date-time field; a time stored as text such as `'05:30 PM'` cannot be read as an hour.)

### 2.4 Duplicate attendance (`GROUP BY … HAVING COUNT(…) > 1`)

Root: **Employee daily attendances**. Columns: `Attend date`, `Employee`, `Attend date` with aggregate **Count**, name `n`.
Filter: `Attend date` *between* `2023-02-01` and `2023-02-28`. Result filter: `n > 1`.

---

## 3. Security rules

* **Tables**: only registered query models (section 4). The root must be allowed by the user's view permission; its rows come
  from the same scoped queryset as its list page (active company, object permissions, row hooks).
* **Joins**: only to registered, *joinable* models the user may view. Joined rows are limited to the active company and to
  rows that are not soft-deleted. A root row whose related row is missing is kept, like a LEFT JOIN. If the related rows of a
  root row all belong to another company, that root row is left out.
* **Fields**: fields with secret-looking names (`password`, `token`, `secret`, `hash`, `key` …), file, binary and JSON fields are
  never offered, and models can exclude more (`exclude_fields`).
* Owner-only resources (notifications, export jobs) and the cross-company audit log are not offered (they rely on rules a join
  cannot repeat).
* Depth: at most `CORE_QUERY_MAX_DEPTH` joins (default 3). Previews are paginated; exports are capped by
  `CORE_EXPORT_MAX_ROWS` (PDF: `CORE_PDF_MAX_ROWS`); charts use the first `CORE_CHART_MAX_POINTS` rows.
* A query the database cannot run (e.g. **As number** on text that is not a number, on PostgreSQL) shows a message instead
  of an error page. Every preview and export is written to the audit log as a *report run*, with the company scope used.
* **Companies**: the active company, unless the user's profile has *Report shows all companies* ticked; then the **Companies**
  control next to *Run* / *Export* picks the active company, all the user's companies or chosen ones (default: the user's setting,
  or the saved query's own choice). Root rows *and* joined rows are limited to that set. With more than one company a **Company**
  column (abbreviation) comes first: a plain column for a single company field, one row per company when grouped; for a many-valued
  company path (users) it is filled per row and left out when grouped or with *distinct rows only*. Details: REPORT_BUILDER.md 2.3.
* Saved queries store only JSON. It is validated again every time it is opened or run, so a query saved before a field was
  removed or a permission was revoked shows a message instead of data.
* **Menu**: the *Save* tab has **Show in menu under** (a menu group such as `Inventory > Yarn`), a menu label (default: the query
  name) and a position. The query then shows in the sidebar to the people who may open it and use its root table; the link opens
  the saved query (`/reports/query/<root>/?q=<id>`). Only people who may change the query get these fields; the server checks the
  group. Rules and the *Inventory › Yarn* recipe: MENU_BUILDER.md §4.1.
* **Dates at run time**: filters (including date filters such as *between*) can be changed by anyone who opens a saved query and
  then *Run*; they are fixed values. A "today" that moves with the calendar is a report-builder feature (REPORT_BUILDER.md §2.5).

---

## 4. Making models available (developers)

Every CRUD resource (`register_resource`) is available automatically (turn this off with `CORE_QUERY_AUTO_RESOURCES = False`).
Detail tables that have no list page of their own, such as `wo_body` or attendance rows, are registered explicitly from the
app's `AppConfig.ready()`:

```python
from core.reports.query import register_query_model

register_query_model(WoBody, label="Work order lines")                        # view permission: <app>.view_wobody
register_query_model(EmployeeOfficialInfo, exclude_fields=("salary",), root=False)   # joinable, not offered as a root
register_query_model(DailyAttendance, permission="hrm.view_attendance",
                     queryset=lambda ctx: DailyAttendance.objects.filter(unit__company_id__in=ctx.company_ids))
```

| Option | Default | Meaning |
|---|---|---|
| `label` | the model's plural name | Shown in the table list and as the group heading of its fields. |
| `permission` | `<app>.view_<model>` | Needed to use the model as root or to join it. Required (fail closed). |
| `company_lookup` | the model's `company_lookup` | ORM path to the company id(s) used to scope root and joined rows (`"company"`, `"companies"`, `"order__company"` …). |
| `exclude_fields` | `()` | Extra fields never offered. |
| `queryset(ctx)` | default manager + company scope + permission | Root rows; when given it must apply the company scope itself. |
| `joinable` / `root` | `True` / `True` | Whether it may be reached through a relation / chosen as the root. |

Run a query from code (e.g. a scheduled e-mail):

```python
from core.reports.query import get_query_model, validate, build, export_bytes

root = get_query_model("knitting.wobody")
spec = validate(root, user, saved.definition)          # raises QueryError with a readable message
rows = build(spec, ctx).rows(0, 100)                   # list of dicts keyed by column name
data, filename, content_type = export_bytes(spec, ctx, "xlsx", title="Work orders")
```

The definition format (what the page saves) is documented at the top of `core/reports/query/engine.py`.

### Extending

* **A new function**: add it to `FUNCTIONS` in `engine.py` (label, field kinds, result kind, needs argument) and build its Django
  expression in `_expr()`. Date-part functions that are Django lookups can also be offered in filters via `FILTER_FUNCTIONS`.
* **A new operator**: add it to `OPERATORS` / `OPERATOR_LABELS` and compile it in `_leaf_q()`; the page picks it up automatically.
* **A new aggregate**: add it to `AGGREGATES` and to `_aggregate()`.
* **Limits**: `CORE_QUERY_MAX_DEPTH`, `CORE_QUERY_MAX_FIELDS`, `CORE_CHART_MAX_POINTS`, `MAX_COLUMNS` / `MAX_FILTERS` in `engine.py`.

### Settings

| Setting | Default | |
|---|---|---|
| `CORE_QUERY_AUTO_RESOURCES` | `True` | Offer every plain CRUD resource. |
| `CORE_QUERY_MAX_DEPTH` | `3` | Joins from the root (max 5). |
| `CORE_QUERY_MAX_FIELDS` | `1500` | Fields listed in the pickers. |
| `CORE_CHART_MAX_POINTS` | `200` | Rows drawn in a chart. |

### Data model and URLs

`QueryReport`: `name`, `slug`, `root` (`app_label.model_name`), `owner`, `visibility`, `shared_users`, `shared_groups`,
`is_system`, `description`, `definition` (JSON).

| Name | Path |
|---|---|
| `core:query_start` | `/reports/query/` |
| `core:query_builder` | `/reports/query/<app_label.model_name>/` (`?q=<id>` opens a saved query; POST `action=preview|save|export`) |
| `core:query_report_action` | `/reports/query/saved/<id>/<clone|delete>/` |
