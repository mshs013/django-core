"""Build the argument lists, environment and option-file text for the native database tools. Pure Python.

Nothing here runs anything. Every command is a LIST (no shell), contains only values from fixed tables or server
configuration (never user text), and keeps the password OUT of the argument list: PostgreSQL gets it via ``PGPASSWORD`` in the
child's environment, MySQL/MariaDB in a 0600 option file. SQLite needs no external tool (see sqlite.py).
"""
from __future__ import annotations

from core.backup.adapters.base import ConnectionSettings

from .modes import DATA_ONLY, FULL, NATIVE_MODES, SCHEMA_ONLY

PG_MODE_FLAGS = {FULL: [], SCHEMA_ONLY: ["--schema-only"], DATA_ONLY: ["--data-only"]}
EXTENSIONS = {"postgresql": ".dump", "mysql": ".sql.gz", "sqlite": ".sqlite.sql.gz"}
RECENT_EXTENSION = ".jsonl.tar.gz"
FILES_EXTENSION = ".tar.gz"


class CommandError(ValueError):
    pass


def check_mode(mode: str) -> None:
    if mode not in NATIVE_MODES:
        raise CommandError(f"{mode} is not a native dump mode")


def pg_connection_args(conn: ConnectionSettings) -> list[str]:
    args = ["--no-password"]
    if conn.host:
        args += ["--host", conn.host]
    if conn.port:
        args += ["--port", conn.port]
    if conn.user:
        args += ["--username", conn.user]
    return args


def pg_env(conn: ConnectionSettings) -> dict[str, str]:
    env = {"PGCONNECT_TIMEOUT": "15"}
    if conn.password:
        env["PGPASSWORD"] = conn.password
    return env


def pg_dump_argv(tool: str, conn: ConnectionSettings, mode: str, dest: str, compression_level: int) -> list[str]:
    """Custom-format dump: internally compressed, restorable (selectively) with pg_restore."""
    check_mode(mode)
    level = max(0, min(9, int(compression_level)))
    return [tool, "--format=custom", f"--compress={level}", "--file", dest, *pg_connection_args(conn), *PG_MODE_FLAGS[mode], "--dbname", conn.name]


def pg_list_argv(tool: str, path: str) -> list[str]:
    return [tool, "--list", path]


def pg_restore_argv(tool: str, conn: ConnectionSettings, mode: str, path: str) -> list[str]:
    check_mode(mode)
    flags = ["--data-only"] if mode == DATA_ONLY else ["--clean", "--if-exists"]
    return [tool, *flags, "--no-owner", "--no-privileges", "--single-transaction", "--exit-on-error",
            *pg_connection_args(conn), "--dbname", conn.name, path]


def db_extension(engine: str, mode: str) -> str:
    from .modes import LAST_24_HOURS

    if mode == LAST_24_HOURS:
        return RECENT_EXTENSION
    return EXTENSIONS[engine]


# ---------------------------------------------------------------- MySQL / MariaDB
MYSQL_MODE_FLAGS = {FULL: [], SCHEMA_ONLY: ["--no-data"], DATA_ONLY: ["--no-create-info", "--skip-triggers"]}
MYSQL_COMMON_FLAGS = ("--single-transaction", "--quick", "--routines", "--triggers", "--hex-blob", "--default-character-set=utf8mb4",
                      "--no-tablespaces")
MYSQL_DUMP_COMPLETED_FOOTER = "-- Dump completed"           # last line both dump tools write after a complete dump


def mysql_options(conn: ConnectionSettings) -> str:
    """Text of the 0600 ``--defaults-extra-file`` that carries the credentials (never placed on the command line)."""

    def quoted(value: str) -> str:
        return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'

    return (f"[client]\nuser={quoted(conn.user or 'root')}\npassword={quoted(conn.password)}\nhost={quoted(conn.host or 'localhost')}\n"
            f"port={int(conn.port or 3306)}\n")


def mysql_dump_argv(tool: str, cnf: str, conn: ConnectionSettings, mode: str) -> list[str]:
    """Plain SQL to stdout (the caller gzips it). ``--defaults-extra-file`` must be the first option."""
    check_mode(mode)
    return [tool, f"--defaults-extra-file={cnf}", *MYSQL_COMMON_FLAGS, *MYSQL_MODE_FLAGS[mode], conn.name]


def mysql_restore_argv(tool: str, cnf: str, conn: ConnectionSettings) -> list[str]:
    return [tool, f"--defaults-extra-file={cnf}", "--default-character-set=utf8mb4", conn.name]
