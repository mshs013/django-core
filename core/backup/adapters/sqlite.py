"""SQLite adapter (used for tests and small installs). Introspection through PRAGMA/sqlite_master; no external tools."""
from __future__ import annotations

from collections.abc import Iterable

from .base import ColumnInfo, DatabaseAdapter, ForeignKeyInfo, quote_double


class SQLiteAdapter(DatabaseAdapter):
    engine = "sqlite"
    supports_skip_locked = False

    def quote(self, identifier: str) -> str:
        return quote_double(identifier)

    def tables(self) -> list[str]:
        rows = self._fetch("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name")
        return [str(r[0]) for r in rows]

    def primary_key_columns(self, table: str) -> list[str]:
        rows = self._fetch(f"PRAGMA table_info({self.quote(table)})")
        return [str(r[1]) for r in sorted((r for r in rows if r[5]), key=lambda r: r[5])]

    def columns(self, table: str) -> list[ColumnInfo]:
        rows = self._fetch(f"PRAGMA table_info({self.quote(table)})")
        return [ColumnInfo(str(r[1]), str(r[2]), not r[3], bool(r[5])) for r in rows]

    def foreign_keys(self) -> list[ForeignKeyInfo]:
        result: list[ForeignKeyInfo] = []
        for table in self.tables():
            groups: dict[int, list[tuple]] = {}
            for row in self._fetch(f"PRAGMA foreign_key_list({self.quote(table)})"):
                groups.setdefault(int(row[0]), []).append(row)
            for ident, rows in groups.items():
                rows.sort(key=lambda r: r[1])
                result.append(ForeignKeyInfo(table=table, columns=tuple(str(r[3]) for r in rows), ref_table=str(rows[0][2]),
                                             ref_columns=tuple(str(r[4]) for r in rows), name=f"fk_{table}_{ident}"))
        return result

    def server_version(self) -> str:
        return str(self._fetch("SELECT sqlite_version()")[0][0])

    def delete_all_rows(self, tables: Iterable[str] | None = None) -> None:
        names = [t for t in (tables if tables is not None else self.tables()) if t != "django_migrations"]
        with self.connection.cursor() as cursor:
            cursor.execute("PRAGMA foreign_keys = OFF")
            try:
                for name in names:
                    cursor.execute(f"DELETE FROM {self.quote(name)}")
            finally:
                cursor.execute("PRAGMA foreign_keys = ON")

    def seconds_between(self, a: str, b: str) -> str:
        return f"((julianday({b}) - julianday({a})) * 86400.0)"

    def window_param(self, moment):
        # SQLite stores Django datetimes as 'YYYY-MM-DD HH:MM:SS[.ffffff]' text in UTC.
        base = super().window_param(moment)
        return base.strftime("%Y-%m-%d %H:%M:%S.%f")
