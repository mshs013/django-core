# Backup, Archive and Export jobs

Three separate tools that all run as **background jobs** and keep a **history row** you can look at afterwards:

| Tool | What it is for | Page | Where files go | Can it delete data? |
|---|---|---|---|---|
| **Backup** | A copy of the whole database (or of a folder of files) to recover from disasters | `/backups/` | `CORE_BACKUP_DIR` | Only old backup *files* (retention) |
| **Archive** | Move **old rows** of big tables out of the live database into files, to keep it small | `/archive/` | `CORE_ARCHIVE_DIR` | **Yes: it removes the rows from the table** (after verifying the file) |
| **Export job** | A download (CSV, XLSX, PDF ...) of a list or report that was too big to build while you wait | `/exports/` | private storage, `exports/` | Only expired export files |

> Status: written from the code (`core/backup`, `core/archive`, `core/reports/resources/export.py`, `core/views/ops.py`, `core/views/exports.py`,
> `core/tasks`). Not executed here (Django and PostgreSQL were not available in the build sandbox). The restore drill in `DEPLOYMENT.md`
> and `docs/pg_restore_transcript.txt` is the check to run on a real server.

## 0. Before anything works: the background worker

Backups, archives, restores and large exports run in the **django-q2 worker**, not in the web request. If the worker is not running,
jobs stay **Queued** forever.

```bash
python manage.py qcluster        # development: own terminal.  Production: the core-q systemd service (docs/DEPLOYMENT.md)
```

Tasks are plain functions in `core/tasks/jobs.py`: `run_database_backup`, `run_file_backup`, `run_backup_restore`, `run_archive`,
`run_archive_restore`, `run_export`, and `housekeeping`. Each takes ids only, reloads the row and **claims** it (`QUEUED` → `RUNNING`
in one atomic update), so a task delivered twice cannot run twice. Progress and errors are stored on the row, so a page reload or a worker
restart never loses them. Set `Q_SYNC=1` to run tasks inline in the web process (tests / quick local trials only).

## 1. Settings (all optional; set them in `.env`, see `.env.example`)

| Setting | Default | Meaning |
|---|---|---|
| `CORE_BACKUP_DIR` | `var/backups` | Where backup files and their `.json` sidecars are written. A relative path is inside the project folder, an absolute path is used as written. Created with mode 0700. |
| `CORE_ARCHIVE_DIR` | `var/archive` | Where archive batch files are written. |
| `CORE_BACKUP_TIMEOUT_SECONDS` | 3600 | Maximum run time of `pg_dump` / `pg_restore`. Also keep `Q_TIMEOUT` (default 3600) at least this high. |
| `CORE_BACKUP_COMPRESSION` | 6 | gzip level 0-9 (0 = none). |
| `CORE_BACKUP_KEEP_DAYS` | 30 | Retention: backup files older than this are deleted. |
| `CORE_BACKUP_MIN_KEEP` | 3 | Retention never deletes the newest N successful backups of each *kind and mode*. |
| `CORE_BACKUP_TOOLS_DIR` | empty | Folder holding `pg_dump` and `pg_restore`. Empty = search `PATH` (PostgreSQL: the newest `/usr/lib/postgresql/<N>/bin` that has both). |
| `CORE_BACKUP_ALLOW_RESTORE` | `False` | Master switch for **restoring**. Off by default. |
| `CORE_BACKUP_FILE_ROOTS` | empty | Folders the **file backup** may archive. Entry is `/path` or `Label=/path`. Nothing outside this list can ever be backed up. |
| `CORE_BACKUP_FILE_EXCLUDES` | `*.pyc, __pycache__, .git, *.tmp` | Patterns skipped by the file backup. |
| `CORE_ARCHIVE_ALLOWED_TABLES` | empty | Table names that may be archived. **Empty = archiving is off.** |
| `CORE_ARCHIVE_MIN_AGE_DAYS` | 30 | Rows younger than this can never be archived. |
| `CORE_ARCHIVE_BATCH_SIZE` | 1000 | Rows per batch (max 10 000). |
| `CORE_EXPORT_SYNC_MAX_ROWS` | 5000 | Up to this many rows the download is built immediately; above it, a background export job is created. |
| `CORE_EXPORT_MAX_ROWS` | 100000 | Hard ceiling: bigger exports are refused ("narrow the filter"). |
| `CORE_EXPORT_EXPIRY_HOURS` | 24 | How long a finished export can be downloaded. |

## 2. Permissions

Every button checks its own permission. Superusers have all of them. These views check the permissions directly (they are
"self-authorizing"), so the `core.access_core_...` URL permissions from `seed_url_permissions` are not needed for them.

| Action | Permission |
|---|---|
| Open the Backups page | `core.view_backuprecord` |
| Start a backup, cancel one | `core.create_backup` |
| Download a backup file | `core.download_backup` |
| Validate a backup | `core.validate_backup` |
| Delete a backup *file* (the row stays) | `core.delete_backuprecord` |
| Restore a backup | `core.restore_backup` (**and** `CORE_BACKUP_ALLOW_RESTORE`) |
| Open the Archive page | `core.view_archivejob` |
| Start / cancel an archive | `core.run_archive` |
| Restore archived rows | `core.restore_archive` |
| Export jobs page and downloads | any signed-in user, **only their own jobs** |
| Create an export | the `view` permission of that list or report |

The default menu shows **Backups**, **Archive** and **Export jobs** to superusers only. To give an operator role access, grant the
permissions above and untick "Superuser only" on those menu items (`docs/MENU.md`).

Every start, finish, failure, download, validation and restore is written to the **audit log** (model `backup` / `archive`).

---

## 3. Backup

### 3.1 Backup types

| Mode | Contents | Restorable? |
|---|---|---|
| **Full** | structure and data | Yes |
| **Schema only** | structure, no rows | Yes (structure) |
| **Data only** | rows, no structure | Yes, into an existing structure |
| **Last 24 hours** | **partial extract**: only rows created in the last 24 h, from tables that have a usable creation timestamp; others are skipped and listed | **Never** (it is not a backup, only a report of recent data) |
| **Files** | a `.tar.gz` of one configured folder (`CORE_BACKUP_FILE_ROOTS`), e.g. the uploaded files | Yes, into a **new folder** |

Use **Full** for anything you intend to restore. Engines: PostgreSQL (needs the client tools `pg_dump`/`pg_restore`, custom
compressed format `.dump`) and SQLite (built in, `.sqlite.sql.gz`, restores only to a *copy*). Other engines are refused with a clear message.

### 3.2 Run a backup

**In the app:** Administration → Backups → choose a mode → *Create*. The row appears as **Queued**, then **Running** with a progress bar, then
**Success** or **Failed**. A running backup can be cancelled (a queued one is cancelled at once; a running one stops at its next check).

**From the shell** (runs in your terminal, not the worker; good for cron):

```bash
python manage.py backup_run --mode FULL          # SCHEMA_ONLY | DATA_ONLY | FULL | LAST_24_HOURS ; --alias default
```

### 3.3 What a finished backup contains

* The file in `CORE_BACKUP_DIR`, named like `db-postgresql-FULL-<timestamp>.dump` (names are unique and never overwrite).
* A **sidecar** `<file>.json` with size, SHA-256 checksum, engine and server version, tool version and a manifest.
* The history row: status, progress, size, checksum, validation verdict, and (for failures) a **redacted** error. Passwords are never put
  on a command line (PostgreSQL gets `PGPASSWORD` in the child environment only) and are scrubbed from stored messages and logs.
* A freshly written backup is **validated automatically** (PostgreSQL: `pg_restore --list` must read it; SQLite: the script is checked;
  files: every member is checked). A backup that fails validation is marked **Failed** and its partial file is removed.

### 3.4 Validate

*Validate* (or `python manage.py backup_validate <id>`) re-checks an existing backup: the file is inside `CORE_BACKUP_DIR`, its checksum
still matches the stored one, and the format is readable. It never restores anything. Do this regularly, and after copying backups off the server.

### 3.5 Download

*Download* (permission `core.download_backup`) streams the file as an attachment, audited. The backup folder is **never** served by Nginx;
only this view hands files out. The path is re-derived from the row and refused if it falls outside `CORE_BACKUP_DIR`.

### 3.6 Retention (clean up old files)

```bash
python manage.py backup_retention --dry-run      # show what would be deleted
python manage.py backup_retention
```

Deletes **files** (and sidecars) older than `CORE_BACKUP_KEEP_DAYS`, but always keeps the newest `CORE_BACKUP_MIN_KEEP` successful backups of
each kind and mode. The **history rows stay** (they show "File deleted (retention)"). The same cleanup is available manually per row
(*Delete file*, permission `core.delete_backuprecord`).

### 3.7 Restore

Restoring is deliberately hard to do by accident:

1. `CORE_BACKUP_ALLOW_RESTORE=1` in `.env` (default off) and the permission `core.restore_backup`.
2. Open the backup → *Restore*. A **preflight checklist** must be all green: restore enabled, backup finished and file present, not a
   partial extract, checksum still matches, same database engine, `pg_restore` installed (database) or the file source still configured (files).
3. Type the **confirmation phrase** shown on the page (`RESTORE <database name>` or `RESTORE FILES <id>`).
4. The restore runs in the worker. For a **database** restore a **Full safety backup is taken first**; if that safety backup fails, the
   restore is **not started**. PostgreSQL restores with `--clean --if-exists --single-transaction --exit-on-error` (all or nothing).
   A **file** restore extracts into a **new folder** `restores/restore-<id>` inside the backup directory; it never overwrites live files.
   A **SQLite** restore also never touches the live file: it writes a copy to `restores/` and tells you to stop the app and swap it in.

Practical rule for production: use the in-app restore for files and drills; for a real disaster, follow the manual procedure in
`DEPLOYMENT.md` ("Restore drill" and "Production restore procedure": stop `core-web` and `core-q`, take a fresh dump, restore, `migrate`, start).

### 3.8 Backup checklist for a live server

* Set `CORE_BACKUP_DIR` to a folder on a **different disk** than the database if you can.
* Schedule a nightly Full backup (section 6) and a daily `backup_retention`.
* **Copy backups off the server** (object storage or another machine) and encrypt them there. The app does not do this for you.
* Run `backup_validate` after copying, and do a **restore drill** on a scratch server before go-live and every quarter.
* The database user must be able to read everything `pg_dump` needs; the OS user running `qcluster` must own the backup folder.

---

## 4. Archive

### 4.1 What it does and when to use it

Archiving **moves old rows out of a table into compressed files** (`CORE_ARCHIVE_DIR/job<id>/<table>-000001.jsonl.gz`, one file per batch), so
that very large, mostly historical tables (logs, notifications, history) stay fast. It is **not a backup**: the archive files become the
only copy of those rows. Archive files are **never deleted automatically**; back that folder up like the database.

It is intentionally conservative and refuses unless all of these hold:

1. The table is listed in `CORE_ARCHIVE_ALLOWED_TABLES` and is not on the built-in deny list (`django_*`, `auth_*`, `guardian_*`, `core_*`,
   `sqlite_*`, `pg_*`). Core's own tables can never be archived.
2. It has a **single-column primary key** and one clear **creation-timestamp column**.
3. It is not self-referencing and has no composite foreign keys pointing at it.
4. The rows are **at least `CORE_ARCHIVE_MIN_AGE_DAYS` old**.
5. Only one active job per table.

Rows that other tables still **reference** (foreign keys) are **never moved** (counted as "blocked"): the check is made when selecting and
again inside the delete.

### 4.2 Run an archive

**In the app:** Administration → Archive → pick the table and "older than N days" → the page shows a **preview** (rows matched, rows blocked by
references, rows that can move). Tick the confirmation box → *Run*.

**From the shell:**

```bash
python manage.py archive_run <table> --older-than-days 365 --preview            # counts only, changes nothing
python manage.py archive_run <table> --older-than-days 365 [--batch-size 2000] --confirm
```

Without `--confirm` nothing is moved.

### 4.3 What happens in each batch (the safety order)

1. Select up to `batch size` movable rows.
2. **Write** them to a gzip file.
3. **Read the file back** and verify checksum, row count and a digest of the primary keys. If this fails, the file is removed and the job fails
   with **no row deleted**.
4. **Delete** those rows inside a database transaction. If the number deleted differs from the number archived, the transaction **rolls back**.
5. Mark the batch **Moved**.

Progress (matched / blocked / processed rows, % done) is stored on the job. *Cancel* stops **between batches**; rows moved so far stay
restorable. If the worker crashes mid-batch, the next run **reconciles** leftover "written" batches (rows still present: the file is dropped;
rows gone: marked moved) before continuing.

### 4.4 Restore archived rows

On a finished archive job choose *Restore* (permission `core.restore_archive`). It creates a restore job that, batch by batch (newest first):
verifies the file again, checks the table still has all the archived columns, **refuses if any row with the same primary key already exists**
(nothing is overwritten), and inserts the rows in one transaction. A failed batch is marked *Restore failed* and can be retried.
This is separate from the full-database restore in section 3.7.

### 4.5 Enabling archive for a table

```ini
# .env
CORE_ARCHIVE_ALLOWED_TABLES=garments_activitylog,garments_oldnotice
```

Use the **database table names** (`app_model`). Preview first on a copy of production.

---

## 5. Export jobs

### 5.1 How exports work

Every list page and report has an **Export** menu (CSV, XLSX, PDF and the other installed formats: TSV, ODS, JSON, YAML, HTML, XLS). An export is
exactly **what the list shows**: same search, filters and sort, same company scope, same permissions (the user needs the page's `view` permission).

* Up to `CORE_EXPORT_SYNC_MAX_ROWS` (5000) rows: the file downloads immediately.
* More than that: a **background export job** is created and you are redirected to **Exports** (`/exports/`). Download it there when its status is **Done**.
* More than `CORE_EXPORT_MAX_ROWS` (100 000) rows: refused. Narrow the filter.
* PDF exports include at most 5000 rows and use the company letterhead.

### 5.2 Statuses

| Status | Meaning |
|---|---|
| Queued | waiting for the worker |
| Running | being built |
| Done | ready to download until the expiry time |
| Failed | something went wrong (the error shows only the error type; details are in the server log `core.export`) |
| Expired | past `CORE_EXPORT_EXPIRY_HOURS`; the file is deleted, the row stays as history |

### 5.3 Security rules

* A user sees and downloads **only their own** export jobs.
* The job stores the search state, **not** rows. When the worker runs it, scope is **re-derived from the owner** (their company memberships and
  current permissions); a permission removed after the request makes the job fail.
* Files live in private storage (`exports/<random>.<ext>`), are served only by the download view, with `Cache-Control: private, no-store`.
* Image columns in reports are exported as Yes/blank, never as storage paths.
* Each export is written to the audit log.

### 5.4 Clean-up

```bash
python manage.py cleanup_exports        # delete files of expired exports, mark them Expired
```

---

## 6. Scheduling (important: nothing runs by itself)

`core` does **not** create schedules automatically. Use systemd timers or cron on the server. Example crontab for the service user
(adjust paths; load `.env` is automatic because Django reads it):

```cron
# nightly full backup at 02:00, retention at 03:00, expired exports every hour
0 2 * * *   cd /srv/core && .venv/bin/python manage.py backup_run --mode FULL
0 3 * * *   cd /srv/core && .venv/bin/python manage.py backup_retention
0 * * * *   cd /srv/core && .venv/bin/python manage.py cleanup_exports
30 3 * * 0  cd /srv/core && .venv/bin/python manage.py cleanup_orphan_files --dry-run   # weekly review
```

There is also a task function `core.tasks.jobs.housekeeping` (expires exports and applies backup retention in one call) if you prefer to
schedule it through django-q2 (`Schedule`) instead of cron. Archives are best run by hand or from your own cron with `--preview` first
(`--confirm` only after you are happy with the numbers).

## 7. Troubleshooting

| Symptom | Likely cause and fix |
|---|---|
| Job stays **Queued** | `qcluster` is not running (section 0). |
| Backup **Failed**: "pg_dump not found" | Install `postgresql-client`, or set `CORE_BACKUP_TOOLS_DIR`. |
| Backup **Failed**: version mismatch | A `pg_dump` older than the server. Install the matching client; `core` prefers the newest `/usr/lib/postgresql/<N>/bin`. |
| Backup **Failed** with a timeout | Raise `CORE_BACKUP_TIMEOUT_SECONDS` **and** `Q_TIMEOUT`. |
| *Restore* button disabled / preflight red | `CORE_BACKUP_ALLOW_RESTORE` is off, file deleted by retention, checksum changed, or it is a Last-24-hours extract. |
| "Choose one of the configured backup sources" | `CORE_BACKUP_FILE_ROOTS` is empty or the folder does not exist. |
| Source folder refused | It is (or lies inside) the backup/archive folder. A source that merely *contains* it is fine; the storage folder is excluded automatically. |
| Archive "not enabled for archiving" | Table not in `CORE_ARCHIVE_ALLOWED_TABLES`, or it is a protected `core_*` / `auth_*` table. |
| Archive "No usable creation timestamp" | The table has no single clear created-at column. Add one or archive another way. |
| Archive moves fewer rows than matched | The difference are "blocked" rows still referenced by other tables. Expected. |
| Export stays Queued / Failed | Worker not running; or the owner lost the permission; check the `core.export` log. |
| Export says "too large" | More than `CORE_EXPORT_MAX_ROWS`; filter first. |
| Download gives 404 | Export expired, file removed, or you are not the owner. |
