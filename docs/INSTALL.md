# Installation guide

This guide takes a new machine to a running system: first a **development install** (Windows or Linux, SQLite or PostgreSQL),
then the extra steps for a **production server** (Linux, PostgreSQL, Gunicorn, Nginx). The production topology, hardening and
backup drill are covered in more depth in `docs/DEPLOYMENT.md`.

---

## 1. What you need

| Item | Version | Notes |
|---|---|---|
| Python | 3.12 or newer (3.12, 3.13 tested) | `python --version`. Django is pinned to the 5.2 LTS line (`docs/UPGRADING.md` §4). On Windows tick *Add python.exe to PATH* in the installer. |
| PostgreSQL | 14 or newer | Production database. SQLite (built into Python) is fine for development and small installs. |
| PostgreSQL client tools | same major version as the server | `pg_dump` / `pg_restore`, only needed for the **Backups** page. On Windows they come with the PostgreSQL installer (`C:\Program Files\PostgreSQL\16\bin`). |
| Git | any | Optional, to fetch the code. |

Nothing else: no Node, no Redis, no Docker. Front-end libraries (Tabler, HTMX, Alpine, Chart.js) are already in
`core/static/core/vendor/` and are served by the app itself.

---

### Quick way: `install.py`

Sections 2–5 are automated by one script (standard library only, run it with the system Python from the project folder):

```bash
python install.py setup      # .venv + requirements + .env (asks for the database, can create the PostgreSQL user/db)
                             # + migrate + first company/superuser (or load an export) + collectstatic + check
python install.py export     # all data to var/exports/data-<date>.json (dumpdata; uploaded files are not included)
python install.py import     # load such a file (optionally into an emptied database)
python install.py check      # system check + migration status
```

Without an argument it shows a menu. An existing `.env` is kept as `.env.bak` when the script rewrites it, and settings it
does not ask about are carried over. Passwords are typed hidden and never put on a command line. The manual steps follow.

---

## 2. Get the code and create a virtual environment

Linux / macOS:

```bash
cd /srv            # or any folder
git clone <your repository url> core && cd core          # or unzip the delivered archive here
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
pip install -r requirements-postgresql.txt    # PostgreSQL driver (see below)
```

Windows (PowerShell):

```powershell
cd F:\projects
git clone <your repository url> core; cd core             # or unzip the delivered archive here
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1                             # if blocked: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
python -m pip install --upgrade pip
pip install -r requirements.txt
pip install -r requirements-postgresql.txt    # PostgreSQL driver (see below)
```

`requirements.txt` holds only what the application needs at runtime. The database driver is a separate file:
`requirements-postgresql.txt` (PostgreSQL, the default), `requirements-mysql.txt` (MySQL 8 / MariaDB) or none (SQLite).
Test and audit tools are in `requirements-dev.txt`
(`pip install -r requirements-dev.txt` on development machines and CI only). `gunicorn` is skipped automatically on Windows;
it is only used on the Linux production server.

---

## 3. Configure the environment (`.env`)

All configuration comes from environment variables. The simplest way is a `.env` file in the project folder (next to
`manage.py`); it is read automatically and real environment variables always win. **Never commit `.env`.**

Create an empty `.env` and add only the variables you need: start from one of the examples below; every variable is
listed with its default in [All settings read from the environment](#all-settings-read-from-the-environment). Anything
left out uses its default.

Generate a secret key and paste it into `.env`:

```bash
python -c "import secrets; print(secrets.token_urlsafe(64))"
```

### Development `.env` (SQLite)

```ini
DJANGO_DEBUG=1
DJANGO_SECRET_KEY=<the generated key>
DB_ENGINE=sqlite
DJANGO_TIME_ZONE=Asia/Dhaka
CORE_DATE_FORMAT=d-M-Y
CORE_DATETIME_FORMAT=d-M-Y h:i:s A
CORE_TIME_FORMAT=h:i:s A
```

### Development or production `.env` (PostgreSQL)

```ini
DJANGO_DEBUG=0
DJANGO_SECRET_KEY=<the generated key>
DJANGO_ALLOWED_HOSTS=erp.example.com
DJANGO_CSRF_TRUSTED_ORIGINS=https://erp.example.com
DJANGO_BEHIND_PROXY=1
DB_ENGINE=postgresql
DB_NAME=core
DB_USER=core
DB_PASSWORD=<database password>
DB_HOST=127.0.0.1
DB_PORT=5432
DJANGO_TIME_ZONE=Asia/Dhaka
CORE_BACKUP_DIR=/var/lib/core/backups
CORE_PRIVATE_MEDIA_ROOT=/var/lib/core/private_media
```

### Settings modules

`config/settings/` holds one module per environment; `manage.py`, `config/wsgi.py` and `config/asgi.py` pick it by themselves:

| Module | Used when | What it changes |
|---|---|---|
| `config.settings.base` | never directly | Everything shared, with production-safe defaults; reads `.env` |
| `config.settings.production` | `DJANGO_DEBUG` is off (default) | Requires `DJANGO_SECRET_KEY`; secure cookies, HTTPS redirect, HSTS, hashed static files, SMTP |
| `config.settings.dev` | `DJANGO_DEBUG=1` | Error pages, plain HTTP on localhost, console e-mail, static files without `collectstatic` |
| `config.settings.test` | `python manage.py test` | SQLite by default, in-memory cache, jobs run inline, the dummy business app |

Set `DJANGO_SETTINGS_MODULE` (environment or `.env`) or pass `--settings=` to choose one explicitly. Project-wide changes
(an extra app, a `CORE_*` option) go in `base.py`; environment-only ones in the module of that environment.

### All settings read from the environment

| Variable | Default | Meaning |
|---|---|---|
| `DJANGO_SECRET_KEY` | — (required unless `DJANGO_DEBUG=1`) | Signing key. The app refuses to start without it in production. |
| `DJANGO_DEBUG` | `0` | `1` only on a developer machine: selects `config.settings.dev` (error pages, static files served). |
| `DJANGO_SETTINGS_MODULE` | chosen automatically | Force a settings module, e.g. `config.settings.production`. |
| `DJANGO_ALLOWED_HOSTS` | `localhost,127.0.0.1` in debug | Comma-separated host names the site answers to. |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | — | Full origins (`https://erp.example.com`) when behind HTTPS. |
| `DJANGO_BEHIND_PROXY` | `0` | `1` behind Nginx/a load balancer that sets `X-Forwarded-Proto`. |
| `DJANGO_SSL_REDIRECT`, `DJANGO_HSTS_SECONDS` | on / 1 year in production | HTTPS redirect and HSTS. |
| `DJANGO_TIME_ZONE` | `UTC` | e.g. `Asia/Dhaka`. Dates are stored in UTC and shown in this zone. |
| `DJANGO_EXTRA_APPS` | — | Comma-separated business apps to add to `INSTALLED_APPS` (e.g. `garments,knitting`). |
| `DB_ENGINE` | `postgresql` | `postgresql`, `mysql` (MySQL 8 / MariaDB 10.6+, also accepts `mariadb`) or `sqlite`. |
| `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT` | `core`, `core`, —, `localhost`, `5432` (MySQL `3306`) | Server connection (for SQLite, `DB_NAME` is the file path; default `db.sqlite3`). |
| `DB_CONN_MAX_AGE` | `60` | Seconds a database connection is reused. |
| `CORE_DATE_FORMAT` | `d-M-Y` | Date format used all over the site (`21-Oct-2026`). |
| `CORE_DATETIME_FORMAT` | `d-M-Y h:i:s A` | Date-time format (`21-Oct-2026 06:42:46 AM`). |
| `CORE_TIME_FORMAT` | `h:i:s A` | Time format. Format letters: `d` day, `m` month no., `M` Jan, `Y` year, `H` 0-23, `h` 1-12, `i` minutes, `s` seconds, `A` AM/PM. |
| `CACHE_DIR` / `REDIS_URL` | `var/cache` | Shared cache for permissions, menus and login throttling (Redis if `REDIS_URL` is set). |
| `CORE_PRIVATE_MEDIA_ROOT` | `<project>/private_media` | Uploaded files (never served directly by the web server). |
| `CORE_BACKUP_DIR`, `CORE_ARCHIVE_DIR` | `<project>/var/backups`, `var/archive` | Backup and archive output. |
| `CORE_BACKUP_ALLOW_RESTORE` | `0` | `1` enables the restore button (with confirmation). |
| `CORE_BACKUP_FILE_ROOTS`, `CORE_ARCHIVE_ALLOWED_TABLES` | — | Folders included in file backups; tables the archive may move. |
| `CORE_NOTIFY_EMAIL` | `0` | `1` also sends notifications as real e-mail. |
| `CORE_MENU_MAX_DEPTH` | `6` | Deepest sidebar menu level (top level = 1); the Menu builder refuses deeper items. |
| `EMAIL_BACKEND`, `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_USE_TLS`, `DEFAULT_FROM_EMAIL` | console in debug | Outgoing e-mail (password reset, notifications). |
| `Q_WORKERS`, `Q_TIMEOUT`, `Q_SYNC` | `2`, `3600`, `0` | Background job workers. `Q_SYNC=1` runs jobs inline (no `qcluster` needed; development only). |
| `SESSION_COOKIE_AGE`, `LOG_LEVEL` | 12 h, `INFO` | Session length, log verbosity. |
| `BOOTSTRAP_COMPANY_NAME`, `BOOTSTRAP_COMPANY_ABBREVIATION`, `DJANGO_SUPERUSER_EMAIL`, `DJANGO_SUPERUSER_PASSWORD` | — | Read once by `manage.py bootstrap`; remove them afterwards. |

Other `CORE_*` options (page sizes, export limits, PDF letterhead, query-builder limits ...) have safe defaults in
`core/conf.py`; set them in `config/settings/base.py` only if you need to change them.

---

## 4. Create the PostgreSQL database (skip for SQLite; MySQL / MariaDB below)

```sql
-- psql -U postgres
CREATE ROLE core LOGIN PASSWORD 'choose-a-strong-password';
CREATE DATABASE core OWNER core ENCODING 'UTF8';
```

On Windows run the same in *SQL Shell (psql)* or pgAdmin. Make sure `DB_*` in `.env` match.

### MySQL 8 / MariaDB 10.6+ instead (optional)

PostgreSQL stays the recommended engine. To use MySQL or MariaDB, install the driver (`pip install -r requirements-mysql.txt`;
on Ubuntu first `sudo apt install pkg-config default-libmysqlclient-dev build-essential`), create the database, and set
`DB_ENGINE=mysql`, `DB_PORT=3306`:

```sql
-- mysql -u root -p   (or: sudo mariadb)
CREATE DATABASE core CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'core'@'localhost' IDENTIFIED BY 'choose-a-strong-password';
GRANT ALL ON core.* TO 'core'@'localhost';
```

`python manage.py dbinfo --live` shows the engine, the server version and whether the backup tools were found. Backups use
`mariadb-dump` / `mysqldump` and restores `mariadb` / `mysql` (install the client tools; the password goes in a temporary
0600 option file, never on the command line). Differences from PostgreSQL, tested on MySQL 8.0.33 and MariaDB 11.8:

* **Soft-delete uniqueness** ("name unique among non-deleted rows") cannot be a database constraint (no partial indexes). Forms,
  imports and `full_clean()` still refuse duplicates; code that saves with `objects.create()` without validating, or two saves at
  the same instant, can create a duplicate. The matching system-check warnings (`models.W036`, `W037`) are silenced in settings.
* **Restores are not one transaction**: MySQL commits every `DROP` / `TRUNCATE` on its own, so a restore that fails half way
  leaves a partly restored database. A FULL safety backup is always taken first; restore that one if this happens.
* The "a company is not its own parent" rule is checked by the form only (MySQL refuses CHECK constraints on AUTO_INCREMENT
  columns; `core.models.constraints.CheckConstraintSkippedOnMySQL`).

---

## 5. Create the tables and the first user

```bash
python manage.py migrate                       # creates every table (and the default menu)
python manage.py bootstrap --company-name "OCMS Knit Composite Ltd." --company-abbreviation OCMS
```

`bootstrap` is safe to run again. It creates the first company and, if `DJANGO_SUPERUSER_EMAIL` and `DJANGO_SUPERUSER_PASSWORD`
are set in the environment, the first superuser; it also syncs approval chains and URL permissions. Without those variables,
create the superuser interactively:

```bash
python manage.py createsuperuser
```

Then sign in, open **Administration → Users**, edit your user and tick the company (the superuser must belong to at least one
company to see company data).

After adding or changing business apps later, run:

```bash
python manage.py migrate
python manage.py sync_approval_chains          # mirrors approval chains declared in code
python manage.py seed_url_permissions          # adds the permission rows for new pages (never deletes)
python manage.py seed_default_menu             # only adds default menu entries that are missing
```

---

## 6. Run it (development)

```bash
python manage.py runserver                     # http://127.0.0.1:8000
python manage.py qcluster                      # second terminal: background jobs (large exports, backups, archive)
```

With `Q_SYNC=1` in `.env` you can skip `qcluster` during development (jobs then run inside the request).

Check the installation:

```bash
python manage.py check                         # configuration problems
python manage.py makemigrations --check --dry-run   # must say "No changes detected"
```

Run the test-suite (uses SQLite and a test app, never your data):

```bash
pip install -r requirements-dev.txt
python manage.py test                 # uses config.settings.test
```

On Windows set `PYTHONUTF8=1` first (`$env:PYTHONUTF8 = "1"`); a few backup/file tests need symlink rights that a normal
Windows account does not have and will report errors there — they pass on Linux.

---

## 7. Production server (Linux)

1. Follow sections 2–5 as the service user (e.g. `core`) in `/srv/core`, with `DJANGO_DEBUG=0` and the PostgreSQL `.env`
   (or put the variables in `/etc/core/core.env`, mode 0600, and load them with systemd `EnvironmentFile=`).
2. Collect static files (served by WhiteNoise, optionally cached by Nginx):
   ```bash
   python manage.py collectstatic --noinput
   python manage.py check --deploy
   ```
3. Create the folders the app writes to and give them to the service user:
   ```bash
   sudo mkdir -p /var/lib/core/backups /var/lib/core/private_media /srv/core/var
   sudo chown -R core:core /var/lib/core /srv/core/var
   ```
4. systemd services — `core-web` (Gunicorn) and `core-q` (`manage.py qcluster`); the unit files, Nginx site and TLS setup are
   in `docs/DEPLOYMENT.md`. Start them:
   ```bash
   sudo systemctl enable --now core-web core-q
   ```
5. Housekeeping (cron or systemd timer), e.g. nightly:
   ```bash
   python manage.py cleanup_exports
   python manage.py backup_retention
   python manage.py cleanup_orphan_files
   ```

Windows servers: Gunicorn does not run on Windows. Use a Linux host for production, or run the app under IIS/wfastcgi or
`waitress` (`pip install waitress` then `waitress-serve --port=8000 config.wsgi:application`) behind IIS/Nginx; the rest of
this guide is unchanged.

---

## 8. Add your business app

```bash
python manage.py startapp knitting
```

1. Add it with `DJANGO_EXTRA_APPS=knitting` in `.env` (or append it to `INSTALLED_APPS` in `config/settings/base.py`).
2. Inherit `core`'s base models and register resources / reports / approval chains from the app's `AppConfig.ready()` — see the
   example in `README.md` and the guides in `docs/`.
3. `python manage.py makemigrations knitting && python manage.py migrate`, then the commands at the end of section 5.

---

## 8b. Use `core` in a new project

`core` is a reusable app; `core/migrations/0001_initial.py` creates its whole schema. To start a new Django project on it (tested:
`django-admin startproject`, the settings below, `migrate`, `bootstrap`, sign-in, dashboard and a PDF export):

1. Either install it as a package — `pip install "<path to this repo or git url>[postgres,server]"` (`pyproject.toml`; the
   templates, static files and migrations come with it; extras `postgres` = psycopg, `server` = whitenoise / argon2 / gunicorn,
   `dev` = coverage / pip-audit; leave `postgres` out on SQLite) — or copy the `core/` folder into the project (next to
   `manage.py`) and `pip install -r requirements.txt` plus `requirements-postgresql.txt` or `requirements-mysql.txt`
   (Django 5.2 LTS, Python 3.12+). The pins in `pyproject.toml` and
   `requirements*.txt` are the same (`tests/test_packaging.py`); change both together.
2. `settings.py` — **set `AUTH_USER_MODEL` before the very first `migrate`** (it cannot be changed afterwards):
   ```python
   INSTALLED_APPS = [
       "django.contrib.auth", "django.contrib.contenttypes", "django.contrib.sessions", "django.contrib.messages",
       "django.contrib.staticfiles",
       "django_filters", "import_export", "guardian", "django_q",
       "core",
       "myapp",                                   # your business apps AFTER core
   ]                                              # no django.contrib.admin (core's system check refuses it)
   AUTH_USER_MODEL = "core.User"
   AUTHENTICATION_BACKENDS = ["django.contrib.auth.backends.ModelBackend", "guardian.backends.ObjectPermissionBackend"]
   ANONYMOUS_USER_NAME = None
   LOGIN_URL, LOGIN_REDIRECT_URL = "core:login", "core:dashboard"
   DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
   MIDDLEWARE = [
       "django.middleware.security.SecurityMiddleware",
       "whitenoise.middleware.WhiteNoiseMiddleware",
       "django.contrib.sessions.middleware.SessionMiddleware",
       "django.middleware.common.CommonMiddleware",
       "django.middleware.csrf.CsrfViewMiddleware",
       "django.contrib.auth.middleware.AuthenticationMiddleware",
       "django.contrib.auth.middleware.LoginRequiredMiddleware",
       "core.middleware.RequestContextMiddleware",
       "core.middleware.IdleTimeoutMiddleware",
       "core.middleware.UrlPermissionMiddleware",
       "django.contrib.messages.middleware.MessageMiddleware",
       "django.middleware.clickjacking.XFrameOptionsMiddleware",
       "core.middleware.SecurityHeadersMiddleware",
   ]
   # TEMPLATES[0]["OPTIONS"]["context_processors"] += ["core.context_processors.core"]
   CORE_PRIVATE_MEDIA_ROOT = BASE_DIR / "private"                 # uploads, backups, exports (outside the web root)
   Q_CLUSTER = {"name": "myproject", "orm": "default", "timeout": 60, "retry": 120}
   ```
   Secrets and the database come from environment variables, never from source (`DJANGO_SECRET_KEY`, `DB_*`, … — copy
   `config/settings/` and `config/env.py`, which already read them, or use them as your settings). The full list is in section 3.
3. `urls.py`: `urlpatterns = [path("", include("core.urls"))]` (namespace `core:`; add your own apps' URLs next to it).
4. Create the tables and the first data:
   ```bash
   python manage.py migrate          # core.0001_initial + the default menu (post_migrate, empty menu only)
   python manage.py createsuperuser  # or DJANGO_SUPERUSER_EMAIL / DJANGO_SUPERUSER_PASSWORD in the environment + bootstrap
   python manage.py bootstrap --company-name "My Company Ltd." --company-abbreviation MC   # company, approval chains, URL permissions
   python manage.py check
   ```
5. Business apps: models inherit core's bases (`CompanyScopedModel`, …) and point user foreign keys at `settings.AUTH_USER_MODEL`.
   `makemigrations myapp` writes the dependencies for you:
   ```python
   dependencies = [("core", "0001_initial"), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
   ```
   Depend on `("core", "0001_initial")` (or on a later core migration only when you need a field it adds).

---

## 9. Upgrading

```bash
git pull                                       # or unzip the new release over the old one (keep .env, var/, private_media/)
pip install -r requirements.txt -r requirements-postgresql.txt   # or requirements-mysql.txt
python manage.py migrate
python manage.py collectstatic --noinput       # production
python manage.py seed_url_permissions
sudo systemctl restart core-web core-q         # production
```

Take a backup first (**Administration → Backups**, or `python manage.py backup_run`).

`bootstrap` after `migrate` adds new URL permissions, approval chains and menu rows. The full procedure, with rollback, is in
`docs/UPGRADING.md` §2.

---

## 10. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `RuntimeError: DJANGO_SECRET_KEY must be set` | Add the key to `.env` (section 3) or set `DJANGO_DEBUG=1` on a developer machine. |
| `connection refused` / `password authentication failed` | PostgreSQL not running, or `DB_*` values wrong. Test with `psql -h DB_HOST -U DB_USER DB_NAME`. |
| Pages have no styling with `DJANGO_DEBUG=0` | Run `python manage.py collectstatic --noinput`. |
| `Bad Request (400)` | The host name is missing from `DJANGO_ALLOWED_HOSTS`. |
| `CSRF verification failed` behind HTTPS | Set `DJANGO_CSRF_TRUSTED_ORIGINS=https://your-host` and `DJANGO_BEHIND_PROXY=1`. |
| A user sees no data / empty menus | The user has no company: edit the user and tick a company (and grant permissions or groups). |
| Large exports stay "queued" | `python manage.py qcluster` is not running (or set `Q_SYNC=1` in development). |
| Backups fail with "pg_dump not found" | Install the PostgreSQL client tools and put them on `PATH`, or set `CORE_BACKUP_TOOLS_DIR = "<their bin folder>"` in `config/settings/base.py`. |
| Dates show in the wrong format or zone | Check `CORE_DATE_FORMAT` / `CORE_DATETIME_FORMAT` / `DJANGO_TIME_ZONE` in `.env` and restart the server. |
| `UnicodeDecodeError` in tests on Windows | Set `PYTHONUTF8=1`. |
