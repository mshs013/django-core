"""``get_adapter`` / ``dialect_name``: engine detection and a per-alias adapter cache (PostgreSQL, MySQL/MariaDB, SQLite)."""
from __future__ import annotations

import threading

from .base import DatabaseAdapter, UnsupportedEngineError
from .mysql import MySQLAdapter
from .postgresql import PostgreSQLAdapter
from .sqlite import SQLiteAdapter

_ADAPTERS: dict[str, type[DatabaseAdapter]] = {"postgresql": PostgreSQLAdapter, "mysql": MySQLAdapter, "sqlite": SQLiteAdapter}
_cache: dict[str, DatabaseAdapter] = {}
_lock = threading.Lock()


def classify_engine(engine_path: str) -> str:
    """Pure decision: Django ENGINE string -> dialect name, or UnsupportedEngineError."""
    lowered = (engine_path or "").lower()
    if "postgresql" in lowered or "postgis" in lowered:
        return "postgresql"
    if "sqlite" in lowered:
        return "sqlite"
    if "mysql" in lowered or "mariadb" in lowered:
        return "mysql"
    raise UnsupportedEngineError(f"Database engine {engine_path!r} is not supported. Supported: PostgreSQL, MySQL/MariaDB, SQLite.")


def dialect_name(alias: str = "default") -> str:
    from django.db import connections

    return classify_engine(connections[alias].settings_dict.get("ENGINE", ""))


def get_adapter(alias: str = "default") -> DatabaseAdapter:
    with _lock:
        adapter = _cache.get(alias)
        if adapter is None:
            adapter = _ADAPTERS[dialect_name(alias)](alias)
            _cache[alias] = adapter
        return adapter


def clear_adapter_cache() -> None:
    with _lock:
        _cache.clear()
