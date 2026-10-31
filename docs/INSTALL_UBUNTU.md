# Installing with `install.py` on Ubuntu

`install.py` (project root) sets up a fresh install in one run. It uses the Python standard library only, so it runs before
anything is pip-installed. Tested shape: Ubuntu 22.04 / 24.04. For the manual steps and production serving (Gunicorn, Nginx,
django-q cluster) see `INSTALL.md` and `DEPLOYMENT.md`.

## 1. System packages

The script needs **Python 3.12 or newer** with `venv` (Django is pinned to the 5.2 LTS line). To use `core` in a
project of your own instead of this one, see `docs/INSTALL.md` section 8b.

Ubuntu 24.04 ships 3.12:

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-dev build-essential libpq-dev git
```

Ubuntu 22.04 ships 3.10, so add 3.12 from the deadsnakes PPA and call it as `python3.12` below:

```bash
sudo apt install -y software-properties-common
sudo add-apt-repository -y ppa:deadsnakes/ppa
sudo apt update
sudo apt install -y python3.12 python3.12-venv python3.12-dev build-essential libpq-dev git
```

PostgreSQL (recommended; skip if you use SQLite or a remote server):

```bash
sudo apt install -y postgresql
sudo systemctl enable --now postgresql
sudo -u postgres psql -c "ALTER USER postgres PASSWORD 'choose-a-strong-one';"
```

The last line gives the `postgres` administrator a password so `install.py` can create the app's database user and database
for you (it connects over TCP to `localhost`, which needs a password, not peer login).

## 2. Get the code

```bash
sudo mkdir -p /srv/core && sudo chown "$USER": /srv/core
git clone <your repository url> /srv/core      # or unzip the delivered archive there
cd /srv/core
```

Run every command below **from the project folder** with the **system Python** (do not activate a venv first; the script
creates `.venv` itself).

## 3. Run setup

```bash
python3 install.py setup        # Ubuntu 22.04: python3.12 install.py setup
```

Running `python3 install.py` with no argument shows a menu (1 Setup, 2 Export, 3 Import, 4 Check).

What `setup` does, in order, and what it asks:

| Step | What happens | Questions |
| --- | --- | --- |
| Python check | Stops if Python < 3.12 | — |
| Virtual env | Creates `.venv/` (reused if it exists) | — |
| Requirements | `pip install -r requirements.txt` inside `.venv`; the driver file of the chosen engine (`requirements-postgresql.txt` / `requirements-mysql.txt`) once the engine is known | Also install `requirements-dev.txt`? (default no) |
| `.env` | Writes the settings file; an existing one is saved as `.env.bak` and settings it does not ask about are kept | see below |
| Tables | `manage.py migrate` | — |
| First data | Either `manage.py bootstrap` (first company + superuser) or loading an export file | Start from an exported file? (default no) |
| Static files | `manage.py collectstatic`, then `manage.py check` | — |

### `.env` questions

* **Development machine (DJANGO_DEBUG=1)?** Answer `n` on a server. You are then asked for:
  * host names, e.g. `erp.example.com,www.erp.example.com` (`DJANGO_ALLOWED_HOSTS`)
  * trusted origins, default `https://<each host>` (`DJANGO_CSRF_TRUSTED_ORIGINS`)
  * behind Nginx / a proxy that terminates HTTPS? (`DJANGO_BEHIND_PROXY`)
* A random `DJANGO_SECRET_KEY` is generated (an existing one is kept).
* **Database engine:** `postgresql` (default) or `sqlite`.
  * PostgreSQL: name, user, host, port, password (typed hidden). The script tests the connection. If the database or user does
    not exist yet it offers to create both: give the administrator user (`postgres`) and its password from step 1.
  * SQLite: path to the file (default `db.sqlite3` in the project).
* **Time zone** (default `Asia/Dhaka`) and **deepest menu level** (default 6).
* In development `Q_SYNC=1` is set, so background jobs run inline and no `qcluster` is needed.

### First company and superuser

Company name and abbreviation, then optionally a superuser e-mail and password (10+ characters, typed twice, hidden). This runs
`manage.py bootstrap`, which is safe to run again later.

When it finishes it prints:

```
Done. Start the development server with:
  .venv/bin/python manage.py runserver
then open http://127.0.0.1:8000/
```

On a server, serve it with Gunicorn + Nginx instead (`DEPLOYMENT.md`) and start the job worker
`.venv/bin/python manage.py qcluster` (systemd unit).

## 4. Other commands

```bash
python3 install.py check     # Django system check + migration plan (needs setup done first)
python3 install.py export    # all data to var/exports/data-<date>.json  (end the name with .json.gz to compress)
python3 install.py import    # load a file written by export
```

* **Export** leaves out content types, permissions, sessions and django-q rows (rebuilt by `migrate`) and uses natural keys.
  **Uploaded files are not in it**: copy the private media folder (`CORE_PRIVATE_MEDIA_ROOT`, default `private_media`) too, or
  use Administration › Backups.
* **Import** runs `migrate`, then asks whether to empty the database first (type `DELETE` to confirm `flush`). Rows with the same
  id are overwritten, others stay. If the file contains a menu it replaces the default one; finally `bootstrap` re-syncs approval
  chains and URL permissions.

### Moving an install to a new Ubuntu server

```bash
# old server
python3 install.py export                      # e.g. var/exports/data-20261025-1000.json
tar czf media.tgz private_media                # or your CORE_PRIVATE_MEDIA_ROOT

# new server: copy both files over, then
python3 install.py setup                       # answer "y" to "Start from a data file exported earlier"
tar xzf media.tgz
```

## 5. Troubleshooting

| Problem | Fix |
| --- | --- |
| `Python 3.12 or newer is needed` | Install 3.12 (step 1) and run `python3.12 install.py setup` |
| `The virtual environment was not created successfully because ensurepip is not available` | `sudo apt install python3.12-venv` (or `python3-venv`) |
| `pg_config executable not found` / psycopg build error | `sudo apt install libpq-dev python3-dev build-essential` |
| `password authentication failed for user "postgres"` | Set the password: `sudo -u postgres psql -c "ALTER USER postgres PASSWORD '...';"` |
| `Connection refused` on port 5432 | `sudo systemctl status postgresql`; check host/port answers |
| `Run python install.py setup first` | `check` / `export` / `import` need `.venv` from setup |
| Want to change settings later | Run `setup` again and answer `y` to "Review and rewrite it" (old file kept as `.env.bak`), or edit `.env` |
| Permission denied writing `.venv` or `.env` | Run as the user who owns the project folder, not with `sudo` |

Secrets typed into the script go only into `.env` (keep it `chmod 600`, never commit it) or, for the superuser password, only
into the bootstrap process environment; they are never echoed or put on a command line.
