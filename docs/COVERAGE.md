# Test coverage

Measured with `coverage` 7.11 (in `requirements-dev.txt`) over the `core` package, line **and** branch coverage, full suite on SQLite:

```bash
coverage run --source=core --branch manage.py test
coverage report --skip-covered --sort=cover          # or: coverage html  ->  htmlcov/index.html
```

**Total: 78 %** (13 767 statements, 4 136 branches; 500 tests).

| Package | Statements | Covered | Notes |
|---|---:|---:|---|
| `core/models` | 1 179 | 94 % | |
| `core/jobs` | 109 | 95 % | |
| `core/audit` | 376 | 93 % | |
| `core/templatetags` | 104 | 92 % | |
| `core/notifications` | 288 | 89 % | |
| `core/menu` | 168 | 88 % | |
| `core/crud` | 1 303 | 87 % | |
| `core/reports` | 2 585 | 87 % | report builder, query builder, custom reports, exports, letterhead |
| `core/mail` | 193 | 85 % | |
| `core/search` | 560 | 84 % | |
| `core/seeder` | 716 | 84 % | |
| `core/middleware.py` | 93 | 84 % | |
| `core/approvals` | 825 | 82 % | |
| `core/permissions` | 364 | 82 % | |
| `core/archive` | 424 | 80 % | |
| `core/views` | 2 121 | 79 % | |
| `core/tasks` | 57 | 67 % | |
| `core/files` | 233 | 65 % | on Windows 6 file-backup tests cannot run (symlink rights) |
| `core/backup` | 1 402 | 54 % | the PostgreSQL / MySQL dump and restore paths run only on those engines (see below) |
| `core/management` | 244 | 27 % | operations commands; most are thin wrappers around tested services |

A SQLite run cannot reach the PostgreSQL and MySQL branches of `core/backup`. Run the suite against those engines (as in
`docs/VALIDATION.md`) to exercise them: `tests/test_backup.py` then performs a real dump, validation and restore.

## Where each area is tested

| Area | Test modules (tests) |
|---|---|
| Sign-in, throttling, idle timeout, security headers, no 2FA | `test_auth` (14) |
| Permissions, cache invalidation, superuser bypass | `test_permissions` (10), `test_roles` (9), `test_users_admin` (8) |
| Row-level access (object access, user access) | `test_access` (8) |
| Companies, departments, designations, modal CRUD | `test_modal_org` (27), `test_company_independent` (3) |
| Soft delete and manager checks | `test_softdelete` (7) |
| Audit, M2M audit, redaction | `test_audit` (6), `test_audit_m2m` (10), `test_redaction_pure` (6) |
| CRUD engine, inlines, list columns, import | `test_crud` (12), `test_list_columns` (7), `test_import_pages` (3), `test_ui_updates` (13) |
| Search, saved searches, search builder | `test_search` (15), `test_search_builder` (4) |
| Report builder, company scope, reports in the menu | `test_reports` (24), `test_report_company_scope` (15), `test_report_menu` (27) |
| Query builder | `test_query_builder` (19) |
| Custom reports, PDF letterhead, record exports | `test_custom_reports` (20), `test_letterhead` (13), `test_record_exports` (18) |
| Approvals, queue, added steps | `test_approvals` (29), `test_approval_queue` (8), `test_add_step` (10) |
| Notifications, rules, menu | `test_notifications_menu` (25), `test_notification_rules` (16) |
| Internal mail | `test_mail` (16) |
| Uploads and private files | `test_uploads` (7), `test_files` (6) |
| Backup, archive, database adapters | `test_backup` (7), `test_backup_pure` (8), `test_adapters_pure` (7), `test_codec` (4), `test_archive` (6), `test_databases` (3) |
| Background jobs | `test_jobs` (14) |
| Migrations, packaging, reusability | `test_migrations` (8), `test_packaging` (6), `test_reusability` (8) |
| Seeder (optional app) | `test_seeder` (9) |
