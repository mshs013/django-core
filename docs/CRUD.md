# CRUD engine (`core.crud`)

One `register_resource(...)` call turns a Django model into a complete screen set: list page (columns, quick search, advanced
filters, sorting, paging, export), modal create / view / edit / delete, trash and restore (soft-delete models), inline child tables,
import, bulk actions, company scope, permissions and audit. This guide shows how to use it, how to choose the list columns and the
search fields, and how to extend it.

Code: `core/crud/registry.py` (the options), `core/crud/forms.py` + `formsets.py` (forms), `core/crud/service.py` (delete, bulk
actions, display), `core/crud/access.py` (scope), `core/views/crud.py` (views), templates in `core/templates/core/crud/`.
Verified by reading the code; the Django-dependent tests (`tests/test_crud.py`) were **not run** in the build sandbox.

## 1. Quick start

```python
# yourapp/apps.py
from django.apps import AppConfig


class YourAppConfig(AppConfig):
    name = "yourapp"

    def ready(self):
        from core.crud.registry import register_resource
        from .models import Fabric

        register_resource(
            Fabric,
            slug="fabrics",                                    # URL: /r/fabrics/   (default: "<app>-<model>")
            list_fields=("name", "composition", "is_active"),  # columns on the list page
            form_fields=("name", "composition", "is_active"),  # fields in the create/edit form
            searchable_fields=("name", "composition"),         # quick-search box looks in these (text fields)
            filterable_fields=("name", "is_active", "created_at"),   # fields offered in the advanced filter
        )
```

That is all. You get these URLs (names in the `core:` namespace):

| URL | Name | Purpose |
|---|---|---|
| `/r/<slug>/` | `resource_list` | list page (also bulk actions via POST) |
| `/r/<slug>/new/` | `resource_new` | create form |
| `/r/<slug>/<pk>/` | `resource_detail` | detail / view |
| `/r/<slug>/<pk>/edit/` | `resource_edit` | edit form |
| `/r/<slug>/<pk>/delete/` | `resource_delete` | confirm and delete |
| `/r/<slug>/trash/`, `/<pk>/restore/` | `resource_trash`, `resource_restore` | soft-deleted rows |
| `/r/<slug>/export/<ext>/`, `/import/` | `resource_export`, `resource_import` | export / import |

On the list page the buttons open **modals** (create, view, edit, delete); no separate pages are needed. Put the list in the sidebar
from **Menu** (Administration): use the URL name `core:resource_list` with the slug.

Rules that always apply: a user needs the model's `view` / `add` / `change` / `delete` permission, rows are limited to the active
company, out-of-scope records answer 404, and an in-scope record without permission answers 403.

## 2. Columns on the list page: `list_fields`

`list_fields` is an ordered tuple; each entry becomes one column, in that order, with the header taken from the field's
`verbose_name`.

| Entry | What is shown |
|---|---|
| `"name"` (any model field) | the value; choices show their label; booleans show a Yes/No badge; dates use the site format (`CORE_DATE_FORMAT`); files show the file name |
| `"fabric"` (a ForeignKey) | the related object's `str()` (the list is loaded with `select_related`, so no extra queries) |
| `"tags"` (many-to-many) | up to 10 related objects, comma separated |
| `"__str__"` | the object's `__str__()`; header is the model's name |
| `"total_price"` (a **property or method** of the model) | its value. Give it a header with `short_description` (below) |

```python
class Garment(models.Model):
    name = models.CharField(max_length=80)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    fabric = models.ForeignKey("Fabric", on_delete=models.PROTECT)

    @property
    def fabric_name(self):                 # a value from a related table: use a property
        return self.fabric.name
    fabric_name.fget.short_description = "Fabric"    # the column header
```

Then `list_fields=("name", "fabric_name", "price")`.

Things to know:

* A double-underscore path such as `"fabric__name"` is **not** supported as a list column; use a property as above.
* A field name that does not exist is rejected when the app starts (`ImproperlyConfigured`), so typos never reach users.
* Without `list_fields` the first six editable fields are used.
* `sortable_fields` (default: the plain model fields in `list_fields`) controls which headers are clickable for sorting;
  `default_sort=("-created_at",)` sets the initial order (every entry must also be in `sortable_fields`, or be `pk`).
* `page_size=25` is the rows per page; users can choose up to `CORE_MAX_PAGE_SIZE`.
* `date_formats={"date": "d/m/Y"}` changes the date format for this page only.
* The detail view shows `detail_fields` (default: every editable field); `readonly_fields` adds non-editable values to it.

## 3. Search fields

There are two search tools on every list page, both built from allowlists you declare (a user can never search a field you did not list):

| Option | Powers | Rules |
|---|---|---|
| `searchable_fields=("name", "style_no")` | the **quick search** box | **text** fields only (CharField, TextField, Email ...). Each word the user types must match (contains) in at least one of these fields |
| `searchable_relations=("fabric__name",)` | quick search inside related tables | a related path ending in a text field (`relation__field`). It is also offered in the advanced filter |
| `filterable_fields=("status", "price", "ordered_on", "fabric")` | the **advanced filter** (field, operator, value rows with AND / OR groups) | any model field; the operators offered depend on the type (text, number, date, choice, yes/no, relation) |

`filterable_fields` can also limit the operators per field:

```python
filterable_fields={"name": ["icontains", "istartswith"], "price": ["gte", "lte", "range"], "status": ["eq", "in"]}
```

Operator names are listed in `docs/SEARCH_BUILDER.md`. Users with the **`core.build_search`** permission can also save a search
and share it; everyone else filters through the searches offered to them. The search text, filters, sort and page size all live in
the URL, so a result page can be bookmarked. Exports (CSV, XLSX, PDF ...) use the same search the user is looking at.

## 4. The form

```python
register_resource(
    Garment, slug="garments",
    form_fields=("name", "style_no", "price", "status", "fabric", "tags"),
    layout=(("name", "style_no"), ("price", "status"), ("fabric",), ("tags",)),    # rows of fields; each row is a Bootstrap row
    tabs=(("Basic", ("name", "style_no", "price")), ("Details", ("status", "fabric", "tags", "@lines"))),
    widgets={"notes": forms.Textarea(attrs={"rows": 3})},                         # Django widgets by field name
    m2m_widgets={"tags": "checkbox"},                                             # many-to-many as a tick list ("select" is the default)
    readonly_fields=("created_at",),
)
```

* `form_fields` empty = every editable field. Always set it for forms with sensitive fields.
* The **company** field is never shown; it is set from the active company automatically.
* `tabs` split a long form. A tab entry is a field name, or `"@<inline slug>"` to put an inline table in that tab. Anything you do not
  name goes on the first tab. A tab with an error opens first and shows a red dot.
* Use `form_class=your_factory` for a completely custom form (see `core/crud/userform.py` for the user + profile example).
* Image/file fields are stored privately and served only by authorised views.

### Inline child tables (order lines, sizes ...)

```python
from core.crud.registry import Inline

register_resource(
    Garment, slug="garments",
    inlines=(
        Inline(GarmentLine, slug="lines", label="Line", fields=("description", "qty", "position"),
               position_field="position", min_num=1, extra=1, can_delete=True,
               nested=(Inline(LineOption, slug="options", label="Option", fields=("label", "position")),)),
    ),
)
```

The children are saved with the parent in one transaction; rows can be added, removed and re-ordered in the form, and `nested=` allows
grandchildren at any depth. `kind="through"` edits a many-to-many through model (extra columns like quantity); `kind="generic"` edits
a `GenericRelation`. Children are checked against the child model's own add/change/delete permissions.

## 5. Permissions, scope and lock rules

| Option | Meaning |
|---|---|
| `permissions={"view": "yourapp.view_report"}` | use a different permission for an action. `None` means "any signed-in user" (only for data you scope yourself) |
| `company_field="auto"` | `"company"` (one company), `"companies"` (several), any ORM path ending in a company id, or `None` for no company scope. `"auto"` reads the model's `company_lookup` |
| `owner_field="created_by"` | row-level: users see their own rows plus those of the users assigned to them (see `docs/ACCESS_CONTROL.md`) |
| `disabled_actions=("add", "delete")` | make the page read-only for those actions |
| `allow_export=True`, `allow_import=False` | export buttons; import needs add + change permission and shows a dry-run preview first |
| `superuser_unscoped=True` | only for core's own cross-company pages (audit, jobs). Do not use it in business apps |

Soft-delete models (`CompanyScopedSoftDeleteModel`) move to the trash and can be restored. A record is **locked** (no edit/delete)
while it is waiting for approval or approved (`docs/APPROVALS.md`).

## 6. Hooks (extend without changing core)

```python
def only_open(qs, ctx):                     # ctx.user, ctx.company_id, ctx.company_ids
    return qs.filter(status="open")

def stamp(obj, ctx):
    obj.updated_note = f"saved by {ctx.user}"

register_resource(
    Order, slug="orders",
    hooks={
        "get_queryset": only_open,          # (queryset, ctx) -> queryset, applied before company scope and permission filters
        "before_save": stamp,               # (obj, ctx) inside the save transaction, before the object is written
        "after_save": lambda obj, ctx: None,        # (obj, ctx) after the object and its inlines are saved
        "delete_guard": check_can_delete,   # (obj, ctx) raise core.crud.service.CrudError("friendly message") to refuse
        "before_delete": lambda obj, ctx: None,
        "after_delete": lambda obj, ctx: None,
        "detail_extra": lambda obj, request: [("Customer total", "1,200.00", None, False)],   # extra rows on the detail view
    },
)
```

`get_queryset` is part of the security boundary: it can only narrow what the scope already allows, and a resource that has one is
not offered automatically in the query builder.

### Bulk actions

```python
def mark_ready(queryset, ctx):
    n = queryset.update(status="ready")
    return f"{n} marked ready."              # shown as the success message

register_resource(Garment, slug="garments", actions={"mark_ready": mark_ready})
```

`queryset` is already limited to rows the user may **change** in the active company. The built-in `delete` action is always there
(unless `delete` is disabled). At most 500 rows are sent per action.

### Templates

`templates={"list": "yourapp/garment_list.html"}` replaces a template. Keys: `list`, `detail`, `form`, `confirm`,
`modal_detail`, `modal_form`, `modal_confirm`. Extend the original (`{% extends "core/crud/list.html" %}`) and override a block, so
you keep future fixes.

## 7. What you get automatically

* A report source (`r-<slug>`) for the report builder, a model in the query builder and a search target, built from the same fields.
* Audit trail (if the model inherits `AuditTrackedModel`), notification rules and approvals (inherit `AuditedModel` or bind an
  approval type in the app).
* Menu entry: add it in **Menu**; set `menu=False` if the resource should not be offered there.

## 8. Checklist for a new resource

1. Model inherits the right base (`CompanyScopedSoftDeleteModel`, `AuditedModel` ...).
2. `register_resource(...)` in `AppConfig.ready()` with `list_fields`, `form_fields`, `searchable_fields`, `filterable_fields`.
3. `makemigrations` and `migrate`; give roles the `view/add/change/delete` permissions.
4. `manage.py seed_url_permissions --dry-run` (new named URLs) and add the page to the **Menu**.
5. Open `/r/<slug>/`: check the columns, the quick search and the advanced filter, create / edit / delete in the modal.

## 9. Troubleshooting

| Message / symptom | Cause |
|---|---|
| `ImproperlyConfigured: ... is not a field` at start-up | a name in `list_fields`, `form_fields` ... does not exist on the model |
| `... quick-search path 'x' must be a text field` | `searchable_fields` accepts text fields only; use `filterable_fields` for numbers and dates |
| `default_sort ... must be in sortable_fields` | add it to `sortable_fields` (or sort by `pk`) |
| Empty column | a `"a__b"` path in `list_fields`; use a property |
| List is empty although rows exist | wrong active company, missing `view` permission, or a `get_queryset` hook filtering them out |
| 403 on a row you can see | you lack the object-level `change`/`delete` permission, or the row is locked by an approval |
