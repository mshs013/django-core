# Architecture

## Packages
`config/` (`settings/` = `base`, `production`, `dev`, `test`; `env.py` picks one; urls, wsgi/asgi) and `core/`. `core` is layered, no cycles upward:

```
core/conf.py            CORE_* defaults             core/signals.py   post_soft_delete, post_restore, approval_event
core/services/          contextvars (acting user/ip/company), login throttle
core/models/            abstract bases + concrete tables (identity, organization, audit, search, reports, approvals, menu,
                        notifications, files, jobs)
core/permissions/       guardian wrapper, versioned cache, company scope helpers, URL-permission seeding
core/audit/             redaction (pure), snapshots, signal receivers
core/{crud,search,reports,approvals}/   registry-driven engines (see below)
core/{menu,notifications,files}/        services
core/{backup,archive}/  operations (adapters for PostgreSQL, MySQL/MariaDB, SQLite; argv-only subprocess runner)
core/jobs/              background jobs for business apps (register_job, submit_job, JobRecord)
core/tasks/             django-q2 entry points
core/views/ core/urls.py core/templates/ core/static/   web layer
core/management/commands/                               operations CLI
```

## Request pipeline
`SecurityMiddleware → WhiteNoise → Session → Common → CSRF → Authentication → LoginRequiredMiddleware (Django, default-deny)
→ RequestContext (user/ip/company into contextvars) → IdleTimeout → UrlPermission → Messages → XFrame → SecurityHeaders (CSP etc.)`.

* Views are login-required by default; public ones opt out with `@login_not_required` (login, password reset, health, ready).
* **UrlPermissionMiddleware** is default-deny by URL name: permission `core.access_<name>` (seeded by `seed_url_permissions`, which never
  deletes). Unnamed URLs are refused for non-superusers. Core's own views set `core_self_authorizing = True` and enforce their own checks.
* Visibility of a menu entry is a convenience only — every view re-checks.

## Company scope (fail closed)
`CompanyScopedModel` (FK `company`) and `MultiCompanyScopedModel` (M2M `companies`) put the lookup on the model (`company_lookup`).
The **data scope is the ACTIVE company only** (`Ctx.company_ids`); `member_ids` lists all memberships (switcher, saved-search visibility).
`scope_queryset(qs, lookup, ids)` accepts any ORM path (`company`, `companies`, `id`, `profile__companies`). No active company ⇒ empty.
Out-of-scope object ⇒ **404** (no probing); in-scope but not permitted ⇒ **403**.

## Permissions
`core.permissions` wraps guardian: `user_has_perm(user, perm, obj=None, company=None)`, `assign_perm`, `remove_perm`, `filter_by_perm`,
`bulk_assign_perm`, `bulk_remove_perm`. A global (model-level) grant only counts inside the user's member companies; an object grant
via guardian counts for that object. Results are cached under one **global version number** in the Django cache; `bump()` invalidates
every user at once. It is called on grants and on Group/Permission/User/Company/Profile/MenuItem changes (including the m2m through
tables). Default cache is file-based so all workers share it (Redis if `REDIS_URL`). Superuser bypass is explicit in one place.

### Row-level access (object access, user access)
On top of company scope and model permissions, `core.permissions.access` limits rows per user:
* `register_access_scope(Buyer, target=Order, path="buyer")`: users see only the buyers assigned to them (`ObjectAccess`), and
  orders follow; no assignment = no rows.
* `register_resource(..., owner_field="created_by")`: users see their own rows plus those of their assigned users (`UserAccess`).

`restrict(qs, user)` is applied in `scoped_queryset` (CRUD, export, search), the report builder's `scoped_base`, and the query
builder's root and join scope. `scope_form` narrows relation dropdowns the same way. Superusers are never limited. See
`docs/ACCESS_CONTROL.md`.

## Soft delete
Managers `objects` (alive, declared first so it is Django's default manager), `deleted_objects`, `global_objects`. Queryset `.delete()`
soft-deletes row by row (signals fire); `hard_delete()` is explicit. System checks `core.E001/E002` fail if a soft model's default
manager can expose deleted rows. Cascades share a `when` timestamp so restore only revives what that delete removed.
Unique constraints on soft models are partial (`deleted_at IS NULL`).

## Audit
Opt-in via `AuditTrackedModel` (also inherited by `AuditedModel`). pre/post_save + soft-delete/restore/delete receivers write append-only
`AuditLog` rows with old/new snapshots. Sensitive names (password, token, hash, secret, key, cookie, session, csrf…) are masked at any
depth; bytes/file contents are never stored; values are length-bounded. Events: CRUD, login/failed/logout, permission, backup, archive,
approval, report_run, saved_search. `AuditLog.save/delete` raise on update/delete. UI: resource `audit`, searchable like any resource.

Every audited save also stores a full `snapshot` of the record (secrets removed), so the audit view shows every field with
the changed ones highlighted; older rows without one fall back to the New/Old lists.

Many-to-many and permission changes (`core/audit/m2m.py`), bulk `update()` paths (`audited_update`) and sign-in events are covered
too; the full list, and what is not covered, is in [AUDIT.md](AUDIT.md).

## Engines — one registry pattern
All engines follow the same shape: **`register_*` called from `AppConfig.ready()` → an allowlist → `get_*`**, with no business imports.

| Engine | Register | Allowlist | Consumers |
|---|---|---|---|
| CRUD | `register_resource(model, …)` | list/detail/form/export fields, inlines, perms | `r/<slug>/…` views, menu, search |
| Search | created by `register_resource` / `register_report_source` (`register_search(target_type, key, …)`) | searchable + filterable + sortable fields | every list, report builder, saved searches, exports |
| Reports | `register_report_source(key, model, columns=[ReportColumn…])` | columns, groupable, per-column aggregate functions | builder, preview, exports, `ReportDefinition` |
| Query builder | `register_query_model(model, …)` (+ every plain CRUD resource automatically) | models, joinable relations, allowed fields; functions / operators / aggregates in `core.reports.query.engine` | `reports/query/…` page, `QueryReport`, exports, charts |
| Custom reports | `register_custom_report(key, label, run, permission=…, form=…)` | the registered keys + their permission; `run(ctx, params)` scopes its own data | `reports/custom/<key>/` page, PDF/spreadsheet exports |
| Approvals | `register_chain`, `register_target`, `register_skip_rule` (+ `sync_chain` DB mirror) | chain steps in code | submit/decide/cancel/history/queue |
| Background jobs | `register_job(name, handler, label=…, timeout=…)` | registered job names (a stored run of an unknown name fails, never executes) | `submit_job` / `retry_job`, `/jobs/` pages, `jobs_info` |

**How the search builder mirrors the approvals and report registries:** a search target is registered by key (`resource:<slug>` or
`report_source:<key>`) exactly like an approval target (`target_key`) or report source (`key`); duplicate registration raises
`ImproperlyConfigured`; the spec is looked up by key and every field/operator/sort name coming from a request is validated against the
registered allowlist, never reflected into the ORM; saved searches and report definitions store only JSON that is **re-validated on every
load** (a definition that no longer validates is skipped with a warning). Approvals store a frozen chain snapshot the same way.

### CRUD engine
* **Field coverage:** `formfield_for()` maps Char/Text/Slug/Email/URL/UUID/JSON/Integer/PositiveInteger/Float/Decimal/Boolean/Date/DateTime/
  Time/Duration/GenericIPAddress/Choice/FK/O2O/M2M/File/Image/Binary/GenericFK; `register_field_mapping()` extends it. Widgets are
  `setdefault`ed so a registration can override. Upload fields validate extension, size and (images) content.
* **Multi-table:** `Inline` kinds `fk`, `through` (M2M through model with extra columns) and `generic`; `nested=(…)` for grandchildren at
  any depth; `position_field` renumbers 0..n from DOM order. `save_all` runs in **one `transaction.atomic()`** with a savepoint per
  formset; any failure raises `SaveFailed` and the parent is not saved. Child choices are company-scoped; child permissions are checked
  per action; soft children soft-delete (hard delete only with `hard_delete=True`).
* **HTMX rows:** `r/<slug>/row/<path>/` renders an empty form; `path` is regex-validated and walked through the allowlisted inline chain
  (unknown ⇒ 404). Prefixes follow `lines-0-options-1-label`. The new row's index is read from `TOTAL_FORMS` when "+ Add" is clicked
  (so every added row gets its own index); a slot left by a removed row is skipped, and an empty position is filled from the order
  shown. Errors in any row raise the form's top alert.
* **Form layout:** `tabs=(("Label", ("field", "@inline_slug")), ...)` (or `self.tabs` on a custom form) splits the form into tabs,
  and the first tab with an error opens. `m2m_widgets={"field": "checkbox"}` (default `CORE_M2M_WIDGET`) renders a many-to-many field
  as a filterable checkbox list.
* **Uploads** are private (`core.files`): random storage names, original name only metadata, served by an authorised view with
  `nosniff` + `attachment`, orphan cleanup with a grace period.

### Search builder
Quick search: whitespace tokens ANDed, each ORed across registered `searchable_fields`/`searchable_relations` (`icontains`, `%`/`_` literal).
Advanced: one-level AND/OR tree of leaves `{field, op, value}`; operators per field kind (text, number, date, datetime, bool, choice, fk,
uuid, isnull, range, in). **django-filter** builds one dynamic `FilterSet` per target; filter names are `<path>::<operator>`; each leaf
is executed through a FilterSet bound to that single filter and combined as `Q(pk__in=leaf_qs.values('pk'))` so to-many joins never
duplicate rows and `distinct()` is not needed. Page size is clamped by `CORE_MAX_PAGE_SIZE`; sort has a stable `pk` tie-breaker.
Saved searches: private / company / public-within-company, one default per owner+target (partial unique), pin to menu, clone, share.

### Report builder
`validate_definition` allowlists columns, groupings (only `groupable`), aggregates (`count`, `count_distinct`, `sum`, `avg`, `min`, `max`,
only where the column permits), calculated columns (`add/sub/mul/div`, division by `NullIf`; operands are shown columns or numbers) and
sort. `build_queryset` compiles to `values()/annotate()/filter()/order_by()` — never raw SQL; company scope is applied before anything
else. Aliases may not clash with model field names. Preview is paginated and bounded.

**Custom reports** (`core.reports.custom`) are code-defined: `run(ctx, params)` returns a `ReportOutput` (columns, rows, info block,
totals, notes, signatures). Permission is checked before `run`; scoping the data is `run`'s job. **PDF letterhead**
(`core.reports.letterhead`): every PDF goes through `build_branded()`, which draws the header/footer from a `Letterhead` (data,
`CORE_PDF_LETTERHEAD`) with a `LetterheadTemplate` (layout, `CORE_PDF_LETTERHEAD_CLASS`); "Page X of Y" uses a deferred canvas.
Signature boxes can be read from approval history (`core.approvals.signatures`). Full guide: `docs/REPORT_BUILDER.md`.

**Query builder** (`core.reports.query`): a JSON definition (root model, columns with function / aggregate / "only where" /
CASE, AND/OR filter tree, HAVING, sort, limit, chart) is validated path by path against the query allowlist (`catalogue.resolve`
walks the same relations the picker shows) and compiled to one `filter()` call (so conditions on a to-many join apply to the same
joined row) + `values()`/`annotate()` with internal aliases `c0…cN`. Joined models add company and soft-delete conditions.
Database errors become a user message inside a savepoint. Charts (both builders) are Chart.js, vendored. Guide:
`docs/QUERY_BUILDER.md`.

**Display formats** (`core.formatting`): `CORE_DATE_FORMAT` / `CORE_DATETIME_FORMAT` / `CORE_TIME_FORMAT` (environment) are
installed as Django's `DATE_FORMAT` etc. through `FORMAT_MODULE_PATH = ["core.formats"]`, so templates, list/detail cells,
previews and audit changes use them by default; a resource can override them with `date_formats=`.

### django-filter + django-import-export integration
* django-filter: only inside `core.search.filters` (FilterSet generation) — the single query path for lists, report filters, saved searches
  and exports, so what you see is what you export.
* django-import-export: `core.reports.resources` builds a `ModelResource` per CRUD resource (export fields from the allowlist) and a
  dynamic `Resource` over row objects for report output; every exported text cell goes through `neutralise()` (spreadsheet formula
  injection). Formats are whatever tablib can load on the install (CSV, TSV, XLSX, XLS, ODS, JSON, YAML, HTML) plus reportlab PDF.
  Import is **dry-run preview → explicit confirm**, in one transaction, company forced from the session (never read from the file),
  extension/size validated, rows capped. CSV/TSV are built in bounded chunks; other formats are capped by `CORE_EXPORT_MAX_ROWS`; above
  `CORE_EXPORT_SYNC_MAX_ROWS` the export becomes an `ExportJob` run by django-q2 with progress, an expiry and an owner-only download.

### Approvals engine (permission-layer inspection)
Inspected `core.permissions` (Django groups/permissions + guardian + company membership): it answers *who may do X on object O in company
C* but has no notion of type/step/chain ⇒ **PARTIAL → extend**: every authorization decision still goes through `core.permissions`;
only chain/step/request/history models are new. Chains are code-driven (`CHAINS`) and mirrored to the DB by `sync_chain`
(get_or_create; never deletes or edits). The last step is final and cannot be skipped or disabled (DB partial-unique + registration check).
A submit freezes a **chain snapshot** (sequence spacing 1000 so `add_step_to_request` can insert after the current step).
`submit_approval_step` takes `select_for_update` on the request and checks `expected_sequence` (stale-click guard) ⇒ two sessions cannot
both approve the same step. Self-approval is off (`CORE_SELF_APPROVAL`). Reject modes: `return_to_submitter`, `previous_step`.
`is_approval/approved_at` are written only by the engine at the final approval. Notifications go through `transaction.on_commit`.
Runs with `django.contrib.admin` absent.

## Notifications / menu
In-app rows (+ optional e-mail `CORE_NOTIFY_EMAIL`, optional Channels push `CORE_CHANNELS_LIVE`), always sent from `transaction.on_commit`.
Menu = DB items (cycle-checked, permission-filtered, cached per user+company, invalidated on change) + registered entries + pinned searches.

## Backup / archive
`core.backup`: argv-list subprocess runner (no shell, timeouts, output redaction, password via env only). PostgreSQL `pg_dump`/`pg_restore`
(custom format). MySQL / MariaDB `mysqldump`/`mysql` (gzip SQL; the password goes in a temporary option file, never argv). SQLite via stdlib `iterdump` → gzip SQL; restores go to a **new file**, never over the live DB. Modes `SCHEMA_ONLY`,
`DATA_ONLY`, `FULL`, `LAST_24_HOURS` (creation-timestamp detection; never silently falls back to a full dump; manifest recorded).
Locking: atomic `QUEUED→RUNNING` claim (a duplicate task delivery claims nothing) + `O_EXCL` unique filenames (test: 20 threads).
Restore is off unless `CORE_BACKUP_ALLOW_RESTORE`, needs permission, a preflight and a typed confirmation phrase.
`core.archive`: allow-listed tables only, never `django_/auth_/guardian_/core_/sqlite_/pg_` prefixes; **move, not copy**: keyset batches →
gzip JSONL → verify row count + sha256 + primary-key digest → `DELETE` guarded by `NOT EXISTS` over every inbound reference (re-checked
inside the DELETE) → record. A failed verification aborts before any delete. Needs `confirm=True`.
