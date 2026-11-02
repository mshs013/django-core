# Deployment guide

> Written without being able to run the stack (see VALIDATION.md). Treat the first deployment as a rehearsal: run
> `makemigrations --check`, `check --deploy`, the test-suite and the restore drill below before go-live.

## Topology
```
Internet → Nginx (TLS, static cache, request limits) → Gunicorn (WSGI, unix socket) → Django/core → PostgreSQL
                                                         systemd: core-web, core-q (python manage.py qcluster)
```

## Why Gunicorn (WSGI), not Uvicorn (ASGI)
The app is server-rendered and synchronous (ORM, formsets, reportlab, subprocess-based backups). ASGI buys nothing without async
views, and running sync code under ASGI adds a thread-pool hop. Gunicorn is the boring, well-understood choice: pre-fork workers,
max-requests recycling, graceful reload. `config/asgi.py` is shipped so Channels push (optional, `CORE_CHANNELS_LIVE`) can be added later
with Daphne/Uvicorn **for the websocket path only**.

## Prerequisites
Python 3.12+ (3.12 and 3.13 tested), PostgreSQL 14+ client tools on the app host (`pg_dump`, `pg_restore` — match the server major),
a system user `core` with no login shell.

## Configuration (environment only)
Put the variables (listed in `docs/INSTALL.md` section 3) in `/etc/core/core.env` (mode 0600, owned by root, read by systemd `EnvironmentFile=`). Required: `DJANGO_SECRET_KEY`
(`python -c "import secrets;print(secrets.token_urlsafe(64))"`), `DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS`, `DB_*`,
`DJANGO_BEHIND_PROXY=1`. The process refuses to start without a secret key outside DEBUG/test. There are **no default credentials** in the
source; the first superuser comes from `DJANGO_SUPERUSER_EMAIL/PASSWORD` read once by `manage.py bootstrap` — remove them afterwards.

## Database
```sql
CREATE ROLE core LOGIN PASSWORD '…';  CREATE DATABASE core OWNER core ENCODING 'UTF8';
```
Prefer TLS or a unix socket between app and DB. Then: `manage.py migrate && manage.py bootstrap --company-name … --company-abbreviation …`.

## systemd (examples)
`/etc/systemd/system/core-web.service`
```ini
[Service]
User=core
WorkingDirectory=/srv/core
EnvironmentFile=/etc/core/core.env
RuntimeDirectory=core
ExecStart=/srv/core/.venv/bin/gunicorn config.wsgi:application --bind unix:/run/core/gunicorn.sock --workers 3 --max-requests 1000 --max-requests-jitter 100 --timeout 60
Restart=on-failure
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=/var/lib/core /srv/core/var
```
`/etc/systemd/system/core-q.service` — same but `ExecStart=/srv/core/.venv/bin/python manage.py qcluster`.
Schedule housekeeping (export expiry + backup retention) with a systemd timer or cron running `manage.py cleanup_exports` and
`manage.py backup_retention`.

## Nginx + HTTPS
```nginx
server {
  listen 443 ssl http2; server_name erp.example.com;
  ssl_certificate …; ssl_certificate_key …;            # e.g. certbot/Let's Encrypt; renew automatically
  client_max_body_size 12m;                            # > CORE_UPLOAD_MAX_BYTES
  location /static/ { alias /srv/core/staticfiles/; expires 7d; }
  location / { proxy_pass http://unix:/run/core/gunicorn.sock; proxy_set_header Host $host;
               proxy_set_header X-Forwarded-Proto $scheme; proxy_set_header X-Forwarded-For $remote_addr; }
}
server { listen 80; server_name erp.example.com; return 301 https://$host$request_uri; }
```
Uploads (`private_media`) and backups are **never** exposed by Nginx; only Django views serve them after an authorisation check.
`REMOTE_ADDR` is used for throttling/audit, so set the real client address at the proxy (e.g. `real_ip` module) and never trust a
client-supplied `X-Forwarded-For`.

## Health
`/health/` (liveness, touches nothing) and `/ready/` (a trivial database query; 503 on failure, details only in the log) — both public by design.

## Logging
JSON lines to stdout (journald). `core.logging` scrubs passwords, tokens, hashes and DB URLs; file contents are never logged.

## Backups
* Database: `manage.py backup_run --mode FULL` (also in the UI, permission `core.create_backup`) writes `pg_dump --format=custom` files
  to `CORE_BACKUP_DIR` with a `.json` sidecar (sha256, size, manifest). `LAST_24_HOURS` is a *partial extract*, not a restorable backup.
* Retention: `manage.py backup_retention [--dry-run]` removes files past `CORE_BACKUP_KEEP_DAYS`, always keeping `CORE_BACKUP_MIN_KEEP`.
* Ship the backup directory off-host (object storage / another machine) and encrypt it there; the app does not do this for you.
* Validate regularly: `manage.py backup_validate <id>`.

## Restore drill (do this before go-live and quarterly)
On a scratch server:
```bash
createdb core_drill
pg_restore --clean --if-exists --no-owner --no-privileges --single-transaction --exit-on-error -d core_drill /path/FULL.dump
```
Compare row counts and run the application against the restored DB. In-app restore is disabled by default
(`CORE_BACKUP_ALLOW_RESTORE=False`); enabling it requires the `core.restore_backup` permission, a passing preflight and a typed phrase.
Production restore procedure: stop `core-web` and `core-q`, take a fresh dump, restore, run `manage.py migrate`, start services.

## Upgrades and rollback
1. Take a `FULL` backup and verify it. 2. Deploy new code to a new release directory. 3. `pip install -r requirements.txt -r requirements-postgresql.txt`,
`migrate`, `bootstrap`, `collectstatic`, `check --deploy` (details: `docs/UPGRADING.md` §2). 4. Switch the symlink, restart `core-web` and `core-q`.
Rollback = switch the symlink back; if a migration was applied, restore the pre-upgrade dump (migrations are not assumed reversible).
Run `pip-audit -r requirements.txt -r requirements-postgresql.txt` on every release.

## Operations checklist
`manage.py seed_url_permissions --dry-run` after adding apps (new named URLs are denied to non-superusers until granted) ·
`manage.py sync_approval_chains` after changing chains in code · `manage.py integrity_check` · `manage.py cleanup_orphan_files --dry-run` ·
archive: `manage.py archive_run <table> --older-than-days N --preview`, then `--confirm`.
