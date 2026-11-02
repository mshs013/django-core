"""Locating native client tools (pg_dump, mysqldump, ...). Pure Python, no Django.

``BACKUP_TOOLS_DIR`` wins when set (an explicit operator choice, and the only place searched). Otherwise PATH is used. For
PostgreSQL, the newest ``/usr/lib/postgresql/<N>/bin`` that holds BOTH pg_dump and pg_restore is preferred: a dump made by
a newer pg_dump cannot be read by an older pg_restore, and Debian's /usr/bin wrappers do not always pick the same version.
"""
from __future__ import annotations

import os
import shutil
from collections.abc import Sequence
from pathlib import Path

PG_LIB_DIR = Path("/usr/lib/postgresql")


class ToolNotFound(RuntimeError):
    """A required native database tool is not installed / not in BACKUP_TOOLS_DIR."""


def _which(name: str, tools_dir: str | None) -> str | None:
    return shutil.which(name, path=tools_dir) if tools_dir else shutil.which(name)


def newest_postgres_bin(lib_dir: Path = PG_LIB_DIR, required: Sequence[str] = ("pg_dump", "pg_restore")) -> Path | None:
    if not lib_dir.is_dir():
        return None
    found = []
    for bin_dir in lib_dir.glob("*/bin"):
        if bin_dir.parent.name.isdigit() and all((bin_dir / t).is_file() and os.access(bin_dir / t, os.X_OK) for t in required):
            found.append((int(bin_dir.parent.name), bin_dir))
    return max(found)[1] if found else None


def resolve_tool(candidates: Sequence[str], tools_dir: str | None = None, prefer_postgres_lib: bool = False) -> str:
    """First candidate name that exists, as an absolute path. Raises ToolNotFound naming every candidate tried."""
    tools_dir = (tools_dir or "").strip() or None
    if prefer_postgres_lib and not tools_dir:
        newest = newest_postgres_bin()
        if newest is not None:
            for name in candidates:
                path = _which(name, str(newest))
                if path:
                    return path
    for name in candidates:
        path = _which(name, tools_dir)
        if path:
            return path
    where = f"BACKUP_TOOLS_DIR={tools_dir}" if tools_dir else "PATH"
    raise ToolNotFound(f"{' / '.join(candidates)} not found in {where}. Install the database client tools or set BACKUP_TOOLS_DIR.")
