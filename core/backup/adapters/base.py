"""The adapter contract: value types plus the engine-neutral part of :class:`DatabaseAdapter`.

Importing this module needs no Django (Django is imported inside methods), so the dataclasses and the redaction rules are
testable with plain Python. Engines implement only what differs: quoting, schema queries, tool names, a few SQL expressions.
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from .identifiers import quote_double  # noqa: F401  (re-exported for the engine modules)
from .redact import MASK, redact
from .timestamps import KIND_DATE, KIND_DATETIME, KIND_OTHER, choose_creation_column
from .tools import ToolNotFound, resolve_tool

SUPPORTED_ENGINES = ("postgresql", "mysql", "sqlite")


class UnsupportedEngineError(RuntimeError):
    """The configured database engine is not PostgreSQL, MySQL/MariaDB or SQLite."""


@dataclass(frozen=True)
class ColumnInfo:
    name: str
    db_type: str
    nullable: bool
    primary_key: bool = False


@dataclass(frozen=True)
class ForeignKeyInfo:
    table: str
    columns: tuple[str, ...]
    ref_table: str
    ref_columns: tuple[str, ...]
    name: str = ""


@dataclass(frozen=True)
class ConnectionSettings:
    """Connection parameters for native tools. The password is never part of repr/str/format output."""

    engine: str
    host: str
    port: str
    user: str
    name: str
    password: str = field(default="", repr=False)

    def __repr__(self) -> str:
        return (f"ConnectionSettings(engine={self.engine!r}, host={self.host!r}, port={self.port!r}, "
                f"user={self.user!r}, name={self.name!r}, password={MASK if self.password else ''!r})")

    __str__ = __repr__

    @property
    def secrets(self) -> list[str]:
        return [self.password] if self.password else []

    def redact(self, text: object) -> str:
        return redact(text, self.secrets)


def kind_of_db_type(db_type: str) -> str:
    """Map a database column type name to datetime / date / other for :func:`choose_creation_column`."""
    lowered = (db_type or "").lower()
    if lowered.startswith("timestamp") or lowered == "datetime":
        return KIND_DATETIME
    if lowered == "date":
        return KIND_DATE
    return KIND_OTHER


# Tool kinds -> candidate executable names, per engine (first existing wins).
TOOL_CANDIDATES = {
    "postgresql": {"pg_dump": ("pg_dump",), "pg_restore": ("pg_restore",), "psql": ("psql",)},
    "mysql": {"mysqldump": ("mariadb-dump", "mysqldump"), "mysql": ("mariadb", "mysql")},     # MariaDB names first (newer servers)
    "sqlite": {},
}


class DatabaseAdapter:
    """Engine-specific facts and queries for one Django database alias. Subclasses: PostgreSQLAdapter, MySQLAdapter, SQLiteAdapter."""

    engine = ""
    supports_skip_locked = False
    schema_expression = ""          # SQL expression naming the current schema/database

    def __init__(self, alias: str = "default"):
        self.alias = alias

    # ---------------------------------------------------------------- plumbing
    @property
    def connection(self):
        from django.db import connections

        return connections[self.alias]

    def __repr__(self) -> str:
        return f"<{type(self).__name__} alias={self.alias!r}>"

    def quote(self, identifier: str) -> str:
        raise NotImplementedError

    def _fetch(self, sql: str, params: Sequence = ()) -> list[tuple]:
        with self.connection.cursor() as cursor:
            cursor.execute(sql, list(params))
            return list(cursor.fetchall())

    def fetch(self, sql: str, params: Sequence = ()) -> list[tuple]:
        """Run a SELECT built by trusted code (identifiers via ``quote``, values as ``params``)."""
        return self._fetch(sql, params)

    def execute(self, sql: str, params: Sequence = ()) -> int:
        """Run a data-changing statement built by trusted code; returns the affected row count."""
        with self.connection.cursor() as cursor:
            cursor.execute(sql, list(params))
            return cursor.rowcount

    # ---------------------------------------------------------------- schema introspection
    def tables(self) -> list[str]:
        rows = self._fetch(
            f"SELECT table_name FROM information_schema.tables WHERE table_schema = {self.schema_expression} "
            "AND table_type = 'BASE TABLE' ORDER BY table_name")
        return [str(r[0]) for r in rows]

    def primary_key_columns(self, table: str) -> list[str]:
        rows = self._fetch(
            "SELECT k.column_name FROM information_schema.table_constraints t "
            "JOIN information_schema.key_column_usage k ON k.constraint_name = t.constraint_name "
            "AND k.table_schema = t.table_schema AND k.table_name = t.table_name "
            f"WHERE t.table_schema = {self.schema_expression} AND t.table_name = %s AND t.constraint_type = 'PRIMARY KEY' "
            "ORDER BY k.ordinal_position", [table])
        return [str(r[0]) for r in rows]

    def columns(self, table: str) -> list[ColumnInfo]:
        pk = set(self.primary_key_columns(table))
        rows = self._fetch(
            "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
            f"WHERE table_schema = {self.schema_expression} AND table_name = %s ORDER BY ordinal_position", [table])
        return [ColumnInfo(str(n), str(t), str(nullable).upper() == "YES", str(n) in pk) for n, t, nullable in rows]

    def foreign_keys(self) -> list[ForeignKeyInfo]:
        raise NotImplementedError

    def creation_timestamp_columns(self, table: str) -> list[str]:
        """Usable "created at" columns of ``table``, best first; empty when the table has none (see timestamps.py for the rules)."""
        cols = [(c.name, kind_of_db_type(c.db_type)) for c in self.columns(table)]
        return list(choose_creation_column(cols).candidates)

    def creation_timestamp_choice(self, table: str):
        """Like creation_timestamp_columns but returns the ColumnChoice, including the reason a table has no usable column."""
        return choose_creation_column([(c.name, kind_of_db_type(c.db_type)) for c in self.columns(table)])

    # ---------------------------------------------------------------- data helpers
    def row_count(self, table: str, where: str | None = None, params: Sequence = ()) -> int:
        """COUNT(*) of ``table``. ``where`` is a trusted SQL fragment written by our code (never user text), values go in ``params``."""
        sql = f"SELECT COUNT(*) FROM {self.quote(table)}"
        if where:
            sql += f" WHERE {where}"
        return int(self._fetch(sql, params)[0][0])

    def window_param(self, moment):
        """A datetime as a query parameter comparable with creation columns.

        Django stores UTC (USE_TZ=True), and its connections run in UTC, so a naive UTC value compares correctly on every engine
        and avoids driver-specific handling of aware values.
        """
        import datetime as dt

        if moment.tzinfo is not None:
            moment = moment.astimezone(dt.timezone.utc).replace(tzinfo=None)
        return moment

    def server_version(self) -> str:
        raise NotImplementedError

    def delete_all_rows(self, tables: Iterable[str] | None = None) -> None:
        raise NotImplementedError

    def seconds_between(self, a: str, b: str) -> str:
        """SQL expression for the seconds from ``a`` to ``b`` (both SQL expressions or quoted columns)."""
        raise NotImplementedError

    # ---------------------------------------------------------------- native tools
    def connection_settings(self) -> ConnectionSettings:
        s = self.connection.settings_dict
        return ConnectionSettings(engine=self.engine, host=str(s.get("HOST") or ""), port=str(s.get("PORT") or ""),
                                  user=str(s.get("USER") or ""), name=str(s.get("NAME") or ""), password=str(s.get("PASSWORD") or ""))

    def tool_name(self, kind: str) -> str:
        """Absolute path of a native tool: ``pg_dump|pg_restore|psql`` or ``mysqldump|mysql``. Raises ToolNotFound."""
        from core import conf

        try:
            candidates = TOOL_CANDIDATES[self.engine][kind]
        except KeyError:
            raise ToolNotFound(f"'{kind}' is not a {self.engine} tool") from None
        return resolve_tool(candidates, conf.get("CORE_BACKUP_TOOLS_DIR") or None, prefer_postgres_lib=self.engine == "postgresql")

    def dump_tool_kind(self) -> str:
        return {"postgresql": "pg_dump", "mysql": "mysqldump"}[self.engine]

    def client_tool_kind(self) -> str:
        return {"postgresql": "psql", "mysql": "mysql"}[self.engine]
