# Feature matrix

Every row is implemented and covered by the tests named in the last column (all passing; see `docs/VALIDATION.md`).

| Area | Feature | Where | Tests |
|---|---|---|---|
| Identity | E-mail login `User` (swappable), `Profile` (department, designation, companies, photo, signature), login / logout, change + reset password, created/updated stamps, soft delete (deleted accounts cannot sign in) | `models/identity.py`, `views/auth.py`, `crud/userform.py` | `test_auth`, `test_users_admin` |
| Security | Session + CSRF, secure cookies, login throttle, idle timeout, CSP / security headers, health / ready, error pages, **no 2FA anywhere**, **no Django admin** (system check) | `middleware.py`, `services/throttle.py`, `checks.py`, `config/settings/` | `test_auth`, `test_reusability` |
| Security | Structured JSON logging with secret redaction | `core/logging.py` | `test_redaction_pure` |
| Companies | Company hierarchy (validated, DB check constraint), logo, membership, default / active company, company switcher that returns to the current page when allowed | `models/organization.py`, `crud/companyform.py`, `views/auth.py` | `test_modal_org`, `test_ui_updates` |
| Companies | Departments and designations shared by every company | `models/organization.py` | `test_company_independent`, `test_modal_org` |
| Companies | `CompanyScopedModel` (FK) + `MultiCompanyScopedModel` (M2M), fail closed | `models/base.py`, `permissions/scope.py` | `test_crud`, `test_permissions` |
| Permissions | Guardian wrapper, versioned cache + invalidation, explicit superuser bypass, staff implied rights, page permissions | `permissions/` | `test_permissions`, `test_roles` |
| Permissions | URL-name default deny + `seed_url_permissions` | `middleware.py`, `permissions/seed.py` | `test_permissions` |
| Permissions | Roles and Permissions pages; a non-superuser can grant only what they hold | `crud/roleform.py`, `permissions/grants.py` | `test_roles` |
| Row access | Object access (e.g. user works only for buyer H&M) and user access (own rows + assigned users' rows) | `permissions/access.py` | `test_access` |
| Models | `CreatedInfoModel`, `UpdatedInfoModel`, `CompanyScopedModel`, `MultiCompanyScopedModel`, `SoftDeleteModel`, `AuditTrackedModel` | `models/base.py` | `test_softdelete`, `test_reusability` |
| Audit | Full snapshot per save, New / Old view with changed fields highlighted, M2M and permission changes, bulk updates, sign-in events, redaction, append-only | `audit/`, `models/audit.py` | `test_audit`, `test_audit_m2m` |
| CRUD | Every field type, nested inlines, HTMX rows, ordering, scoping, trash / restore, modal create / edit / view / delete, tabbed forms, M2M widgets, related-path list columns, image thumbnails | `crud/`, `views/crud.py`, `views/modal.py` | `test_crud`, `test_modal_org`, `test_list_columns`, `test_ui_updates` |
| Search | Quick + advanced builder + saved searches (personal / standard), applied to every list, report and export; Search builder page | `search/`, `views/search_builder.py` | `test_search`, `test_search_builder` |
| Reports | Report builder (`register_report_source`): grouping, aggregates, calculated columns, date presets, charts, sharing, company scope, show in menu | `reports/builder/`, `reports/scope.py`, `reports/menu.py` | `test_reports`, `test_report_company_scope`, `test_report_menu` |
| Reports | Query builder: joins, functions, filters, grouping, sort, charts, saved queries | `reports/query/` | `test_query_builder` |
| Reports | Custom code-defined reports (`register_custom_report`, stock ledger), PDF letterhead from the active company | `reports/custom.py`, `reports/letterhead.py` | `test_custom_reports`, `test_letterhead` |
| Export / import | CSV / TSV / XLSX / XLS / ODS / JSON / YAML / HTML + PDF, formula neutralising, large exports as background jobs; import with preview then confirm and an empty template; PDF / Excel of one record | `reports/resources/`, `views/exports.py` | `test_reports`, `test_import_pages`, `test_record_exports` |
| Approvals | Approval types and steps (permission, several approver users, group, condition), frozen chain, row lock + stale guard, reject modes, resubmit, Observation, add a step to one request, queue per type, signatures on the record | `approvals/`, `views/approvals.py` | `test_approvals`, `test_approval_queue`, `test_add_step`, `test_record_exports` |
| Menu | DB-stored menu up to 6 levels, Menu Builder, default tree seeded after `migrate`, reports placed in the menu, permission-filtered and cached | `menu/`, `views/menu.py` | `test_notifications_menu`, `test_migrations`, `test_report_menu` |
| Notifications | In-app notices, rules per model event, optional e-mail, `on_commit` | `notifications/` | `test_notifications_menu`, `test_notification_rules` |
| Mail | Internal mail (folders, threads, attachments), system mails | `mail/`, `views/mail.py` | `test_mail` |
| Files | Private storage, validation by content, authorised download, orphan cleanup | `files/` | `test_uploads`, `test_files` |
| Background jobs | `register_job` allow-list, `submit_job` / `retry_job`, progress, result file, redaction, retention, Background jobs page, notices | `jobs/`, `views/jobs.py`, `tasks/jobs.py` | `test_jobs` |
| Backup | SCHEMA_ONLY / DATA_ONLY / FULL / LAST_24_HOURS on PostgreSQL, MySQL / MariaDB and SQLite; file backups; validation, retention, guarded restore, atomic claim | `backup/` | `test_backup`, `test_backup_pure`, `test_adapters_pure`, `test_databases` |
| Archive | Move (not copy) to verified files, FK-safe delete, restore, confirm required | `archive/` | `test_archive`, `test_codec` |
| Databases | PostgreSQL (recommended), MySQL 8 / MariaDB 10.6+ (`DB_ENGINE=mysql`), SQLite; `dbinfo` | `config/settings/base.py`, `backup/adapters/` | `test_databases` |
| Packaging | `pyproject.toml` (extras `postgres`, `mysql`, `server`, `dev`), `install.py` setup / export / import / check | `pyproject.toml`, `install.py` | `test_packaging` |
| Migrations | `0001_initial` (schema) + `0002_jobrecord`; default data from `post_migrate` / `bootstrap` | `core/migrations/` | `test_migrations`, `test_jobs` |
| Seeder | Optional demo-data app (`/seed/`, `manage.py seed_data`, `register_seeder`), off unless installed | `core/seeder/` | `test_seeder` |
| Reuse | Dummy business app + import-boundary contract tests | `tests/dummyapp`, `test_reusability.py` | `test_reusability` |

## Limits
* **Channels:** only the `CORE_CHANNELS_LIVE` switch and the notification hook exist; no consumer or routing is shipped.
* **MySQL / MariaDB:** soft-delete uniqueness is enforced by model validation only (no partial indexes); restores are not one
  transaction (`docs/INSTALL.md` §4).
* **SQLite backups** are SQL scripts (stdlib `iterdump`), not byte copies; a restore goes to a new file.
* **User** detail / edit exposes only safe fields; passwords change through the change / reset flows.
