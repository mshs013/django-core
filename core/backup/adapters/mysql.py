"""MySQL / MariaDB adapter (one class: Django uses the same backend for both). Introspection through information_schema; dumps
by ``mariadb-dump`` or ``mysqldump``, whichever is installed, with the password in a 0600 option file (never on the command line)."""
from __future__ import annotations

from collections.abc import Iterable

from .base import DatabaseAdapter, ForeignKeyInfo
from .identifiers import validate_identifier


class MySQLAdapter(DatabaseAdapter):
    engine = "mysql"
    supports_skip_locked = True          # MySQL 8.0 / MariaDB 10.6
    schema_expression = "DATABASE()"

    def quote(self, identifier: str) -> str:
        return "`" + validate_identifier(identifier) + "`"

    def foreign_keys(self) -> list[ForeignKeyInfo]:
        rows = self._fetch(
            "SELECT constraint_name, table_name, column_name, referenced_table_name, referenced_column_name "
            "FROM information_schema.key_column_usage WHERE table_schema = DATABASE() AND referenced_table_name IS NOT NULL "
            "ORDER BY table_name, constraint_name, ordinal_position")
        grouped: dict[tuple[str, str], list[tuple[str, str, str]]] = {}
        for name, table, column, ref_table, ref_column in rows:
            grouped.setdefault((str(table), str(name)), []).append((str(column), str(ref_table), str(ref_column)))
        return [ForeignKeyInfo(table=table, columns=tuple(c[0] for c in cols), ref_table=cols[0][1],
                               ref_columns=tuple(c[2] for c in cols), name=name)
                for (table, name), cols in grouped.items()]

    def server_version(self) -> str:
        return str(self._fetch("SELECT VERSION()")[0][0])

    def delete_all_rows(self, tables: Iterable[str] | None = None) -> None:
        """TRUNCATE each table with foreign-key checks off for this session. Default: every table except django_migrations."""
        names = [t for t in (tables if tables is not None else self.tables()) if t != "django_migrations"]
        with self.connection.cursor() as cursor:
            cursor.execute("SET FOREIGN_KEY_CHECKS = 0")
            try:
                for name in names:
                    cursor.execute(f"TRUNCATE TABLE {self.quote(name)}")
            finally:
                cursor.execute("SET FOREIGN_KEY_CHECKS = 1")

    def seconds_between(self, a: str, b: str) -> str:
        return f"(TIMESTAMPDIFF(MICROSECOND, {a}, {b}) / 1000000.0)"
