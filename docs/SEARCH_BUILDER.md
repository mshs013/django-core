# Dynamic Search Builder

The search builder lets users find records on any list page with a quick text search, a visual filter builder (AND/OR groups,
typed operators) and saved searches that can be shared, made the default, or pinned to the menu. The same engine also powers the
report builder's filters, list exports, approval-step conditions and notification-rule conditions, so a filter means the same thing
everywhere.

Code: `core/search/` · views: `core/views/search.py` · templates: `core/templates/core/search/` · tests: `tests/test_search.py`

---

## 1. What it does

| Feature | Details |
|---|---|
| **Quick search** | One text box. Every word must match at least one *searchable* field (case-insensitive "contains"). Up to 8 words. |
| **Filter builder** | Rows of *field · operator · value*, combined with **AND** or **OR**, with one level of nested groups (e.g. `A AND (B OR C)`). The stored format can also negate a row (`"negate": true`). |
| **Typed operators** | The operators offered depend on the field type (text, number, date, choice, relation, …); see §4. |
| **Sorting & paging** | Click a column to sort (up to 3 sort keys); page size is capped by `CORE_MAX_PAGE_SIZE`. |
| **Saved searches** | Save the current quick text + filters + sort + page size under a name. Visibility: *Private*, *Company*, *Public*; administrators can also mark one *Standard (all users)*. |
| **Default / delete** | From the *Saved searches* dropdown: ★ makes a search your default for that page, × deletes it. |
| **Pin / clone / share** | Pin to the side menu, copy a visible search into your own, or change its visibility. These are POST endpoints (`/search/<id>/pin|clone|share/`) and service functions; the default list template has no buttons for them yet (see §6.4). |
| **Bookmarkable** | The whole state lives in the URL (`?q=…&adv=…&sort=…`), so a filtered list can be bookmarked or sent to a colleague (they still only see what they are allowed to see). |
| **Live preview** | While building, the result count and the first rows update without reloading the page. |
| **Exports match the list** | Export uses exactly the same search state as the list on screen. |

What it never does: it never lets a user name a field that was not registered, never builds raw SQL, and never shows rows outside the
user's active company or permissions — the search narrows the already-scoped queryset.

---

## 2. How to use it (end users)

### Quick search
Type in the **Search** box on a list page and press Enter. `blue polo` finds rows where *blue* matches some searchable field **and**
*polo* matches some searchable field. **Clear** removes the search.

### Building a filter
1. Click **+ Filter**. A row appears: pick a **field**, an **operator** and type or pick a **value**.
2. Add more rows. Set **Match** to **AND** (all rows must match) or **OR** (any row).
3. To mix AND and OR, add a group: inside a group the rows use the group's own AND/OR. Only one level of nesting is allowed.
4. Press **Search**. The preview under the builder shows how many rows match before you apply it.

Operator examples:

| You want | Field · operator · value |
|---|---|
| Price between 10 and 20 | `Price` · `between` · `10` and `20` (leave one side empty for "≥ 10" or "≤ 20") |
| Ordered in March 2026 | `Ordered on` · `year is` · `2026` **AND** `Ordered on` · `month is` · `3` |
| No fabric chosen | `Fabric` · `is empty` |
| Status Draft or Ready | `Status` · `is any of` · Draft, Ready |
| Created on a given day | `Created at` · `equals` · `2026-03-14` (a whole day, for date-time fields) |

### Saving and reusing a search
* **Save** (next to the builder) stores the current search: give it a **name** and choose **Private** (only you), **Company** (members
  of the active company) or **Public**. Superusers can also tick **Standard (all users)**: the search is then offered to everyone who
  can open that list, in every company.
* **Saved searches** (dropdown on the list page) lists your searches, the company's shared ones and the **Standard** ones (badge).
  Click a name to apply it; typing in the quick search on top of it narrows it further.
* In the same dropdown, **★** makes one of your searches the default (it opens automatically when you visit the page; add `?nosaved=1`
  to skip it) and **×** deletes it. Superusers also see × on Standard searches.

### Search builder page (Administration › Search builder, superusers)
Builds **standard searches** without opening each list:
* The right-hand list shows every list page and report that can be searched; pick one to open the editor.
* The editor sets a **name**, optional **quick text**, the **filters** (match all / any), the **sort** and the **rows per page**.
* **Apply by default to everyone** opens the page with this search for every user who has not chosen a default of their own (one
  default standard search per page).
* Saved searches appear in that page's **Saved searches** menu (badge *Standard*) for every user who can open the page, in every
  company. They can run them and page through the results, but cannot change or delete them.
* The builder's own list shows the standard searches with **Open**, **Edit** and **Delete**; one marked *needs fixing* uses a field
  that no longer exists.

Adding **+ Filter** rows on a list page that has a saved search loaded now works on top of that search: the builder starts from the
search's filters and your edited version is applied.

### Who can do what
| Action | Who |
|---|---|
| Quick search, sort, pick an offered saved search | Every user who can view the list |
| Build advanced filters, save/clone/share/pin searches | Superusers and users with permission **`core.build_search`** |
| Create/change **Standard** searches (on the list page or in Administration › Search builder) | Superusers only |

Users without `core.build_search` who open a URL containing an advanced filter get the list without it and a message telling them that
advanced filters are available through the saved searches offered to them.

---

## 3. The search state (URL format)

| Parameter | Meaning |
|---|---|
| `q` | Quick-search text (max `CORE_MAX_SEARCH_VALUE_LENGTH` = 200 characters). |
| `adv` | The filter tree as compact JSON (max 16,000 bytes). |
| `sort` | Comma-separated sort keys, `-` for descending: `sort=-price,name` (max 3; each must be sortable). |
| `page_size`, `page` | Paging (page size is clamped to 1…`CORE_MAX_PAGE_SIZE`). |
| `saved` | Id of a saved search to apply. |
| `nosaved` | Present = do not apply your default saved search. |

### Filter tree format
```json
{"op": "AND", "items": [
  {"field": "name", "op": "icontains", "value": "polo"},
  {"op": "OR", "items": [
    {"field": "price", "op": "range", "value": ["10", "20"]},
    {"field": "status", "op": "in", "value": ["draft", "ready"], "negate": true}
  ]}
]}
```
* A **leaf** is `{"field", "op", "value", "negate"?}`; a **group** is `{"op": "AND"|"OR", "items": [...]}`.
* `range` takes `[low, high]` (either may be empty); `in` takes a list (max 100 values); `isnull` takes `true`/`false`.
* `"is_null"` / `"not_null"` are accepted as aliases for `isnull` true/false.
* The same format is stored in `SavedSearch.definition`, `ReportDefinition.filters`, `ApprovalTypeStep.condition` and
  `NotificationRule.condition`. Admin forms for conditions also accept a single row, or a list of rows (all must match).

---

## 4. Field kinds and operators

The kind is detected from the model field; the operators below are the most that can be allowed (a registration may allow fewer).

| Kind | Model fields | Operators |
|---|---|---|
| `text` | CharField, TextField (+ Email/Slug/URL) | is (`iexact`), contains (`icontains`), starts with (`istartswith`), is empty (`isnull`) |
| `number` | Integer/Decimal/Float fields | equals, `>`, `≥`, `<`, `≤`, between (`range`), is empty |
| `date` | DateField | equals, `>`, `≥`, `<`, `≤`, year is, month is, between, is empty |
| `datetime` | DateTimeField | same as date; *equals* means "that whole day" |
| `choice` | any field with `choices` | equals, is any of (`in`), is empty |
| `bool` | BooleanField | equals |
| `fk` | ForeignKey / OneToOne | equals, is any of, is empty (value = related id) |
| `m2m` | ManyToMany / reverse relations | is any of, is empty |
| `uuid` | UUIDField | equals, is any of, is empty |

Other field types (files, JSON, binary…) cannot be filtered; registering one raises `ImproperlyConfigured` at startup.

---

## 5. Limits and safety

| Limit | Value / setting |
|---|---|
| Filter rows per search | `CORE_MAX_SEARCH_ROWS` (25) |
| Value length | `CORE_MAX_SEARCH_VALUE_LENGTH` (200) |
| Filter JSON size | 16,000 bytes |
| Nesting | one level of groups |
| Quick-search words | 8 |
| Sort keys | 3 |
| Values in an `in` row | 100 |

How it stays safe:
* **Allowlist only.** Field names, operators and sort keys are looked up in the registry; anything else is rejected with a readable
  `SearchError`. User text never becomes part of an ORM path.
* **Values go through django-filter.** Each leaf is executed by binding a generated `FilterSet` to exactly one filter, so values are
  parsed and type-checked by Django form fields, and LIKE wildcards are escaped by Django's lookups.
* **No duplicate rows.** Each leaf becomes `Q(pk__in=<subquery>)`, so filters across to-many relations never duplicate rows and no
  `distinct()` is needed.
* **Scope first.** The search is applied to a queryset that has already been limited to the active company and the user's permissions.
* **Re-validated on every load.** Saved searches are validated again each time they are offered; one that no longer fits the registry
  (e.g. a field was removed) is skipped and logged instead of breaking the page.
* **Audited.** Saving, updating, sharing, pinning and deleting saved searches write `saved_search` audit events.

---

## 6. How to extend it (developers)

### 6.1 Make a model searchable
Business apps normally never call the search registry directly: `register_resource` (list pages) and `register_report_source`
(reports) register a search target for you from these arguments. Call them from your app's `AppConfig.ready()`.

```python
# myapp/apps.py  ->  def ready(self): from . import registrations
from core.crud.registry import register_resource
from .models import Order

register_resource(
    Order, slug="orders",
    list_fields=("number", "customer", "total", "status", "ordered_on"),
    searchable_fields=("number", "note"),                    # quick search (text fields only)
    searchable_relations=("customer__name",),                # quick search AND filter through a relation (text, with '__')
    filterable_fields={                                       # builder fields; a list = all operators of the kind
        "status": None,                                       #   None/omitted = every operator allowed for the kind
        "total": ("gte", "lte", "range"),                     #   or restrict them
        "ordered_on": None,
        "customer": ("eq", "in"),
        "tags": None,                                         #   m2m works too
    },
    sortable_fields=("number", "total", "ordered_on"),       # default: the list fields that are real fields
    default_sort=("-ordered_on",),                           # must be sortable (or pk)
)
```
Mistakes fail at startup with `ImproperlyConfigured` (unknown path, non-text quick-search path, operator not valid for the kind,
default sort that is not sortable, a target registered twice).

**Columns from a related record, with your own heading, sortable.** A list column may be an ORM path; it shows that value, and
listing the same path in `sortable_fields` makes the heading clickable. `labels` renames any column or detail field (without it a
path is headed by its last field's name). The Users page does this (`core/bootstrap_registry.py`):
```python
list_fields=("email", "full_name", "profile__dept__name", "profile__designation__name", "profile__default_company__abbreviation"),
sortable_fields=("email", "full_name", "profile__dept__name", "profile__designation__name", "profile__default_company__abbreviation"),
labels={"profile__dept__name": "Department", "profile__designation__name": "Designation",
        "profile__default_company__abbreviation": "Company"},
```
Add the relations to `select_related` (a `get_queryset` hook) so the page does not query once per row.

### 6.2 Use the engine from your own code
```python
from core.search.registry import get_search
from core.search.builder.state import parse_state, apply_state, paginate
from core.search.builder.tree import validate_tree, compile_tree
from core.crud.access import scoped_queryset

spec = get_search("resource", "orders")                       # target types: "resource", "report_source"
state = parse_state(spec, request.GET)                        # strict=True raises SearchError instead of dropping bad parts
qs = apply_state(spec, scoped_queryset(cfg, ctx, action="view"), state)   # ALWAYS start from a scoped queryset
page = paginate(qs, state)

tree = validate_tree(spec, {"op": "AND", "items": [{"field": "status", "op": "eq", "value": "open"}]})
open_orders = Order.objects.filter(compile_tree(spec, tree))  # a Q object
```

### 6.3 Conditions ("only when …")
`core.search.conditions` lets any feature store a filter tree and later ask whether a record matches it — approval steps and
notification rules use it. The model must have a registered list page (its filterable fields are the vocabulary).
```python
from core.search import conditions
tree = conditions.validate(Order, {"field": "total", "op": "gte", "value": "10000"})   # normalise + validate + compile
if conditions.matches(order, tree):                                                   # evaluated in the database
    ...
```

### 6.4 Saved searches from code (and the endpoints)
`core.search.service` holds the rules (all raise `SearchError` with a user-safe message):
`can_build(user)`, `visible_to(user, target_type, key, company_id)`, `usable_searches(...)`, `validated_state(saved)`,
`save_search(user, company_id, target_type=, key=, name=, state=, visibility=, pinned=, is_default=, is_system=)`,
`set_default`, `share`, `pin`, `clone`, `delete_search`.

HTTP endpoints (POST, CSRF-protected, `next=` for the redirect):

| URL name | Path | Fields |
|---|---|---|
| `core:search_save` | `/search/save/` | `target_type`, `target_key`, `name`, `visibility`, `q`, `adv`, `sort`, `page_size`, optional `id` (update), `is_default`, `pinned`, `is_system` |
| `core:search_action` | `/search/<id>/<action>/` | `action` = `default` · `pin` · `clone` · `share` · `delete`; `value=1/0` for default/pin; `visibility` for share |
| `core:search_preview` | `/search/preview/` | same fields as save; returns the live-preview fragment |

To expose pin / clone / share to users, add buttons to `core/templates/core/search/_saved_dropdown.html` posting to
`core:search_action` (the server-side checks are already in place).

### 6.5 A new search target type
For a page that is neither a CRUD list nor a report, register your own target and look it up the same way:
```python
from core.search.registry import register_search
register_search(target_type="dashboard", key="late-orders", model=Order, searchable_fields=("number",),
                filterable_fields=("status", "due_on"), sortable_fields=("due_on",), default_sort=("due_on",))
```
Note: the saved-search views (`core/views/search.py`, `_may_use_target`) only know `resource` and `report_source`; add a branch there
that checks the user may open your page before saved searches can be stored for a new target type.

### 6.6 A new operator
1. Add it to `OPERATORS[kind]`, `OPERATOR_LABELS` and (if it maps to a Django lookup) `LOOKUPS` in `core/search/registry/__init__.py`.
2. Return a django-filter filter for it in `_filter_for()` in `core/search/filters/__init__.py`.
3. If it needs special value handling (like `range` or `in`), extend `_validate_leaf()` and `compile_leaf()` in `core/search/builder/tree.py`.
4. Add a test in `tests/test_search.py`.

### 6.7 A new field kind
Extend `kind_of()` in `core/search/registry/__init__.py` to map the model field class to a kind, give the kind an `OPERATORS` entry,
and teach `_filter_for()` which django-filter class parses its values.

---

## 7. Troubleshooting

| Message | Cause |
|---|---|
| `Unknown filter field: 'x'` | The field is not in `filterable_fields`/`searchable_relations` of that page (or a saved search predates a registry change). |
| `Operator 'gt' is not allowed for Name` | The operator is not allowed for that field's kind or was restricted in the registration. |
| `Groups can be nested one level only.` | A group inside a group inside the root. Flatten it. |
| `Too many filter rows.` | More than `CORE_MAX_SEARCH_ROWS` rows. |
| `Cannot sort by 'x'` | Not in `sortable_fields`. |
| `You can use the saved searches offered to you, but not build or change searches.` | The user lacks `core.build_search`. |

## 8. File map
| File | Role |
|---|---|
| `core/search/registry/__init__.py` | `register_search`, `get_search`, field kinds, operators, path resolution |
| `core/search/filters/__init__.py` | Generated django-filter `FilterSet` per target |
| `core/search/builder/tree.py` | Filter-tree validation and compilation to `Q` |
| `core/search/builder/state.py` | URL state, quick search, ordering, paging |
| `core/search/conditions.py` | Stored conditions for approvals / notification rules |
| `core/search/service.py` | Saved-search rules (visibility, default, pin, clone, share, audit) |
| `core/models/search.py` | `SavedSearch` |
| `core/views/search.py` | Save / action / live-preview endpoints |
| `core/views/search_builder.py`, `core/templates/core/search/builder_*.html` | Administration › Search builder (standard searches) |
