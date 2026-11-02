# How `core` is tested

## 1. Run the checks

```bash
# full suite (SQLite; manage.py test always uses config/settings/test.py)
python manage.py test

# the same suite on another database: set the DB_* variables (docs/INSTALL.md section 3)
DB_ENGINE=postgresql DB_NAME=... DB_USER=... DB_PASSWORD=... python manage.py test
DB_ENGINE=mysql      DB_NAME=... DB_USER=... DB_PASSWORD=... python manage.py test

python manage.py makemigrations --check --dry-run      # must print "No changes detected"
python manage.py check --deploy                        # with production settings: only security.W021 (HSTS preload is
                                                       # a deliberate opt-in, SECURE_HSTS_PRELOAD in config/settings/base.py)
node --check core/static/core/js/*.js                  # after editing JavaScript
pip-audit -r requirements.txt -r requirements-postgresql.txt   # known vulnerabilities in the pinned packages
```

Pure-Python tests that need no database:
`python -m unittest tests.test_adapters_pure tests.test_backup_pure tests.test_codec tests.test_files tests.test_redaction_pure`.

Install the driver first (`requirements-postgresql.txt` or `requirements-mysql.txt`). The database test user needs the right to create a database (`CREATEDB` on PostgreSQL, `ALL` on `test_%` on MySQL).
Backup tests on PostgreSQL and MySQL call the real client tools (`pg_dump`/`pg_restore`, `mysqldump`/`mysql` or the `mariadb-*`
names): put them on `PATH` or set `CORE_BACKUP_TOOLS_DIR`.

## 2. What the suite covers

500 tests (`tests/`), listed by area in `docs/COVERAGE.md`. Among them:

* **Reusability:** `tests/dummyapp` is a small business app (garments, fabrics, buyers, orders) built only on core's public API;
  `test_reusability` checks that core never imports it and that nothing in core uses `django.contrib.admin`.
* **Data scoping:** company scope, object access and user access are checked on lists, detail pages, exports, search, the report
  builder and the query builder.
* **Concurrency:** approval decisions take a row lock with a stale-click guard (two sessions on PostgreSQL); backup runs are
  claimed atomically and file names are unique under 20 threads; a job run is claimed once.
* **Migrations:** no model change without a migration; `0001_initial` holds no data operations; the default menu after a fresh
  `migrate` equals `core/menu/defaults.py`; seeding twice changes nothing and never undoes an administrator's edits.
* **Packaging:** the pins in `pyproject.toml` equal `requirements*.txt`, the dependencies match what core imports, every template
  folder is package data.
* **Backups:** dump, validation and restore on the database the suite runs on; the password is never in argv (MySQL: only in a
  temporary option file) and is masked in every reported setting.

## 3. Verified configurations

| Check | Result |
|---|---|
| Full suite on SQLite, PostgreSQL 17.6, MySQL 8.0.33 and MariaDB 11.8 (Windows 11, Python 3.12, Django 5.2.18) | pass, except the 7 Windows-only tests below |
| `makemigrations --check` | No changes detected |
| `check --deploy` (DEBUG off, real secret key and host) | only `security.W021` (HSTS preload, opt-in) |
| Real backup → validate → restore on PostgreSQL, MySQL 8 and MariaDB | pass; no password in dumps or option files left behind |
| Wheel built from `pyproject.toml`, installed into a bare `django-admin startproject` host, `migrate`, `bootstrap`, sign-in, PDF export | pass |
| `install.py setup` end to end on a clean copy (SQLite) | pass |
| Pages checked in a browser (Chrome) as superuser, staff and a normal user, light and dark theme | pass |
| Line + branch coverage | 78 % (`docs/COVERAGE.md`) |

**Windows-only errors (expected):** `test_backup.BackupRecordTests.test_download_is_checked_and_audited` and six
`test_files.FileBackupTests` tests need symlink rights and file locking that a normal Windows account does not have. They pass on
Linux.

## 4. Areas to check by hand after big changes
1. The browser: modal forms (open, field errors stay in the modal, save closes it and refreshes the table), nested inline rows
   (+ Add, remove, reorder), tabbed forms, the sidebar (fold, nested groups, phone width), dark theme.
2. A backup restore drill on a scratch server (`docs/DEPLOYMENT.md`, *Restore drill*).
3. `python manage.py qcluster` running against the real database: an export, a backup and a registered job finish and notify.
