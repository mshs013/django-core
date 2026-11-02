"""SQLite backup/restore without external tools (stdlib ``sqlite3`` only). Pure Python: no Django imports, testable anywhere.

FULL / SCHEMA_ONLY / DATA_ONLY are written as a gzip-compressed SQL script produced by ``Connection.iterdump()`` (read-only
connection, consistent snapshot inside one transaction). Restores NEVER overwrite the live database file: the script is replayed
into a NEW file which the operator then swaps in during a maintenance window.
"""
from __future__ import annotations

import gzip
import sqlite3
from pathlib import Path

from .modes import DATA_ONLY, FULL, SCHEMA_ONLY

MAX_VALIDATE_REPLAY_BYTES = 200 * 1024 * 1024


class SqliteBackupError(RuntimeError):
    pass


def _connect_ro(db_path: Path) -> sqlite3.Connection:
    if not Path(db_path).is_file():
        raise SqliteBackupError("The SQLite database file does not exist.")
    return sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True)


def dump(db_path: Path, dest: Path, mode: str, compression_level: int = 6) -> dict:
    """Write the dump; returns ``{"tables": n, "statements": n}``."""
    if mode not in (FULL, SCHEMA_ONLY, DATA_ONLY):
        raise SqliteBackupError("Unsupported SQLite backup mode.")
    conn = _connect_ro(db_path)
    statements = tables = 0
    try:
        conn.execute("BEGIN")
        with gzip.open(dest, "wt", encoding="utf-8", compresslevel=max(1, min(9, compression_level))) as out:
            for statement in conn.iterdump():
                is_insert = statement.startswith("INSERT INTO")
                is_tx = statement in ("BEGIN TRANSACTION;", "COMMIT;")
                if mode == SCHEMA_ONLY and is_insert:
                    continue
                if mode == DATA_ONLY and not (is_insert or is_tx):
                    continue
                if statement.startswith("CREATE TABLE"):
                    tables += 1
                statements += 1
                out.write(statement + "\n")
        conn.execute("ROLLBACK")
    finally:
        conn.close()
    return {"tables": tables, "statements": statements}


def read_script(path: Path) -> str:
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            return handle.read()
    except (OSError, EOFError) as exc:
        raise SqliteBackupError(f"Not a readable gzip file: {type(exc).__name__}") from None


def validate(path: Path, mode: str = FULL) -> str:
    """Complete (ends with COMMIT), and for FULL/SCHEMA_ONLY the script replays cleanly into an in-memory database."""
    script = read_script(path)
    if not script.rstrip().endswith("COMMIT;"):
        raise SqliteBackupError("The dump is incomplete: the closing COMMIT is missing.")
    if mode == DATA_ONLY:
        return "ok: complete data-only dump"
    if len(script) > MAX_VALIDATE_REPLAY_BYTES:
        return "ok: complete dump (too large to replay for validation)"
    mem = sqlite3.connect(":memory:")
    try:
        mem.executescript(script)
        count = mem.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
    except sqlite3.Error as exc:
        raise SqliteBackupError(f"The dump does not replay: {type(exc).__name__}") from None
    finally:
        mem.close()
    return f"ok: replays cleanly ({count} tables)"


def restore_to_new_file(script_path: Path, target: Path) -> dict:
    """Replay a FULL dump into ``target`` (must not exist). Returns table row counts for verification."""
    if target.exists():
        raise SqliteBackupError("The restore target already exists; restores never overwrite files.")
    script = read_script(script_path)
    conn = sqlite3.connect(str(target))
    try:
        conn.executescript(script)
        counts = {}
        for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall():
            counts[name] = conn.execute(f'SELECT COUNT(*) FROM "{name.replace(chr(34), chr(34) * 2)}"').fetchone()[0]
        conn.commit()
    except sqlite3.Error as exc:
        conn.close()
        target.unlink(missing_ok=True)
        raise SqliteBackupError(f"Restore failed: {type(exc).__name__}") from None
    conn.close()
    return counts
