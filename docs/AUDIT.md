# Audit log

Every change to an audited record, every permission change and every sign-in lands in `AuditLog` (append-only: `save()` of an
existing row and `delete()` raise). The page is **Administration › Audit log**; a record's own history is its *Audit* tab.

## 1. What is logged

| Operation | Event | Where the row goes | Written by |
| --- | --- | --- | --- |
| Create / edit / delete (soft or hard) / restore of an audited model | `create` `update` `delete` `restore` | the record | `core/audit/signals.py` (pre/post_save, post_soft_delete, post_restore, post_delete) |
| Many-to-many change (`add`, `remove`, `set`, `clear`) on an audited model | `update` | the record whose manager was called | `core/audit/m2m.py` (`m2m_changed`) |
| Roles of a user (`groups`), single permissions (`user_permissions`), permissions of a role (`permissions`), members of a role (`user_set`) | `permission` | the user / the role | `core/audit/m2m.py` |
| A user's companies, chosen report companies (`Profile` M2Ms) | `update` | the **user** (not the profile) | `core/audit/m2m.py` |
| Object permissions (`core.permissions.assign_perm` / `remove_perm` / bulk) | `permission` | the object (message: who, what, to whom) | `permissions/service._audit_change` |
| Object access / user access / "may add steps" lists saved on the user form (bulk writes, no signals) | `permission` | the user | `crud/userform._audit_set` |
| `QuerySet.update()` on an audited model (parent-company flag, default saved search, approval / cancel flags, inline row order) | `update` | each changed row | `audit.service.audited_update` |
| Sign in, failed sign in, sign out, idle time-out | `login` `login_failed` `logout` | the user (failed: the e-mail typed, in *By*) | `views/auth.audit_auth` |
| Password changed / reset requested / reset completed | `other` | the user | `views/auth.audit_auth` |
| Report preview / export, query preview / export, list export | `report_run` / `other` | the source model; message names the company scope | reports engines |

Every row carries the acting user, the client IP and the company (the record's company, else the user's active company).

Audited models: anything inheriting `AuditTrackedModel` (also via `AuditedModel`): User, Profile, Role, Company, Department,
Designation, SavedSearch, ReportDefinition, QueryReport, ApprovalType, ApprovalTypeStep, and your own models that opt in.

## 2. Many-to-many rows

```
changes = {"companies": [["Company A"], ["Company A", "Company B"]]}       # [old set, new set], names sorted
message = "companies: added Company B"
```

* Names, not ids (permissions as `app.codename`), stored when the change happens, so they still read after a rename or delete.
* **One row per operation.** Changes to the same field of the same record inside one transaction are collected and written when it
  commits: `set()` (= remove + add) gives one row, saving the user form gives one row per changed list. Nothing changed, or the
  transaction rolled back = no row. The user / IP / company are taken at the first change.
* The audit view lists the old and new sets under friendly headings (*Member of*, *Roles*, *Single permissions*, *Permissions*,
  *Members*, …).
* Tests: rows are written by `transaction.on_commit`, so wrap the change in `self.captureOnCommitCallbacks(execute=True)`.

## 3. Secrets and files

* Field names that look secret (`password`, `token`, `hash`, `secret`, `key`, `session`, `signature`, …) are masked at any depth;
  `User.password` / `last_login` and `Profile.active_company` / `theme` are never snapshotted (`audit_exclude_fields`).
* File / image fields: only "Previous image" / "New image" / "Image removed" is shown, never the name or contents.
* Report audit rows hold the mode and company abbreviations, never row data.
* An audit write never raises into the caller: failures are logged (without payload) and the operation goes on, in a savepoint so
  PostgreSQL does not abort the caller's transaction.

## 4. Paths that are not covered

* `QuerySet.update()` / `bulk_create()` / raw SQL in **your own** code: call `core.audit.service.audited_update(qs, **values)` or
  `record_event(...)` yourself. In core, every such path on an audited model is listed above.
* Many-to-many changes of models that are not audited (notification rule recipients, mail labels, …) and through-table rows written
  directly (`Through.objects.bulk_create`) other than the ones in the table above.
* Data migrations (they use historical models, which are never audited) and code run inside `context.audit_suppressed()`.
* `loaddata` / fixtures (`raw=True` saves are skipped).

## 5. Code

| File | What |
| --- | --- |
| `core/models/audit.py` | `AuditLog` |
| `core/audit/service.py` | `record_event`, `snapshot`, `audited_update`, `is_tracked` |
| `core/audit/signals.py` | save / delete / soft delete / restore receivers |
| `core/audit/m2m.py` | many-to-many receiver (collect per transaction, write on commit) |
| `core/audit/display.py` | readable rows (labels, names, New / Old lists) |
| `core/audit/redaction.py` | masking and bounding of values |
