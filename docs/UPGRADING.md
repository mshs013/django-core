# Migrations, upgrades and versions

## 1. Core's migrations

| Migration | What it does |
|---|---|
| `0001_initial` | The whole schema of `core`. Schema only, no data operations (`tests/test_migrations.py` checks this). |
| `0002_jobrecord` | The `JobRecord` table of the background-job framework (`docs/JOBS.md`). On a database that already has a menu it also adds the **Background jobs** entry under *Administration* once; it never re-creates an entry an administrator removed and leaves a menu without an *Administration* group alone. |

Default data is never written by `0001_initial`. It comes from receivers and commands that only **add what is missing**:

* permission rows and content types: Django's own `post_migrate` receivers (auth, contenttypes);
* the default menu (`core/menu/defaults.py`): the `post_migrate` receiver `core.signals_handlers.seed_defaults_after_migrate`
  (fills an **empty** menu table only, so an administrator's edits and deletions are never undone) and `manage.py seed_default_menu`;
* URL permissions (`core.access_*`), approval chains, the first company and superuser: `manage.py bootstrap`.

`core` ships no roles (the optional seeder creates a demo role). Give the everyday page permissions (Approvals, Reports, Mail,
Notifications) through roles.

## 2. Upgrading an installation to a newer `core`

1. Take a backup and validate it: **Administration › Backups**, or `python manage.py backup_run --mode FULL` then
   `backup_validate <id>`.
2. Put the new code in place (`git pull`, a new release directory, or `pip install -U "<path or git url>[postgres,server]"`).
3. `pip install -r requirements.txt -r requirements-postgresql.txt` (or `requirements-mysql.txt`; the pins may have changed).
4. `python manage.py migrate` — applies only the core migrations the database does not have yet.
5. `python manage.py bootstrap` — adds new URL permissions, approval chains and default menu rows; never edits or deletes yours.
6. `python manage.py collectstatic --noinput`, then restart the web process and the `qcluster` worker.
7. `python manage.py makemigrations --check` must say *No changes detected*.

Rolling back: put the previous code back and restore the backup from step 1 (migrations are not assumed reversible).

## 3. Writing a migration for `core`

* Change the models, then `python manage.py makemigrations core` → `core/migrations/0003_….py` (the next free number).
* A data change that existing databases need goes into that migration as `RunPython`: idempotent, and never overwriting what an
  administrator changed (see `add_menu_entry` in `0002_jobrecord.py`). If fresh databases need the same data, add it to
  `core/menu/defaults.py`, the `post_migrate` receiver or `bootstrap` as well.
* `tests/test_migrations.py` fails when a model change has no migration, when `0001_initial` gains a data operation, or when the
  chain is not linear.
* Business apps depend on the latest core migration they need (Django writes it automatically) and on
  `migrations.swappable_dependency(settings.AUTH_USER_MODEL)` for user foreign keys.

## 4. Django and Python versions (long-term support policy)
* **Django 5.2 LTS**, pinned to the latest patch release in `requirements.txt` and `pyproject.toml` (`Django==5.2.18`). 5.2 receives
  security fixes until **April 2028**.
* **Python 3.12+** (3.12 and 3.13 tested).
* Take every 5.2.x patch release (security fixes): bump the pin in `requirements.txt` and `pyproject.toml`, run the test suite and
  `makemigrations --check`. Move to the **next LTS (6.2, expected April 2027)** once it is released and `django-q2`,
  `django-guardian`, `django-import-export` and `django-filter` support it; never run a non-LTS Django (6.0, 6.1) in production.
  `django-q2==1.8.0` requires `django<6`.
