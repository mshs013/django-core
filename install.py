"""Project setup and data helper. Run it with the system Python (3.12+) from the project folder:

    python install.py            # menu
    python install.py setup      # virtual env + requirements + .env (asks for the database) + migrate + bootstrap + static files
    python install.py export     # write all data to a JSON file (dumpdata)
    python install.py import     # load a JSON file written by export (loaddata)
    python install.py check      # Django system check + database connection

Standard library only: it runs before anything is installed. Secrets typed here go into .env (or, for the first superuser, only
into the bootstrap process environment) and are never echoed or passed on a command line."""
from __future__ import annotations

import getpass
import os
import secrets
import subprocess
import sys
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
VENV = BASE / ".venv"
ENV_FILE = BASE / ".env"
PY = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
# Rebuilt by migrate or meaningless elsewhere: never exported (rows that point at them use natural keys).
EXPORT_EXCLUDE = ("contenttypes", "auth.permission", "sessions", "django_q")


# ---- small helpers --------------------------------------------------------------------------------------------------------------
def say(msg: str = "") -> None:
    print(msg, flush=True)


def ask(prompt: str, default: str = "") -> str:
    shown = f" [{default}]" if default else ""
    value = input(f"{prompt}{shown}: ").strip()
    return value or default


def ask_yes(prompt: str, default: bool = True) -> bool:
    value = ask(f"{prompt} (y/n)", "y" if default else "n").lower()
    return value in ("y", "yes")


def ask_secret(prompt: str, *, confirm: bool = False, min_len: int = 0) -> str:
    while True:
        value = getpass.getpass(f"{prompt}: ")
        if len(value) < min_len:
            say(f"  At least {min_len} characters, please.")
            continue
        if confirm and getpass.getpass("  Repeat: ") != value:
            say("  The two entries differ, try again.")
            continue
        return value


def run(args: list, *, env: dict | None = None, check: bool = True) -> int:
    say("  $ " + " ".join(str(a) for a in args))
    code = subprocess.call([str(a) for a in args], cwd=BASE, env={**os.environ, **(env or {})})
    if check and code:
        sys.exit(f"Command failed (exit {code}).")
    return code


def manage(*args: str, env: dict | None = None, check: bool = True) -> int:
    return run([PY, "manage.py", *args], env=env, check=check)


def read_env() -> dict:
    """Same rules as ``config.env.load_dotenv`` (KEY=VALUE, # comments, optional quotes)."""
    values = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            value = value.strip()
            if value[:1] in "\"'" and value[-1:] == value[:1] and len(value) >= 2:
                value = value[1:-1]
            values[key.strip().removeprefix("export ").strip()] = value
    return values


def read_text(path: Path) -> str:
    import gzip

    with gzip.open(path, "rt", encoding="utf-8") if path.suffix == ".gz" else path.open(encoding="utf-8") as fh:
        return fh.read()


def quote(value: str) -> str:
    """Single-quote anything a shell would read as syntax, so `. ./.env` works in bash too (the loaders strip the quotes)."""
    if not value or all(c.isalnum() or c in "_-.,/:@+=" for c in value):
        return value
    return f'"{value}"' if "'" in value else f"'{value}'"


# ---- setup steps ----------------------------------------------------------------------------------------------------------------
def check_python() -> None:
    if sys.version_info < (3, 12):
        sys.exit(f"Python 3.12 or newer is needed (this is {sys.version.split()[0]}).")


def make_venv() -> None:
    if PY.exists():
        say(f"Virtual environment found: {VENV}")
        return
    say(f"Creating the virtual environment in {VENV} ...")
    run([sys.executable, "-m", "venv", VENV])


def install_requirements() -> None:
    say("Installing requirements ...")
    run([PY, "-m", "pip", "install", "--upgrade", "pip"], check=False)
    run([PY, "-m", "pip", "install", "-r", "requirements.txt"])
    if (BASE / "requirements-dev.txt").exists() and ask_yes("Also install the development tools (requirements-dev.txt)?", False):
        run([PY, "-m", "pip", "install", "-r", "requirements-dev.txt"])


def pg_probe(db: dict, *, admin: dict | None = None, create: bool = False) -> str:
    """Runs inside the venv (psycopg lives there). Returns "ok", "missing" (database does not exist) or an error text.
    Credentials travel in environment variables, never on the command line."""
    code = r"""
import os, sys, psycopg
from psycopg import sql
e = os.environ
target = dict(dbname=e["P_NAME"], user=e["P_USER"], password=e["P_PASS"], host=e["P_HOST"], port=e["P_PORT"], connect_timeout=10)
if e.get("P_CREATE"):
    with psycopg.connect(dbname="postgres", user=e["P_AUSER"], password=e["P_APASS"], host=e["P_HOST"], port=e["P_PORT"],
                         autocommit=True, connect_timeout=10) as c:
        if not c.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (e["P_USER"],)).fetchone():
            c.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(sql.Identifier(e["P_USER"]), sql.Literal(e["P_PASS"])))
        if not c.execute("SELECT 1 FROM pg_database WHERE datname=%s", (e["P_NAME"],)).fetchone():
            c.execute(sql.SQL("CREATE DATABASE {} OWNER {} ENCODING 'UTF8' TEMPLATE template0").format(sql.Identifier(e["P_NAME"]), sql.Identifier(e["P_USER"])))
try:
    psycopg.connect(**target).close()
    print("ok")
except psycopg.OperationalError as exc:
    print("missing" if "does not exist" in str(exc) else str(exc).strip().splitlines()[-1])
"""
    env = {"P_NAME": db["DB_NAME"], "P_USER": db["DB_USER"], "P_PASS": db["DB_PASSWORD"], "P_HOST": db["DB_HOST"], "P_PORT": db["DB_PORT"]}
    if create and admin:
        env.update(P_CREATE="1", P_AUSER=admin["user"], P_APASS=admin["password"])
    out = subprocess.run([str(PY), "-c", code], cwd=BASE, env={**os.environ, **env}, capture_output=True, text=True)
    return (out.stdout.strip().splitlines() or [out.stderr.strip().splitlines()[-1] if out.stderr.strip() else "unknown error"])[-1]


def mysql_probe(db: dict) -> str:
    """Runs inside the venv (mysqlclient lives there). Returns "ok" or an error text. The password travels in the environment."""
    code = r"""
import os, MySQLdb
e = os.environ
try:
    MySQLdb.connect(database=e["M_NAME"], user=e["M_USER"], password=e["M_PASS"], host=e["M_HOST"], port=int(e["M_PORT"]),
                    connect_timeout=10).close()
    print("ok")
except MySQLdb.Error as exc:
    print(str(exc).strip().splitlines()[-1])
"""
    env = {"M_NAME": db["DB_NAME"], "M_USER": db["DB_USER"], "M_PASS": db["DB_PASSWORD"], "M_HOST": db["DB_HOST"], "M_PORT": db["DB_PORT"]}
    out = subprocess.run([str(PY), "-c", code], cwd=BASE, env={**os.environ, **env}, capture_output=True, text=True)
    return (out.stdout.strip().splitlines() or [out.stderr.strip().splitlines()[-1] if out.stderr.strip() else "unknown error"])[-1]


def ask_mysql(old: dict) -> dict:
    say("Installing the MySQL/MariaDB driver (requirements-mysql.txt) ...")
    if run([PY, "-m", "pip", "install", "-r", "requirements-mysql.txt"], check=False):
        say("  mysqlclient did not install. On Ubuntu first run: sudo apt install pkg-config default-libmysqlclient-dev build-essential")
    while True:
        db = {"DB_ENGINE": "mysql",
              "DB_NAME": ask("Database name", old.get("DB_NAME", "core")),
              "DB_USER": ask("Database user", old.get("DB_USER", "core")),
              "DB_HOST": ask("Host", old.get("DB_HOST", "localhost")),
              "DB_PORT": ask("Port", old.get("DB_PORT", "3306"))}
        keep = old.get("DB_PASSWORD") and ask_yes("Keep the database password already in .env?")
        db["DB_PASSWORD"] = old["DB_PASSWORD"] if keep else ask_secret("Database password")
        result = mysql_probe(db)
        if result == "ok":
            say("  Connected.")
            return db
        say(f"  Could not connect: {result}")
        say(f"  Create them as the MySQL root user, e.g.: CREATE DATABASE `{db['DB_NAME']}` CHARACTER SET utf8mb4; "
            f"CREATE USER '{db['DB_USER']}'@'localhost' IDENTIFIED BY '...'; GRANT ALL ON `{db['DB_NAME']}`.* TO '{db['DB_USER']}'@'localhost';")
        if not ask_yes("Try other settings?"):
            if ask_yes("Write these settings anyway (fix the database later)?", False):
                return db
            sys.exit("Stopped: no database.")


def ask_database(old: dict) -> dict:
    engine = ask("Database engine: postgresql, mysql (MySQL 8 / MariaDB) or sqlite", old.get("DB_ENGINE", "postgresql")).lower()
    if engine.startswith("s"):
        return {"DB_ENGINE": "sqlite", "DB_NAME": ask("SQLite file", old.get("DB_NAME", str(BASE / "db.sqlite3")))}
    if engine.startswith("m"):
        return ask_mysql(old)
    say("Installing the PostgreSQL driver (requirements-postgresql.txt) ...")
    if run([PY, "-m", "pip", "install", "-r", "requirements-postgresql.txt"], check=False):
        say("  psycopg did not install. On Ubuntu first run: sudo apt install libpq-dev python3-dev build-essential")
    while True:
        db = {"DB_ENGINE": "postgresql",
              "DB_NAME": ask("Database name", old.get("DB_NAME", "core")),
              "DB_USER": ask("Database user", old.get("DB_USER", "core")),
              "DB_HOST": ask("Host", old.get("DB_HOST", "localhost")),
              "DB_PORT": ask("Port", old.get("DB_PORT", "5432"))}
        keep = old.get("DB_PASSWORD") and ask_yes("Keep the database password already in .env?")
        db["DB_PASSWORD"] = old["DB_PASSWORD"] if keep else ask_secret("Database password")
        result = pg_probe(db)
        if result == "ok":
            say("  Connected.")
            return db
        if result == "missing" or "authentication failed" in result or "role" in result:
            say(f"  {('The database or user does not exist yet.' if result == 'missing' else result)}")
            if ask_yes("Create the user and database now with a PostgreSQL administrator account?"):
                admin = {"user": ask("  Administrator user", "postgres"), "password": ask_secret("  Administrator password")}
                result = pg_probe(db, admin=admin, create=True)
                if result == "ok":
                    say("  Created and connected.")
                    return db
        say(f"  Could not connect: {result}")
        if not ask_yes("Try other settings?"):
            if ask_yes("Write these settings anyway (fix the database later)?", False):
                return db
            sys.exit("Stopped: no database.")


def write_env() -> dict:
    old = read_env()
    if old and not ask_yes(f"{ENV_FILE.name} exists. Review and rewrite it (old copy kept as .env.bak)?"):
        say("Keeping the existing .env.")
        return old
    say("\n-- Environment (.env) --")
    debug = ask_yes("Development machine (DJANGO_DEBUG=1)?", old.get("DJANGO_DEBUG", "1") == "1")
    values = {"DJANGO_DEBUG": "1" if debug else "0",
              "DJANGO_SECRET_KEY": old.get("DJANGO_SECRET_KEY") or secrets.token_urlsafe(64)}
    if not debug:
        hosts = ask("Host names the site answers to (comma separated)", old.get("DJANGO_ALLOWED_HOSTS", ""))
        values["DJANGO_ALLOWED_HOSTS"] = hosts
        values["DJANGO_CSRF_TRUSTED_ORIGINS"] = ask("Trusted origins (https://host, comma separated)",
                                                    old.get("DJANGO_CSRF_TRUSTED_ORIGINS") or ",".join(f"https://{h.strip()}" for h in hosts.split(",") if h.strip()))
        values["DJANGO_BEHIND_PROXY"] = "1" if ask_yes("Behind Nginx / a proxy that terminates HTTPS?", True) else "0"
    say("\n-- Database --")
    values.update(ask_database(old))
    say("\n-- Site --")
    values["DJANGO_TIME_ZONE"] = ask("Time zone", old.get("DJANGO_TIME_ZONE", "Asia/Dhaka"))
    values["CORE_MENU_MAX_DEPTH"] = ask("Deepest menu level (CORE_MENU_MAX_DEPTH)", old.get("CORE_MENU_MAX_DEPTH", "6"))
    if debug:
        values["Q_SYNC"] = old.get("Q_SYNC", "1")             # background jobs run inline: no qcluster needed while developing
    known = set(values) | {"DB_PASSWORD", "DB_USER", "DB_HOST", "DB_PORT"}
    extra = {k: v for k, v in old.items() if k not in known}           # keep every other setting the file already had
    if ENV_FILE.exists():
        ENV_FILE.replace(BASE / ".env.bak")
    lines = ["# Written by install.py on " + datetime.now().strftime("%Y-%m-%d %H:%M") + ". Never commit this file.",
             *(f"{k}={quote(v)}" for k, v in values.items()), *(f"{k}={quote(v)}" for k, v in extra.items())]
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    say(f"Wrote {ENV_FILE}")
    return {**extra, **values}


def first_company_and_admin() -> None:
    say("\n-- First company and superuser (manage.py bootstrap; safe to run again) --")
    env = {"BOOTSTRAP_COMPANY_NAME": ask("Company name", "My Company"),
           "BOOTSTRAP_COMPANY_ABBREVIATION": ask("Company abbreviation", "CO")}
    if ask_yes("Create a superuser now?"):
        env["DJANGO_SUPERUSER_EMAIL"] = ask("Superuser e-mail")
        env["DJANGO_SUPERUSER_PASSWORD"] = ask_secret("Superuser password (10+ characters)", confirm=True, min_len=10)
    manage("bootstrap", "--company-name", env["BOOTSTRAP_COMPANY_NAME"], "--company-abbreviation", env["BOOTSTRAP_COMPANY_ABBREVIATION"],
           env=env)


def setup() -> None:
    check_python()
    make_venv()
    install_requirements()
    write_env()
    say("\n-- Database tables --")
    manage("migrate")
    # An export already holds companies and users: creating a first company/superuser too would clash with it.
    if ask_yes("Start from a data file exported earlier (install.py export) instead of a new first company?", False):
        import_data(fresh=True)
    else:
        first_company_and_admin()
    say("\n-- Static files --")
    manage("collectstatic", "--noinput", "-v", "0")
    manage("check")
    run_hint = f"{PY.relative_to(BASE)} manage.py runserver"
    say(f"\nDone. Start the development server with:\n  {run_hint}\nthen open http://127.0.0.1:8000/")


# ---- data import / export -------------------------------------------------------------------------------------------------------
def require_venv() -> None:
    if not PY.exists():
        sys.exit("Run `python install.py setup` first (no virtual environment yet).")


def export_data() -> None:
    require_venv()
    default = BASE / "var" / "exports" / f"data-{datetime.now():%Y%m%d-%H%M}.json"
    target = Path(ask("Export file (.json, or .json.gz to compress)", str(default)))
    target.parent.mkdir(parents=True, exist_ok=True)
    args = ["dumpdata", "--natural-foreign", "--natural-primary", "--indent", "1", "-o", str(target)]
    for label in EXPORT_EXCLUDE:
        args += ["-e", label]
    manage(*args)
    say(f"\nExported to {target}\nUploaded files are not inside it: copy the private media folder"
        f" ({read_env().get('CORE_PRIVATE_MEDIA_ROOT') or 'private_media'}) along with it, or use Administration > Backups.")


def import_data(fresh: bool = False) -> None:
    require_venv()
    source = Path(ask("Data file to import"))
    if not source.is_file():
        sys.exit(f"No such file: {source}")
    manage("migrate", "-v", "0")
    if not fresh:
        say("Rows in the file overwrite rows with the same id; other rows stay. Into a database that already has its own data,"
            " that can clash (same company name, same e-mail).")
        if ask_yes("Empty the database first (deletes ALL current data, then loads the file)?", False):
            if ask("  Type DELETE to confirm") != "DELETE":
                sys.exit("Not confirmed; nothing changed.")
            manage("flush", "--noinput")
    has_menu = '"core.menuitem"' in read_text(source)
    if has_menu:
        # the file carries its own menu: drop the default rows migrate seeded, or they would sit next to it under other ids
        manage("shell", "-c", "from core.models import MenuItem; MenuItem.objects.all().delete()")
    manage("loaddata", str(source))
    if not has_menu:
        manage("seed_default_menu")                           # an emptied database (flush) gets the default menu back
    manage("bootstrap", "-v", "0", check=False)               # re-sync approval chains and URL permissions
    say(f"\nImported {source}")


def check() -> None:
    require_venv()
    manage("check")
    manage("showmigrations", "--plan", check=False)


ACTIONS = {"setup": setup, "export": export_data, "import": import_data, "check": check}


def main() -> None:
    os.chdir(BASE)
    action = sys.argv[1] if len(sys.argv) > 1 else ""
    if action not in ACTIONS:
        say("1) Set up the project (venv, requirements, .env, database, first user)\n2) Export data\n3) Import data\n4) Check\n")
        action = {"1": "setup", "2": "export", "3": "import", "4": "check"}.get(ask("Choose", "1"), "")
        if not action:
            sys.exit("Nothing chosen.")
    try:
        ACTIONS[action]()
    except KeyboardInterrupt:
        sys.exit("\nCancelled.")


if __name__ == "__main__":
    main()
