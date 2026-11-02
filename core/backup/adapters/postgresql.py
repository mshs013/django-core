"""PostgreSQL adapter."""
from __future__ import annotations

from collections.abc import Iterable

from .base import DatabaseAdapter, ForeignKeyInfo, quote_double


class PostgreSQLAdapter(DatabaseAdapter):
    engine = "postgresql"
    supports_skip_locked = True          # PostgreSQL >= 9.5; the project minimum is 15
    schema_expression = "current_schema()"

    def quote(self, identifier: str) -> str:
        return quote_double(identifier)

    def foreign_keys(self) -> list[ForeignKeyInfo]:
        rows = self._fetch(
            "SELECT con.conname, rel.relname, "
            "ARRAY(SELECT a.attname FROM unnest(con.conkey) WITH ORDINALITY k(attnum, ord) "
            "      JOIN pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = k.attnum ORDER BY k.ord), "
            "frel.relname, "
            "ARRAY(SELECT a.attname FROM unnest(con.confkey) WITH ORDINALITY k(attnum, ord) "
            "      JOIN pg_attribute a ON a.attrelid = con.confrelid AND a.attnum = k.attnum ORDER BY k.ord) "
            "FROM pg_constraint con "
            "JOIN pg_class rel ON rel.oid = con.conrelid "
            "JOIN pg_class frel ON frel.oid = con.confrelid "
            "JOIN pg_namespace n ON n.oid = rel.relnamespace "
            "WHERE con.contype = 'f' AND n.nspname = current_schema() ORDER BY rel.relname, con.conname")
        return [ForeignKeyInfo(table=str(t), columns=tuple(cols), ref_table=str(rt), ref_columns=tuple(rcols), name=str(name))
                for name, t, cols, rt, rcols in rows]

    def server_version(self) -> str:
        return str(self._fetch("SHOW server_version")[0][0])

    def delete_all_rows(self, tables: Iterable[str] | None = None) -> None:
        """TRUNCATE ... RESTART IDENTITY CASCADE (needs no superuser, unlike disabling triggers). Default: every table except django_migrations."""
        names = [t for t in (tables if tables is not None else self.tables()) if t != "django_migrations"]
        if not names:
            return
        with self.connection.cursor() as cursor:
            cursor.execute("TRUNCATE " + ", ".join(self.quote(t) for t in names) + " RESTART IDENTITY CASCADE")

    def seconds_between(self, a: str, b: str) -> str:
        return f"EXTRACT(EPOCH FROM (({b}) - ({a})))"
