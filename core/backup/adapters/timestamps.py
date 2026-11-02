"""Which column says "this row was created at"? Pure Python (no Django, no database) so it can be tested in isolation.

Used by the LAST_24_HOURS backup and by the archive. The rules are deliberately strict: a wrong guess would either dump far
more data than asked for or silently miss rows, so a table without an unambiguous creation timestamp is *skipped with a
reason* instead of being guessed at.

A column qualifies when
  * its name looks like a creation time (created_at, created_on, date_created, inserted_at, create_time, ...), and
  * its type is a date-time (DATETIME / TIMESTAMP / timestamptz). A bare DATE has no time of day, so it cannot bound a
    24-hour window and is rejected.
A table whose only timestamp is updated_at / modified_at is rejected: that is "last changed", not "created".
"""
from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

KIND_DATETIME = "datetime"
KIND_DATE = "date"
KIND_OTHER = "other"

# Preferred first when a table has several creation-like columns.
CREATION_PRIORITY = (
    "created_at", "created_on", "date_created", "created_date", "creation_date", "inserted_at", "insert_date",
    "create_time", "created_time", "create_date", "created", "creation_time", "creation_timestamp", "added_at",
)
_SUFFIX = r"(?:at|on|date|time|datetime|timestamp|ts|dt)"
_CREATION_RES = (
    re.compile(rf"^(?:created|creation|inserted|insert|create|added)_{_SUFFIX}$"),
    re.compile(r"^(?:date|time|datetime|timestamp)_(?:created|inserted|added|creation)$"),
    re.compile(r"^(?:created|createdat|createdon|datecreated|insertedat|creationdate|createdate)$"),
)
_MODIFIED_RES = (
    re.compile(rf"^(?:updated|modified|update|changed|last_updated|last_modified|lastupdated|lastmodified)(?:_{_SUFFIX})?$"),
    re.compile(r"^(?:date|time|datetime|timestamp)_(?:updated|modified|changed)$"),
    re.compile(r"^(?:updatedat|modifiedat|updatedon|modifiedon)$"),
)


def looks_like_creation_column(name: str) -> bool:
    lowered = name.lower()
    return any(rx.match(lowered) for rx in _CREATION_RES)


def looks_like_modified_column(name: str) -> bool:
    lowered = name.lower()
    return any(rx.match(lowered) for rx in _MODIFIED_RES)


def kind_from_field_type(field_type: str) -> str:
    """Map a Django introspection field type name (``DateTimeField``) to datetime / date / other."""
    if field_type == "DateTimeField":
        return KIND_DATETIME
    if field_type == "DateField":
        return KIND_DATE
    return KIND_OTHER


@dataclass(frozen=True)
class ColumnChoice:
    column: str | None          # the creation-timestamp column to filter on, or None
    reason: str                 # why a table was skipped ("" when a column was chosen)
    candidates: tuple[str, ...] = ()   # every qualifying column, best first


def _rank(name: str) -> tuple[int, str]:
    lowered = name.lower()
    try:
        return (CREATION_PRIORITY.index(lowered), lowered)
    except ValueError:
        return (len(CREATION_PRIORITY), lowered)


def choose_creation_column(columns: Sequence[tuple[str, str]]) -> ColumnChoice:
    """``columns`` is ``[(column_name, kind)]`` with kind from :func:`kind_from_field_type`."""
    usable = sorted((n for n, k in columns if k == KIND_DATETIME and looks_like_creation_column(n)), key=_rank)
    if usable:
        return ColumnChoice(usable[0], "", tuple(usable))
    named = [(n, k) for n, k in columns if looks_like_creation_column(n)]
    if named:
        name, kind = named[0]
        if kind == KIND_DATE:
            return ColumnChoice(None, f"column '{name}' is a DATE without a time of day; it cannot bound a 24-hour window")
        return ColumnChoice(None, f"column '{name}' looks like a creation time but is not a date-time type")
    modified = [n for n, k in columns if k == KIND_DATETIME and looks_like_modified_column(n)]
    if modified:
        return ColumnChoice(None, f"only timestamp is '{modified[0]}' (last-modified time, not creation time)")
    if any(k == KIND_DATETIME for _n, k in columns):
        return ColumnChoice(None, "has date-time columns, but none is named like a creation timestamp")
    return ColumnChoice(None, "no creation timestamp column")
