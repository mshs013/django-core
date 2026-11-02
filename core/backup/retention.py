"""Which backups does retention delete? Pure function over plain rows.

Rules: only SUCCESS backups whose file still exists are candidates; per (kind, mode) group the newest ``min_keep`` are always
kept; older ones are deleted when they are older than ``keep_days``. ``keep_days <= 0`` keeps everything. FAILED/CANCELLED rows
own no file (it is removed when a job fails), and history rows are never deleted by retention, only files.
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class RetentionRow:
    id: int
    kind: str
    mode: str
    status: str
    created_at: dt.datetime
    file_deleted: bool = False


def select_expired(rows: Iterable[RetentionRow], now: dt.datetime, keep_days: int, min_keep: int) -> list[int]:
    if keep_days <= 0:
        return []
    min_keep = max(0, min_keep)
    cutoff = now - dt.timedelta(days=keep_days)
    groups: dict[tuple[str, str], list[RetentionRow]] = defaultdict(list)
    for row in rows:
        if row.status == "SUCCESS" and not row.file_deleted:
            groups[(row.kind, row.mode)].append(row)
    expired: list[int] = []
    for group in groups.values():
        group.sort(key=lambda r: (r.created_at, r.id), reverse=True)
        expired.extend(r.id for r in group[min_keep:] if r.created_at < cutoff)
    return sorted(expired)
