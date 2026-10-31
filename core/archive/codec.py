"""Archive file format. Pure Python (no Django, no database), so it can be tested alone.

One batch = one gzip-compressed JSON-Lines file. Line 1 is a header ``{"table", "columns", "pk"}``; every other line is one row as a
list of typed values. Values that JSON cannot carry (datetime, Decimal, bytes, UUID ...) are tagged objects, so a restore gives
back the same Python values the database driver produced. A value of an unknown type raises ``ArchiveEncodeError`` *before* any
row is deleted from the source, so an un-archivable table fails safe.
"""
from __future__ import annotations

import base64
import datetime as dt
import decimal
import gzip
import hashlib
import json
import os
import uuid
from collections.abc import Iterable, Iterator, Sequence
from pathlib import Path


class ArchiveEncodeError(ValueError):
    pass


def encode_value(value):
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise ArchiveEncodeError("NaN/Infinity cannot be archived")
        return value
    if isinstance(value, dt.datetime):  # before date: datetime is a date subclass
        return {"$dt": value.isoformat()}
    if isinstance(value, dt.date):
        return {"$d": value.isoformat()}
    if isinstance(value, dt.time):
        return {"$t": value.isoformat()}
    if isinstance(value, dt.timedelta):
        return {"$td": [value.days, value.seconds, value.microseconds]}
    if isinstance(value, decimal.Decimal):
        return {"$dec": str(value)}
    if isinstance(value, uuid.UUID):
        return {"$uuid": str(value)}
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"$b64": base64.b64encode(bytes(value)).decode("ascii")}
    if isinstance(value, (dict, list)):
        return {"$json": json.dumps(value, sort_keys=True, separators=(",", ":"))}
    raise ArchiveEncodeError(f"Cannot archive a value of type {type(value).__name__}")


def decode_value(value):
    if not isinstance(value, dict):
        return value
    (tag, payload), = value.items()
    if tag == "$dt":
        return dt.datetime.fromisoformat(payload)
    if tag == "$d":
        return dt.date.fromisoformat(payload)
    if tag == "$t":
        return dt.time.fromisoformat(payload)
    if tag == "$td":
        return dt.timedelta(days=payload[0], seconds=payload[1], microseconds=payload[2])
    if tag == "$dec":
        return decimal.Decimal(payload)
    if tag == "$uuid":
        return uuid.UUID(payload)
    if tag == "$b64":
        return base64.b64decode(payload)
    if tag == "$json":
        return payload  # restored as JSON text; the database casts it to its JSON column type
    raise ArchiveEncodeError(f"Unknown value tag {tag!r}")


def pk_digest(pks: Iterable) -> str:
    """Order-independent SHA-256 over the primary keys of a batch (cheap proof that the same rows are in the file and were deleted)."""
    h = hashlib.sha256()
    for text in sorted(json.dumps(encode_value(p), sort_keys=True) for p in pks):
        h.update(text.encode())
        h.update(b"\n")
    return h.hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write_batch(path: Path, table: str, columns: Sequence[str], pk_column: str, rows: Sequence[Sequence]) -> dict:
    """Write rows to ``path`` atomically (tmp file, fsync, rename). Returns {rows, sha256, pk_sha256, pk_min, pk_max}."""
    pk_index = list(columns).index(pk_column)
    tmp = path.with_suffix(path.suffix + ".part")
    pks = []
    try:
        with open(tmp, "wb") as raw:
            with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as gz:
                gz.write((json.dumps({"table": table, "columns": list(columns), "pk": pk_column}) + "\n").encode())
                for row in rows:
                    pks.append(row[pk_index])
                    gz.write((json.dumps([encode_value(v) for v in row], separators=(",", ":")) + "\n").encode())
            raw.flush()
            os.fsync(raw.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()
    return {"rows": len(rows), "sha256": sha256_file(path), "pk_sha256": pk_digest(pks),
            "pk_min": str(min(pks, key=_sortable)) if pks else "", "pk_max": str(max(pks, key=_sortable)) if pks else ""}


def _sortable(v):
    return (0, v) if isinstance(v, (int, float, decimal.Decimal)) else (1, str(v))


def read_batch(path: Path) -> tuple[dict, Iterator[list]]:
    """(header, row iterator) with values decoded."""
    fh = gzip.open(path, "rt", encoding="utf-8")
    header = json.loads(fh.readline())

    def rows():
        with fh:
            for line in fh:
                yield [decode_value(v) for v in json.loads(line)]

    return header, rows()


def verify_batch(path: Path, expected_rows: int, expected_sha256: str, expected_pk_sha256: str) -> str | None:
    """None when the file matches the recorded checksum, row count and key digest; otherwise a short reason."""
    if not path.is_file():
        return "archive file is missing"
    if sha256_file(path) != expected_sha256:
        return "file checksum does not match"
    try:
        header, rows = read_batch(path)
        pk_index = header["columns"].index(header["pk"])
        pks = [r[pk_index] for r in rows]
    except Exception as exc:  # corrupt gzip / JSON
        return f"file cannot be read ({type(exc).__name__})"
    if len(pks) != expected_rows:
        return f"row count {len(pks)} != {expected_rows}"
    if pk_digest(pks) != expected_pk_sha256:
        return "key digest does not match"
    return None
