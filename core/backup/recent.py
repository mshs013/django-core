"""LAST_24_HOURS bundle: writer and validator. Pure Python (stdlib only).

The bundle is a ``.jsonl.tar.gz``: one ``<table>.jsonl`` per exported table (one JSON object per row) plus ``manifest.json``.
It is a PARTIAL data extract: only rows created inside the window, tables without a usable creation timestamp are
skipped and listed with a reason (NEVER dumped whole), and it is not restorable over a database.
"""
from __future__ import annotations

import base64
import datetime as dt
import decimal
import io
import json
import tarfile
import tempfile
import uuid
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from pathlib import Path

from .modes import LAST_24_HOURS, LAST_24_HOURS_NOTICE

MANIFEST_NAME = "manifest.json"
FORMAT = "partial-extract/v1"


class BundleError(Exception):
    pass


def json_default(value):
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"$base64": base64.b64encode(bytes(value)).decode("ascii")}
    if isinstance(value, (uuid.UUID, dt.timedelta)):
        return str(value)
    if isinstance(value, (set, frozenset)):
        return sorted(value, key=str)
    raise TypeError(f"Cannot serialise {type(value).__name__}")


def encode_row(columns: Sequence[str], values: Sequence) -> bytes:
    return (json.dumps(dict(zip(columns, values)), default=json_default, ensure_ascii=False) + "\n").encode("utf-8")


def build_manifest(*, engine: str, since: dt.datetime, until: dt.datetime, tables: Mapping[str, dict], skipped: Mapping[str, str]) -> dict:
    return {
        "format": FORMAT, "mode": LAST_24_HOURS, "partial": True, "notice": LAST_24_HOURS_NOTICE, "engine": engine,
        "since": since.isoformat(), "until": until.isoformat(),
        "tables": dict(tables), "skipped": dict(skipped),
        "total_rows": sum(int(t["rows"]) for t in tables.values()),
    }


def write_bundle(dest: Path, *, engine: str, since: dt.datetime, until: dt.datetime,
                 exports: Iterable[tuple[str, str, Sequence[str], Iterator[Sequence]]], skipped: Mapping[str, str],
                 compression_level: int = 6, progress: Callable[[str], None] | None = None) -> dict:
    """``exports`` yields ``(table, creation_column, column_names, row_iterator)``. Each table is spooled to a temp file (bounded memory)
    then added to the archive. Returns the manifest (also stored inside the bundle)."""
    tables: dict[str, dict] = {}
    with tarfile.open(dest, "w:gz", compresslevel=max(1, min(9, compression_level))) as tar:
        for table, column, names, rows in exports:
            with tempfile.TemporaryFile() as spool:
                count = 0
                for row in rows:
                    spool.write(encode_row(names, row))
                    count += 1
                size = spool.tell()
                spool.seek(0)
                info = tarfile.TarInfo(f"{table}.jsonl")
                info.size, info.mtime, info.mode = size, int(until.timestamp()), 0o600
                tar.addfile(info, spool)
            tables[table] = {"column": column, "rows": count}
            if progress is not None:
                progress(table)
        manifest = build_manifest(engine=engine, since=since, until=until, tables=tables, skipped=skipped)
        data = json.dumps(manifest, indent=2, ensure_ascii=False).encode("utf-8")
        info = tarfile.TarInfo(MANIFEST_NAME)
        info.size, info.mtime, info.mode = len(data), int(until.timestamp()), 0o600
        tar.addfile(info, io.BytesIO(data))
    return manifest


def read_manifest(path: Path) -> dict:
    try:
        with tarfile.open(path, "r:gz") as tar:
            handle = tar.extractfile(MANIFEST_NAME)
            if handle is None:
                raise BundleError("manifest.json missing")
            return json.loads(handle.read().decode("utf-8"))
    except BundleError:
        raise
    except (tarfile.TarError, OSError, EOFError, KeyError, ValueError) as exc:
        raise BundleError(f"Not a readable LAST_24_HOURS bundle: {type(exc).__name__}") from None


def validate_bundle(path: Path) -> str:
    """Check that the manifest is consistent with the table files (row counts) and marks the bundle partial."""
    manifest = read_manifest(path)
    if manifest.get("mode") != LAST_24_HOURS or manifest.get("partial") is not True:
        raise BundleError("Manifest does not describe a LAST_24_HOURS partial extract")
    try:
        with tarfile.open(path, "r:gz") as tar:
            names = set(tar.getnames())
            for table, info in manifest.get("tables", {}).items():
                member = f"{table}.jsonl"
                if member not in names:
                    raise BundleError(f"{member} listed in the manifest but missing")
                lines = 0
                for line in tar.extractfile(member):
                    json.loads(line)
                    lines += 1
                if lines != info.get("rows"):
                    raise BundleError(f"{table}: {lines} rows in the file, manifest says {info.get('rows')}")
    except BundleError:
        raise
    except (tarfile.TarError, OSError, EOFError, ValueError) as exc:
        raise BundleError(f"Bundle is corrupt: {type(exc).__name__}") from None
    return (f"ok: {len(manifest['tables'])} tables, {manifest.get('total_rows', 0)} rows, "
            f"{len(manifest.get('skipped', {}))} tables skipped; manifest consistent")
