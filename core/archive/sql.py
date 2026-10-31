"""SQL text for the archive. Only identifiers (validated and quoted by the adapter) are interpolated; every value is a bound
parameter. The statements use the ANSI subset that PostgreSQL and SQLite share (LIMIT, NOT EXISTS, IN lists), so there is
nothing engine-specific here -- engine differences stay in ``core.backup.adapters``."""
from __future__ import annotations

from collections.abc import Sequence


class Reference:
    """One single-column foreign key from ``child_table.child_column`` to ``<archived table>.parent_column``."""

    def __init__(self, child_table: str, child_column: str, parent_column: str):
        self.child_table, self.child_column, self.parent_column = child_table, child_column, parent_column


def not_referenced(adapter, table: str, refs: Sequence[Reference]) -> str:
    """AND-chain of NOT EXISTS clauses; empty string when nothing references the table. The outer table is addressed by its own
    name (no alias) so the same fragment is legal inside SELECT ... and DELETE ... on every engine."""
    q = adapter.quote
    return "".join(
        f" AND NOT EXISTS (SELECT 1 FROM {q(r.child_table)} WHERE {q(r.child_table)}.{q(r.child_column)} = {q(table)}.{q(r.parent_column)})"
        for r in refs)


def count_old(adapter, table: str, column: str) -> str:
    return f"SELECT COUNT(*) FROM {adapter.quote(table)} WHERE {adapter.quote(column)} < %s"


def count_movable(adapter, table: str, column: str, refs) -> str:
    return f"SELECT COUNT(*) FROM {adapter.quote(table)} WHERE {adapter.quote(column)} < %s{not_referenced(adapter, table, refs)}"


def select_batch(adapter, table: str, columns: Sequence[str], column: str, pk: str, refs, has_last: bool, limit: int) -> str:
    q = adapter.quote
    after = f" AND {q(table)}.{q(pk)} > %s" if has_last else ""
    return (f"SELECT {', '.join(q(c) for c in columns)} FROM {q(table)} WHERE {q(table)}.{q(column)} < %s{after}"
            f"{not_referenced(adapter, table, refs)} ORDER BY {q(table)}.{q(pk)} LIMIT {int(limit)}")


def delete_batch(adapter, table: str, pk: str, refs, count: int) -> str:
    """Re-checks the reference condition at delete time, so a child row inserted after the SELECT is never orphaned or cascaded."""
    q = adapter.quote
    marks = ", ".join(["%s"] * count)
    return f"DELETE FROM {q(table)} WHERE {q(table)}.{q(pk)} IN ({marks}){not_referenced(adapter, table, refs)}"


def existing_keys(adapter, table: str, pk: str, count: int) -> str:
    marks = ", ".join(["%s"] * count)
    return f"SELECT {adapter.quote(pk)} FROM {adapter.quote(table)} WHERE {adapter.quote(pk)} IN ({marks})"


def insert_row(adapter, table: str, columns: Sequence[str]) -> str:
    q = adapter.quote
    return f"INSERT INTO {q(table)} ({', '.join(q(c) for c in columns)}) VALUES ({', '.join(['%s'] * len(columns))})"
